"""Day 5: `GET /me/progress` — the cross-session learning curve.

Headline scores are computed on the fly from stored metrics/model_scores
rather than persisted in their own table: they're a *view* over data that's
already durable (session_metrics, model_scores), the weighting logic in
app/scoring/headline.py is still a heuristic likely to change before Phase 7
calibrates it, and recomputing over a user's sessions is cheap (a handful of
rows, no ML inference) — so there's no migration to run every time a weight
changes.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DBSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user
from app.core.db import get_db
from app.models.model_scores import ModelScores
from app.models.session import Session, SessionStatus
from app.models.session_metrics import SessionMetrics
from app.models.user import User
from app.schemas.progress import ProgressPoint, ProgressResponse
from app.scoring.headline import compute_headline_scores

router = APIRouter(tags=["progress"])

# Standard EWMA smoothing factor — weights the newest session at 30%, the
# accumulated history at 70%. Not tuned against data; the roadmap's own
# rationale (Phase 6) is just "a raw per-session line looks like noise".
EWMA_ALPHA = 0.3


def _ewma_series(values: list[float | None], alpha: float = EWMA_ALPHA) -> list[float | None]:
    out: list[float | None] = []
    last: float | None = None
    for v in values:
        if v is not None:
            last = v if last is None else alpha * v + (1 - alpha) * last
        out.append(last)
    return out


@router.get("/me/progress", response_model=ProgressResponse)
async def get_progress(
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    result = await db.execute(
        select(Session)
        .options(selectinload(Session.topic))
        .where(
            Session.user_id == current_user.id,
            Session.status.in_([SessionStatus.SCORED, SessionStatus.TRANSCRIBED]),
        )
        .order_by(Session.created_at.asc())
    )
    sessions = result.scalars().all()

    raw_points: list[dict] = []
    for session in sessions:
        metrics = (
            await db.execute(select(SessionMetrics).where(SessionMetrics.session_id == session.id))
        ).scalar_one_or_none()
        if metrics is None:
            continue  # metrics not computed yet — nothing to score

        scores = (
            await db.execute(select(ModelScores).where(ModelScores.session_id == session.id))
        ).scalar_one_or_none()

        headline = compute_headline_scores(
            metrics=metrics,
            pronunciation=scores.pronunciation_result if scores else None,
            relevance=scores.relevance_result if scores else None,
            argument=scores.argument_result if scores else None,
            topic_difficulty=session.topic.difficulty,
        )
        raw_points.append(
            {
                "session_id": session.id,
                "created_at": session.created_at,
                "topic_difficulty": session.topic.difficulty,
                "topic_category": session.topic.category,
                "headline": headline,
            }
        )

    ewma_fields = {
        dim: _ewma_series([p["headline"].__dict__[dim] for p in raw_points])
        for dim in ("fluency", "vocabulary", "clarity", "relevance", "argumentation", "overall")
    }

    points: list[ProgressPoint] = []
    for i, p in enumerate(raw_points):
        h = p["headline"]
        points.append(
            ProgressPoint(
                session_id=p["session_id"],
                created_at=p["created_at"],
                topic_difficulty=p["topic_difficulty"],
                topic_category=p["topic_category"],
                fluency=h.fluency,
                vocabulary=h.vocabulary,
                clarity=h.clarity,
                relevance=h.relevance,
                argumentation=h.argumentation,
                overall=h.overall,
                fluency_ewma=ewma_fields["fluency"][i],
                vocabulary_ewma=ewma_fields["vocabulary"][i],
                clarity_ewma=ewma_fields["clarity"][i],
                relevance_ewma=ewma_fields["relevance"][i],
                argumentation_ewma=ewma_fields["argumentation"][i],
                overall_ewma=ewma_fields["overall"][i],
            )
        )

    return ProgressResponse(points=points, latest=points[-1] if points else None)
