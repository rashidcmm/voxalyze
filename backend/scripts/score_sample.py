"""Day 4 DoD: run all three scorers standalone against a sample audio file,
with no DB / API / worker involved — just transcribe + score + print.

Usage (from backend/):
    .venv/Scripts/python.exe -m scripts.score_sample <audio_path> "<topic text>"

Each scorer is independently optional: if AZURE_SPEECH_KEY or
ANTHROPIC_API_KEY aren't set yet, that scorer reports status=not_configured
instead of crashing the run — see app/scoring/types.py.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.scoring.argument_quality import score_argument_quality
from app.scoring.pronunciation import score_pronunciation
from app.scoring.relevance import score_relevance
from app.scoring.types import ScorerNotConfigured
from app.transcription.local_whisper import LocalWhisperTranscriber


def _asdict(result):
    from dataclasses import asdict

    return asdict(result)


def main() -> None:
    if len(sys.argv) < 3:
        print('Usage: python -m scripts.score_sample <audio_path> "<topic text>"')
        sys.exit(1)

    audio_path = Path(sys.argv[1])
    topic_text = sys.argv[2]
    if not audio_path.exists():
        print(f"Audio file not found: {audio_path}")
        sys.exit(1)

    print(f"Transcribing {audio_path} ...")
    t0 = time.monotonic()
    transcriber = LocalWhisperTranscriber()
    transcript = transcriber.transcribe(audio_path)
    print(f"  {len(transcript.words)} words, {transcript.duration_s:.1f}s audio "
          f"({time.monotonic() - t0:.1f}s to transcribe)")

    results: dict = {}

    print("\n[1/3] Pronunciation (Azure)...")
    t0 = time.monotonic()
    try:
        pron = score_pronunciation(audio_path)
    except ScorerNotConfigured as exc:
        print(f"  skipped: {exc}")
        pron = None
    if pron is not None:
        print(f"  status={pron.status} ({time.monotonic() - t0:.1f}s)")
        results["pronunciation"] = _asdict(pron)

    print("\n[2/3] Topic relevance (local embeddings)...")
    t0 = time.monotonic()
    rel = score_relevance(transcript.full_text, transcript.words, topic_text)
    print(f"  status={rel.status}, mean_relevance={rel.mean_relevance} ({time.monotonic() - t0:.1f}s)")
    results["relevance"] = _asdict(rel)

    print("\n[3/3] Argument quality (Anthropic API)...")
    t0 = time.monotonic()
    try:
        arg = score_argument_quality(transcript.full_text, topic_text)
    except ScorerNotConfigured as exc:
        print(f"  skipped: {exc}")
        arg = None
    if arg is not None:
        print(f"  status={arg.status} ({time.monotonic() - t0:.1f}s)")
        results["argument_quality"] = _asdict(arg)

    print("\n--- Full results ---")
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
