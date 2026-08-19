"""LLM qualitative pass over a full multi-speaker room transcript — the group-
discussion analogue of app/scoring/argument_quality.py. Explicitly avoids
personality-trait labels (no "arrogant", "rude", etc.): those are subjective,
potentially biased claims about a real person, especially in front of an
interviewer. Every observation must be evidence-tied to something that
actually happened in the transcript — see the design spec's Post-session
report section.
"""
import logging

from anthropic import Anthropic
from pydantic import BaseModel, Field, ValidationError

from app.core.config import get_settings
from app.scoring.types import ScorerNotConfigured

logger = logging.getLogger("scoring.group_dynamics")

MAX_TRANSCRIPT_CHARS = 30_000

SYSTEM_PROMPT = (
    "You are analyzing a transcript of a multi-person group discussion or debate "
    "practice session. Speaker turns are labeled by participant id. Give a neutral, "
    "evidence-tied behavioral read on each participant's contribution — for example "
    "'frequently spoke over others without yielding' or 'built on other participants' "
    "points with specific references'. Every observation must cite what actually "
    "happened in the transcript. NEVER use personality-trait labels or character "
    "judgments (e.g. 'arrogant', 'rude', 'shy') — describe behavior, not character. "
    "You are also given deterministic talk-time/interruption stats; use them as "
    "context, don't just restate the numbers."
)

_client: Anthropic | None = None


class _ParticipantRead(BaseModel):
    participant_id: str
    constructiveness: int = Field(ge=1, le=5)
    clarity_of_points: int = Field(ge=1, le=5)
    observations: list[str] = Field(min_length=1, max_length=4)


class _GroupDynamicsSchema(BaseModel):
    participants: list[_ParticipantRead]
    overall_summary: str


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic()  # resolves ANTHROPIC_API_KEY from env, per SDK convention
    return _client


def score_group_dynamics(transcript: str, deterministic_stats: dict) -> dict:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise ScorerNotConfigured("ANTHROPIC_API_KEY not set — see README for how to get one")

    user_message = (
        f"Deterministic stats (talk-time %, turns, interruptions per participant):\n"
        f"{deterministic_stats}\n\n"
        f"Transcript:\n{transcript[:MAX_TRANSCRIPT_CHARS]}\n\n"
        "Give a behavioral read per participant plus a short overall summary of how "
        "the discussion went."
    )

    client = _get_client()
    last_error: Exception | None = None
    for attempt in range(2):  # one retry on schema failure, same convention as argument_quality.py
        try:
            response = client.messages.parse(
                model=settings.anthropic_model,
                max_tokens=2000,
                system=SYSTEM_PROMPT,
                output_config={"effort": "low"},
                messages=[{"role": "user", "content": user_message}],
                output_format=_GroupDynamicsSchema,
            )
            parsed = response.parsed_output
            return {
                "status": "ok",
                "participants": [p.model_dump() for p in parsed.participants],
                "overall_summary": parsed.overall_summary,
            }
        except ValidationError as exc:
            last_error = exc
            logger.warning("group dynamics schema validation failed (attempt %d): %s", attempt + 1, exc)
        except Exception as exc:  # API errors, refusals, etc.
            last_error = exc
            logger.warning("group dynamics scoring failed (attempt %d): %s", attempt + 1, exc)
            break  # don't retry non-schema failures

    return {"status": "error", "error_detail": str(last_error)[:500] if last_error else "unknown"}
