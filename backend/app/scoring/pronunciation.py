"""Azure Speech pronunciation assessment (Phase 5.1 / Day 4).

**Product + ethics call (see roadmap):** Azure's pronunciation model scores against
a native-reference accent and will systematically flag Indian-English features as
"errors." We do not surface this as "your pronunciation is wrong" or compute any
kind of accent score. We only ever call it *clarity* / *intelligibility*, we only
ever surface the specific words flagged as hardest to make out (never a per-accent
verdict), and the frontend must say explicitly that accent is not being graded.

Implementation notes:
- Uses the plain REST short-audio endpoint (`Pronunciation-Assessment` header)
  rather than the Speech SDK, so this stays a `requests` call — no extra native
  dependency to install/build on top of what's already in requirements.txt.
- That endpoint tops out around ~60s of audio per request, so a 2-5 minute
  session is decoded once (via PyAV, already a transitive dep of faster-whisper)
  and split into ~45s chunks. Per-chunk scores are aggregated as a
  duration-weighted mean; word-level issues carry an absolute session-relative
  offset (chunk_start_s + in-chunk offset).
- "Unscripted" assessment: ReferenceText="" — we're scoring free speech against
  a topic, not reading a script, so there's nothing to diff against.
- Results should be cached per session (the caller — the scoring job — only
  invokes this once per session and stores the result) to avoid burning the
  free tier's 5 hours/month re-running the same audio.
"""
import base64
import io
import json
import logging
import wave
from pathlib import Path

import av
import numpy as np
import requests

from app.core.config import get_settings
from app.scoring.types import PronunciationResult, ScorerNotConfigured, WordIssue

logger = logging.getLogger("scoring.pronunciation")

CHUNK_SECONDS = 45.0
TARGET_SAMPLE_RATE = 16000
REQUEST_TIMEOUT_S = 30
MAX_WORDS_SURFACED = 8  # worst-first, capped so the UI shows a short, actionable list


def _decode_to_mono16k_pcm(audio_path: Path) -> np.ndarray:
    """Decode any container faster-whisper/browsers produce (webm/opus, ogg,
    wav, m4a, mp3) to a single array of int16 mono samples at 16kHz — the
    format Azure's REST endpoint expects.
    """
    container = av.open(str(audio_path))
    resampler = av.AudioResampler(format="s16", layout="mono", rate=TARGET_SAMPLE_RATE)
    frames: list[np.ndarray] = []
    stream = container.streams.audio[0]
    for packet in container.demux(stream):
        for frame in packet.decode():
            for resampled in resampler.resample(frame):
                frames.append(resampled.to_ndarray().reshape(-1))
    container.close()
    if not frames:
        return np.zeros(0, dtype=np.int16)
    return np.concatenate(frames).astype(np.int16)


def _chunk_to_wav_bytes(samples: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # int16
        wf.setframerate(TARGET_SAMPLE_RATE)
        wf.writeframes(samples.tobytes())
    return buf.getvalue()


def _pronunciation_header() -> str:
    payload = {
        "ReferenceText": "",
        "GradingSystem": "HundredMark",
        "Granularity": "Word",
        "Dimension": "Comprehensive",
        "EnableMiscue": True,
    }
    return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


def _score_chunk(wav_bytes: bytes, key: str, region: str) -> dict | None:
    url = f"https://{region}.stt.speech.microsoft.com/speech/recognition/conversation/cognitiveservices/v1"
    headers = {
        "Ocp-Apim-Subscription-Key": key,
        "Content-Type": "audio/wav; codecs=audio/pcm; samplerate=16000",
        "Accept": "application/json",
        "Pronunciation-Assessment": _pronunciation_header(),
    }
    resp = requests.post(
        url,
        params={"language": "en-US", "format": "detailed"},
        headers=headers,
        data=wav_bytes,
        timeout=REQUEST_TIMEOUT_S,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("RecognitionStatus") != "Success" or not body.get("NBest"):
        # e.g. an all-silence chunk — not an error, just nothing to score.
        return None
    return body["NBest"][0]


def score_pronunciation(audio_path: Path) -> PronunciationResult:
    settings = get_settings()
    if not settings.azure_speech_key or not settings.azure_speech_region:
        raise ScorerNotConfigured(
            "AZURE_SPEECH_KEY / AZURE_SPEECH_REGION not set — see README for how to get a free F0 key"
        )

    samples = _decode_to_mono16k_pcm(audio_path)
    if samples.size == 0:
        return PronunciationResult(status="error", error_detail="audio decoded to zero samples")

    chunk_len = int(CHUNK_SECONDS * TARGET_SAMPLE_RATE)
    chunks = [samples[i : i + chunk_len] for i in range(0, samples.size, chunk_len)]

    weighted_accuracy = weighted_fluency = weighted_prosody = weighted_completeness = 0.0
    weight_accuracy = weight_fluency = weight_prosody = weight_completeness = 0.0
    all_word_issues: list[WordIssue] = []
    chunks_scored = 0

    for i, chunk in enumerate(chunks):
        chunk_start_s = (i * chunk_len) / TARGET_SAMPLE_RATE
        chunk_duration_s = chunk.size / TARGET_SAMPLE_RATE
        if chunk_duration_s < 0.3:
            continue
        try:
            nbest = _score_chunk(_chunk_to_wav_bytes(chunk), settings.azure_speech_key, settings.azure_speech_region)
        except requests.RequestException as exc:
            logger.warning("azure pronunciation chunk %d failed: %s", i, exc)
            continue
        if nbest is None:
            continue

        pa = nbest.get("PronunciationAssessment", {})
        if "AccuracyScore" in pa:
            weighted_accuracy += pa["AccuracyScore"] * chunk_duration_s
            weight_accuracy += chunk_duration_s
        if "FluencyScore" in pa:
            weighted_fluency += pa["FluencyScore"] * chunk_duration_s
            weight_fluency += chunk_duration_s
        if "ProsodyScore" in pa:  # not present on all Azure API versions
            weighted_prosody += pa["ProsodyScore"] * chunk_duration_s
            weight_prosody += chunk_duration_s
        if "CompletenessScore" in pa:
            weighted_completeness += pa["CompletenessScore"] * chunk_duration_s
            weight_completeness += chunk_duration_s

        for w in nbest.get("Words", []):
            word_pa = w.get("PronunciationAssessment", {})
            error_type = word_pa.get("ErrorType", "None")
            if error_type in ("Mispronunciation", "Omission"):
                # Offset is in 100ns ticks (Azure Speech convention).
                offset_s = chunk_start_s + w.get("Offset", 0) / 10_000_000
                all_word_issues.append(
                    WordIssue(
                        word=w.get("Word", ""),
                        offset_s=round(offset_s, 2),
                        error_type=error_type,
                        accuracy_score=word_pa.get("AccuracyScore"),
                    )
                )
        chunks_scored += 1

    if chunks_scored == 0:
        return PronunciationResult(
            status="error", error_detail="no chunk returned a successful recognition"
        )

    all_word_issues.sort(key=lambda w: (w.accuracy_score if w.accuracy_score is not None else 0))

    return PronunciationResult(
        status="ok",
        accuracy_score=round(weighted_accuracy / weight_accuracy, 1) if weight_accuracy else None,
        fluency_score=round(weighted_fluency / weight_fluency, 1) if weight_fluency else None,
        prosody_score=round(weighted_prosody / weight_prosody, 1) if weight_prosody else None,
        completeness_score=round(weighted_completeness / weight_completeness, 1)
        if weight_completeness
        else None,
        words_needing_attention=all_word_issues[:MAX_WORDS_SURFACED],
        chunks_scored=chunks_scored,
    )
