"""Orchestrates the three Day 4 scorers into one dict shaped for the
`model_scores` table — the model-layer analogue of app/metrics/pipeline.py.

Each scorer is wrapped so a failure in one (including "not configured yet")
never prevents the other two from running and being stored. This matters
concretely for this project: Azure/Anthropic keys are obtained after the code
is written (see README), so the pipeline has to degrade gracefully rather than
fail the whole scoring job the first time it runs without them.
"""
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from app.metrics.types import WordLike
from app.scoring.argument_quality import score_argument_quality
from app.scoring.pronunciation import score_pronunciation
from app.scoring.relevance import score_relevance
from app.scoring.types import ArgumentQualityResult, PronunciationResult, RelevanceResult, ScorerNotConfigured

logger = logging.getLogger("scoring.pipeline")


def _run_pronunciation(audio_path: Path) -> PronunciationResult:
    try:
        return score_pronunciation(audio_path)
    except ScorerNotConfigured as exc:
        return PronunciationResult(status="not_configured", error_detail=str(exc))
    except Exception as exc:  # noqa: BLE001 — this is a deliberate isolation boundary
        logger.exception("pronunciation scorer crashed")
        return PronunciationResult(status="error", error_detail=str(exc)[:500])


def _run_relevance(full_text: str, words: Sequence[WordLike], topic_text: str) -> RelevanceResult:
    try:
        return score_relevance(full_text, words, topic_text)
    except Exception as exc:  # noqa: BLE001
        logger.exception("relevance scorer crashed")
        return RelevanceResult(status="error", error_detail=str(exc)[:500])


def _run_argument_quality(full_text: str, topic_text: str) -> ArgumentQualityResult:
    try:
        return score_argument_quality(full_text, topic_text)
    except ScorerNotConfigured as exc:
        return ArgumentQualityResult(status="not_configured", error_detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("argument quality scorer crashed")
        return ArgumentQualityResult(status="error", error_detail=str(exc)[:500])


def _payload(result) -> dict | None:
    """Dataclass -> JSON-storable dict, dropping the status/error fields that
    already have their own columns (see model_scores.py)."""
    if result.status != "ok":
        return None
    d = asdict(result)
    d.pop("status", None)
    d.pop("error_detail", None)
    return d


def compute_all_scores(
    audio_path: Path, full_text: str, words: Sequence[WordLike], topic_text: str
) -> dict:
    pronunciation = _run_pronunciation(audio_path)
    relevance = _run_relevance(full_text, words, topic_text)
    argument = _run_argument_quality(full_text, topic_text)

    return {
        "pronunciation_status": pronunciation.status,
        "pronunciation_result": _payload(pronunciation),
        "pronunciation_error": pronunciation.error_detail,
        "relevance_status": relevance.status,
        "relevance_result": _payload(relevance),
        "relevance_error": relevance.error_detail,
        "argument_status": argument.status,
        "argument_result": _payload(argument),
        "argument_error": argument.error_detail,
    }
