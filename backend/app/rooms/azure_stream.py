"""Bridges Azure Speech's continuous streaming recognition (push-stream) into
an asyncio-friendly per-participant segment queue.

Unlike app/scoring/pronunciation.py's plain `requests` REST call, this uses
the native azure-cognitiveservices-speech SDK — Azure's real-time streaming
protocol is a proprietary WebSocket framing only the SDK owns; hand-rolling
it would be far more fragile than the one extra native dependency.
"""
import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Callable

import azure.cognitiveservices.speech as speechsdk

from app.core.config import get_settings
from app.scoring.types import ScorerNotConfigured

logger = logging.getLogger("rooms.azure_stream")

SAMPLE_RATE_HZ = 16_000


@dataclass(frozen=True)
class TranscriptSegment:
    text: str
    start_s: float
    end_s: float


def parse_recognition_result(result_json: dict) -> TranscriptSegment | None:
    """Azure's Detailed-format JSON -> (text, start_s, end_s), using the
    first/last word's offset+duration (100ns ticks) when word-level
    timestamps are enabled, falling back to the utterance-level Offset/Duration."""
    best = (result_json.get("NBest") or [{}])[0]
    text = best.get("Display") or result_json.get("DisplayText") or ""
    if not text:
        return None
    words = best.get("Words")
    if words:
        start_s = words[0]["Offset"] / 10_000_000
        last = words[-1]
        end_s = (last["Offset"] + last["Duration"]) / 10_000_000
    else:
        start_s = result_json.get("Offset", 0) / 10_000_000
        end_s = start_s + result_json.get("Duration", 0) / 10_000_000
    return TranscriptSegment(text=text, start_s=start_s, end_s=end_s)


def _default_recognizer_factory(key: str, region: str, push_stream):
    speech_config = speechsdk.SpeechConfig(subscription=key, region=region)
    speech_config.output_format = speechsdk.OutputFormat.Detailed
    speech_config.request_word_level_timestamps()
    audio_config = speechsdk.audio.AudioConfig(stream=push_stream)
    return speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)


class AzureStreamingTranscriber:
    """One instance per room participant (see app/rooms/bot.py). `segments`
    is an asyncio.Queue recognition results are pushed onto. NOTE: no caller
    drains it today — app/rooms/bot.py feeds PCM in but never reads results
    back, so no RoomTranscriptSegment row is written from the live path.
    Draining this queue and persisting it is Task 12 of
    docs/superpowers/plans/2026-08-19-gd-room-analytics-extension.md.

    The SDK's recognized callback fires on its own background thread, so it's
    bridged into the event loop via call_soon_threadsafe rather than touched
    directly from asyncio code.

    `recognizer_factory` is injectable so tests can supply a fake recognizer
    without a real Azure connection — the real PushAudioInputStream/
    AudioStreamFormat objects constructed below don't touch the network
    themselves, only starting recognition does."""

    def __init__(self, loop: asyncio.AbstractEventLoop, recognizer_factory: Callable = _default_recognizer_factory):
        settings = get_settings()
        if not settings.azure_speech_key or not settings.azure_speech_region:
            raise ScorerNotConfigured("AZURE_SPEECH_KEY / AZURE_SPEECH_REGION not set — see README")

        self.segments: asyncio.Queue[TranscriptSegment] = asyncio.Queue()
        self._loop = loop
        stream_format = speechsdk.audio.AudioStreamFormat(
            samples_per_second=SAMPLE_RATE_HZ, bits_per_sample=16, channels=1
        )
        self._push_stream = speechsdk.audio.PushAudioInputStream(stream_format=stream_format)
        self._recognizer = recognizer_factory(settings.azure_speech_key, settings.azure_speech_region, self._push_stream)
        self._recognizer.recognized.connect(self._on_recognized)
        self._recognizer.start_continuous_recognition()

    def _on_recognized(self, evt) -> None:
        if evt.result.reason != speechsdk.ResultReason.RecognizedSpeech:
            return
        try:
            result_json = json.loads(evt.result.json)
        except (json.JSONDecodeError, AttributeError) as exc:
            logger.warning("azure streaming result had no parseable json: %r", exc)
            return
        try:
            segment = parse_recognition_result(result_json)
        except (KeyError, AttributeError, TypeError) as exc:
            logger.warning("azure streaming result failed to parse: %r", exc)
            return
        if segment is not None:
            self._loop.call_soon_threadsafe(self.segments.put_nowait, segment)

    def push_pcm(self, data: bytes) -> None:
        self._push_stream.write(data)

    def close(self) -> None:
        self._push_stream.close()
        self._recognizer.stop_continuous_recognition()
