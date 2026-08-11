"""Argument quality scoring (Phase 5.3 / Day 4) — the one scorer that calls an LLM.

Scores ONLY qualitative dimensions an LLM is actually good at judging (structure,
evidence use, persuasiveness, coherence, counter-argument handling) — never asked
to count fillers or words, since the deterministic metrics engine (Day 3) already
does that far more reliably and far more cheaply.

- Strict JSON via `client.messages.parse(output_format=...)` — the response is
  validated against a Pydantic model server-side-shaped and client-validated;
  no free-text parsing, no regex.
- `claude-opus-5` doesn't accept `temperature` (400) or a fixed thinking budget,
  so "temperature=0" from the original roadmap is approximated with
  `effort: "low"` (tighter, more deterministic generation) instead — see the
  claude-api skill's migration notes on newer-model sampling params.
- Retries exactly once on a schema/validation failure (`.parse()` raising),
  per the roadmap's "retry once on schema failure".
"""
import logging

from anthropic import Anthropic
from pydantic import BaseModel, Field, ValidationError

from app.core.config import get_settings
from app.scoring.types import ArgumentQualityResult, ScorerNotConfigured

logger = logging.getLogger("scoring.argument_quality")

MAX_TRANSCRIPT_CHARS = 20_000  # generous for a 2-5 min speech; guards against runaway cost

SYSTEM_PROMPT = (
    "You are scoring a spoken group-discussion/debate practice transcript for argument "
    "quality only. Do not comment on filler words, pace, or word counts — those are "
    "scored separately by a deterministic system. Score strictly against the topic given. "
    "Be honest and specific: a rambling or off-topic transcript should score low."
)

_client: Anthropic | None = None


class _ArgumentQualitySchema(BaseModel):
    """Server-validated shape for the LLM's response. 1-5 integer scale per
    dimension (matches the roadmap's headline-scoring inputs for Day 5)."""

    argument_structure: int = Field(ge=1, le=5)
    evidence_use: int = Field(ge=1, le=5)
    persuasiveness: int = Field(ge=1, le=5)
    coherence: int = Field(ge=1, le=5)
    counter_argument_handling: int = Field(ge=1, le=5)
    improvement_points: list[str] = Field(min_length=1, max_length=5)
    rationale: str


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic()  # resolves ANTHROPIC_API_KEY from env, per SDK convention
    return _client


def score_argument_quality(full_text: str, topic_text: str) -> ArgumentQualityResult:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise ScorerNotConfigured("ANTHROPIC_API_KEY not set — see README for how to get one")

    transcript = full_text[:MAX_TRANSCRIPT_CHARS]
    user_message = (
        f"Topic: {topic_text}\n\n"
        f"Transcript:\n{transcript}\n\n"
        "Score this transcript's argument quality (1-5 per dimension) and give 1-5 "
        "concrete, specific improvement points."
    )

    client = _get_client()
    last_error: Exception | None = None
    for attempt in range(2):  # one retry on schema failure, per roadmap
        try:
            response = client.messages.parse(
                model=settings.anthropic_model,
                max_tokens=1500,
                system=SYSTEM_PROMPT,
                output_config={"effort": "low"},  # tight, near-deterministic — see module docstring
                messages=[{"role": "user", "content": user_message}],
                output_format=_ArgumentQualitySchema,
            )
            parsed = response.parsed_output
            return ArgumentQualityResult(
                status="ok",
                argument_structure=parsed.argument_structure,
                evidence_use=parsed.evidence_use,
                persuasiveness=parsed.persuasiveness,
                coherence=parsed.coherence,
                counter_argument_handling=parsed.counter_argument_handling,
                improvement_points=parsed.improvement_points,
                rationale=parsed.rationale,
            )
        except ValidationError as exc:
            last_error = exc
            logger.warning("argument quality schema validation failed (attempt %d): %s", attempt + 1, exc)
        except Exception as exc:  # API errors, refusals, etc.
            last_error = exc
            logger.warning("argument quality scoring failed (attempt %d): %s", attempt + 1, exc)
            break  # don't retry non-schema failures (rate limit, auth, refusal)

    return ArgumentQualityResult(status="error", error_detail=str(last_error)[:500] if last_error else "unknown")
