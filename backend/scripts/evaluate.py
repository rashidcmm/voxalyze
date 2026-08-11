"""Day 5 mini evaluation harness (roadmap Phase 7).

Two things this script does, matching the roadmap's DoD:

1. **Correlation**: given a CSV of human ratings (0-100 per dimension, one row
   per rater per session) and a list of already-scored session IDs, computes
   the Spearman correlation between the human mean and the pipeline's
   headline score, per dimension — using the *exact same*
   `compute_headline_scores` function the live app uses, not a re-derivation.
2. **LLM variance**: re-runs the argument-quality scorer 3x on one session's
   transcript and reports per-dimension variance, per the roadmap's "measure
   score variance" ask.

Usage (from backend/):
    .venv/Scripts/python.exe -m scripts.evaluate correlate ratings.csv
    .venv/Scripts/python.exe -m scripts.evaluate llm-variance <session_id> [--runs 3]

`ratings.csv` columns: session_id,rater,fluency,vocabulary,clarity,relevance,argumentation
(0-100 each; see EVALUATION.md for the rating rubric raters should use).

This script needs real recordings run through the full pipeline first — it
cannot fabricate evaluation data. See EVALUATION.md for how to collect it.
"""
import asyncio
import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.db import async_session_maker
from app.models.model_scores import ModelScores
from app.models.session import Session
from app.models.session_metrics import SessionMetrics
from app.models.transcript import Transcript
from app.scoring.argument_quality import score_argument_quality
from app.scoring.headline import compute_headline_scores

DIMENSIONS = ["fluency", "vocabulary", "clarity", "relevance", "argumentation"]


def _read_ratings(csv_path: Path) -> dict[str, dict[str, list[float]]]:
    ratings: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            session_id = row["session_id"].strip()
            for dim in DIMENSIONS:
                if row.get(dim, "").strip():
                    ratings[session_id][dim].append(float(row[dim]))
    return ratings


async def _pipeline_headline(db, session_id: str):
    session = (
        await db.execute(
            select(Session).options(selectinload(Session.topic)).where(Session.id == session_id)
        )
    ).scalar_one_or_none()
    if session is None:
        return None
    metrics = (
        await db.execute(select(SessionMetrics).where(SessionMetrics.session_id == session_id))
    ).scalar_one_or_none()
    if metrics is None:
        return None
    scores = (
        await db.execute(select(ModelScores).where(ModelScores.session_id == session_id))
    ).scalar_one_or_none()
    return compute_headline_scores(
        metrics=metrics,
        pronunciation=scores.pronunciation_result if scores else None,
        relevance=scores.relevance_result if scores else None,
        argument=scores.argument_result if scores else None,
        topic_difficulty=session.topic.difficulty,
    )


def _spearman(xs: list[float], ys: list[float]) -> tuple[float, float] | None:
    if len(xs) < 2:
        return None
    from scipy.stats import spearmanr

    rho, p = spearmanr(xs, ys)
    return float(rho), float(p)


async def correlate(csv_path: Path) -> None:
    ratings = _read_ratings(csv_path)
    print(f"Loaded ratings for {len(ratings)} session(s) from {csv_path}\n")

    per_dim: dict[str, list[tuple[float, float]]] = {d: [] for d in DIMENSIONS}
    skipped: dict[str, int] = defaultdict(int)

    async with async_session_maker() as db:
        for session_id, dims in ratings.items():
            headline = await _pipeline_headline(db, session_id)
            if headline is None:
                print(f"  session {session_id}: no metrics/scores found — skipping")
                continue
            for dim in DIMENSIONS:
                if dim not in dims:
                    continue
                human_mean = statistics.mean(dims[dim])
                pipeline_value = getattr(headline, dim)
                if pipeline_value is None:
                    skipped[dim] += 1
                    continue
                per_dim[dim].append((human_mean, pipeline_value))

    print("| dimension | n | spearman rho | p-value |")
    print("|---|---|---|---|")
    for dim in DIMENSIONS:
        pairs = per_dim[dim]
        result = _spearman([p[0] for p in pairs], [p[1] for p in pairs])
        if result is None:
            note = f" ({skipped[dim]} skipped: not configured)" if skipped[dim] else ""
            print(f"| {dim} | {len(pairs)} | n/a — need >=2 sessions{note} | — |")
        else:
            rho, p = result
            note = f" ({skipped[dim]} skipped: not configured)" if skipped[dim] else ""
            print(f"| {dim} | {len(pairs)}{note} | {rho:.2f} | {p:.3f} |")

    n_sessions = len(ratings)
    if n_sessions < 6:
        print(
            f"\n**Caveat**: only {n_sessions} session(s) rated — the roadmap's target is "
            "6-8. Treat these numbers as directional, not conclusive, until more are collected."
        )


async def llm_variance(session_id: str, runs: int) -> None:
    async with async_session_maker() as db:
        session = (
            await db.execute(
                select(Session).options(selectinload(Session.topic)).where(Session.id == session_id)
            )
        ).scalar_one_or_none()
        if session is None:
            print(f"session {session_id} not found")
            return
        transcript = (
            await db.execute(select(Transcript).where(Transcript.session_id == session_id))
        ).scalar_one_or_none()
        if transcript is None:
            print(f"session {session_id} has no transcript")
            return
        topic_text = session.topic.text
        full_text = transcript.full_text

    print(f"Running argument-quality scorer {runs}x on session {session_id}...\n")
    results = []
    for i in range(runs):
        result = score_argument_quality(full_text, topic_text)
        print(f"  run {i + 1}: status={result.status} {result.__dict__}")
        if result.status == "ok":
            results.append(result)

    if len(results) < 2:
        print("\nNeed at least 2 successful runs to compute variance.")
        return

    dims = [
        "argument_structure",
        "evidence_use",
        "persuasiveness",
        "coherence",
        "counter_argument_handling",
    ]
    print("\n| dimension | values | variance |")
    print("|---|---|---|")
    for dim in dims:
        values = [getattr(r, dim) for r in results]
        print(f"| {dim} | {values} | {statistics.pvariance(values):.3f} |")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "correlate":
        if len(sys.argv) < 3:
            print("Usage: python -m scripts.evaluate correlate <ratings.csv>")
            sys.exit(1)
        asyncio.run(correlate(Path(sys.argv[2])))
    elif cmd == "llm-variance":
        if len(sys.argv) < 3:
            print("Usage: python -m scripts.evaluate llm-variance <session_id> [--runs N]")
            sys.exit(1)
        runs = 3
        if "--runs" in sys.argv:
            runs = int(sys.argv[sys.argv.index("--runs") + 1])
        asyncio.run(llm_variance(sys.argv[2], runs))
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
