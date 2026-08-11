"""Topic relevance scoring (Phase 5.2 / Day 4) — local embeddings, no API key.

Splits the transcript into utterances at the same pause boundary the fluency
metrics already use (`NATURAL_PAUSE_S`, a >=0.5s gap between words), embeds
each utterance plus the topic statement with all-MiniLM-L6-v2, and reports
per-utterance cosine similarity. `mean_relevance` is the headline number;
`drift_curve` (similarity over time) is the "where did they wander off-topic"
visual the roadmap calls out as the best screenshot for a resume.

Model loads lazily and is cached at module scope for the worker process's
lifetime — same load-once-use-many pattern as LocalWhisperTranscriber. First
call downloads ~90MB from Hugging Face; needs internet once, same as Whisper.
"""
import logging
from typing import Sequence

import numpy as np

from app.metrics.fluency import NATURAL_PAUSE_S
from app.metrics.types import WordLike
from app.scoring.types import RelevanceResult, RelevanceUtterance

logger = logging.getLogger("scoring.relevance")

EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"

_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        logger.info("loading sentence-transformers model %s", EMBEDDING_MODEL_NAME)
        _model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _model


def split_into_utterances(
    words: Sequence[WordLike], gap_threshold: float = NATURAL_PAUSE_S
) -> list[tuple[str, float, float]]:
    """Group consecutive words into utterances, breaking on any inter-word gap
    >= gap_threshold. Returns (text, start_s, end_s) tuples.
    """
    if not words:
        return []

    utterances: list[tuple[str, float, float]] = []
    current: list[str] = [words[0].word]
    start_s = words[0].start_s
    end_s = words[0].end_s

    for prev, nxt in zip(words, words[1:]):
        gap = nxt.start_s - prev.end_s
        if gap >= gap_threshold:
            utterances.append((" ".join(current).strip(), start_s, end_s))
            current = [nxt.word]
            start_s = nxt.start_s
        else:
            current.append(nxt.word)
        end_s = nxt.end_s

    utterances.append((" ".join(current).strip(), start_s, end_s))
    # Drop empty/whitespace-only utterances (can happen with stray tokens).
    return [u for u in utterances if u[0]]


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def score_relevance(full_text: str, words: Sequence[WordLike], topic_text: str) -> RelevanceResult:
    utterances = split_into_utterances(words)
    if not utterances:
        return RelevanceResult(status="error", error_detail="no utterances to score")

    try:
        model = _get_model()
        texts = [u[0] for u in utterances] + [topic_text]
        embeddings = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
    except Exception as exc:  # model download/load failure, OOM, etc.
        logger.exception("relevance scoring failed")
        return RelevanceResult(status="error", error_detail=str(exc)[:500])

    topic_embedding = embeddings[-1]
    utterance_embeddings = embeddings[:-1]

    drift_curve: list[RelevanceUtterance] = []
    similarities: list[float] = []
    for (text, start_s, end_s), emb in zip(utterances, utterance_embeddings):
        sim = max(0.0, min(1.0, _cosine_similarity(emb, topic_embedding)))
        similarities.append(sim)
        drift_curve.append(
            RelevanceUtterance(text=text, start_s=round(start_s, 2), end_s=round(end_s, 2), similarity=round(sim, 3))
        )

    return RelevanceResult(
        status="ok",
        mean_relevance=round(sum(similarities) / len(similarities), 3),
        drift_curve=drift_curve,
    )
