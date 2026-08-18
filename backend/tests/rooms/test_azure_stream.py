import asyncio
import json

import azure.cognitiveservices.speech as speechsdk
import pytest

from app.core.config import get_settings
from app.rooms.azure_stream import AzureStreamingTranscriber, parse_recognition_result
from app.scoring.types import ScorerNotConfigured


def test_parse_recognition_result_uses_word_level_timestamps():
    result_json = {
        "NBest": [{
            "Display": "hello world",
            "Words": [
                {"Word": "hello", "Offset": 10_000_000, "Duration": 5_000_000},
                {"Word": "world", "Offset": 16_000_000, "Duration": 5_000_000},
            ],
        }]
    }
    segment = parse_recognition_result(result_json)
    assert segment.text == "hello world"
    assert segment.start_s == pytest.approx(1.0)
    assert segment.end_s == pytest.approx(2.1)


def test_parse_recognition_result_returns_none_for_empty_text():
    assert parse_recognition_result({"NBest": [{"Display": ""}]}) is None


def test_parse_recognition_result_falls_back_to_utterance_offset_without_words():
    result_json = {"NBest": [{"Display": "hi"}], "Offset": 20_000_000, "Duration": 5_000_000}
    segment = parse_recognition_result(result_json)
    assert segment.start_s == pytest.approx(2.0)
    assert segment.end_s == pytest.approx(2.5)


class _FakeResult:
    def __init__(self, reason, result_json):
        self.reason = reason
        self.json = json.dumps(result_json)


class _FakeEvent:
    def __init__(self, result):
        self.result = result


class _FakeRecognizer:
    """Stands in for speechsdk.SpeechRecognizer: captures the connected
    handler so the test can fire a fake recognition event directly, instead
    of needing a real Azure connection."""

    def __init__(self):
        self._handler = None
        self.started = False
        self.stopped = False

    @property
    def recognized(self):
        return self

    def connect(self, handler):
        self._handler = handler

    def start_continuous_recognition(self):
        self.started = True

    def stop_continuous_recognition(self):
        self.stopped = True

    def fire(self, reason, result_json):
        self._handler(_FakeEvent(_FakeResult(reason, result_json)))


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    yield
    get_settings.cache_clear()


def test_transcriber_raises_when_not_configured(monkeypatch):
    monkeypatch.setenv("AZURE_SPEECH_KEY", "")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "")
    get_settings.cache_clear()
    with pytest.raises(ScorerNotConfigured):
        AzureStreamingTranscriber(asyncio.get_event_loop())


async def test_transcriber_queues_a_recognized_segment(monkeypatch):
    monkeypatch.setenv("AZURE_SPEECH_KEY", "test-key")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "test-region")
    get_settings.cache_clear()

    fake_recognizer = _FakeRecognizer()
    loop = asyncio.get_event_loop()
    transcriber = AzureStreamingTranscriber(
        loop, recognizer_factory=lambda key, region, push_stream: fake_recognizer
    )
    assert fake_recognizer.started is True

    fake_recognizer.fire(
        speechsdk.ResultReason.RecognizedSpeech,
        {"NBest": [{"Display": "testing one two", "Words": [
            {"Word": "testing", "Offset": 0, "Duration": 5_000_000},
            {"Word": "two", "Offset": 10_000_000, "Duration": 3_000_000},
        ]}]},
    )
    segment = await asyncio.wait_for(transcriber.segments.get(), timeout=1.0)
    assert segment.text == "testing one two"

    transcriber.close()
    assert fake_recognizer.stopped is True


async def test_transcriber_logs_and_drops_result_with_word_missing_duration(monkeypatch):
    monkeypatch.setenv("AZURE_SPEECH_KEY", "test-key")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "test-region")
    get_settings.cache_clear()

    fake_recognizer = _FakeRecognizer()
    loop = asyncio.get_event_loop()
    transcriber = AzureStreamingTranscriber(
        loop, recognizer_factory=lambda key, region, push_stream: fake_recognizer
    )

    # No "Duration" key on the word entry -> parse_recognition_result raises
    # KeyError internally; _on_recognized must catch it, log it, and simply
    # drop the segment rather than letting the exception escape onto the
    # SDK's background callback thread.
    fake_recognizer.fire(
        speechsdk.ResultReason.RecognizedSpeech,
        {"NBest": [{"Display": "broken", "Words": [{"Word": "broken", "Offset": 0}]}]},
    )
    assert transcriber.segments.empty()

    transcriber.close()


async def test_transcriber_logs_and_drops_non_dict_json_result(monkeypatch):
    monkeypatch.setenv("AZURE_SPEECH_KEY", "test-key")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "test-region")
    get_settings.cache_clear()

    fake_recognizer = _FakeRecognizer()
    loop = asyncio.get_event_loop()
    transcriber = AzureStreamingTranscriber(
        loop, recognizer_factory=lambda key, region, push_stream: fake_recognizer
    )

    # The JSON decodes fine but to a list, not a dict -> result_json.get(...)
    # in parse_recognition_result raises AttributeError; must be caught,
    # logged, and dropped without propagating.
    fake_recognizer.fire(speechsdk.ResultReason.RecognizedSpeech, [])
    assert transcriber.segments.empty()

    transcriber.close()
