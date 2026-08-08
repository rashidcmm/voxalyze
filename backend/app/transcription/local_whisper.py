import logging
from pathlib import Path

from faster_whisper import WhisperModel

from app.transcription.base import TranscriptResult, WordTiming

logger = logging.getLogger("transcription")


class LocalWhisperTranscriber:
    """Transcribes locally via faster-whisper (CTranslate2). Loads the model once
    per process on first use — model load is the expensive part, not inference.
    """

    provider_name = "local"

    def __init__(self, model_name: str = "base.en"):
        self.model_name = model_name
        self._model: WhisperModel | None = None

    @property
    def model(self) -> WhisperModel:
        if self._model is None:
            logger.info("Loading faster-whisper model %s (int8, CPU)", self.model_name)
            self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
        return self._model

    def transcribe(self, audio_path: Path) -> TranscriptResult:
        segments, info = self.model.transcribe(
            str(audio_path),
            word_timestamps=True,
            vad_filter=True,
        )

        words: list[WordTiming] = []
        text_parts: list[str] = []
        for segment in segments:
            text_parts.append(segment.text.strip())
            if segment.words:
                for w in segment.words:
                    words.append(
                        WordTiming(
                            word=w.word.strip(),
                            start_s=w.start,
                            end_s=w.end,
                            confidence=w.probability,
                        )
                    )

        return TranscriptResult(
            full_text=" ".join(p for p in text_parts if p),
            words=words,
            duration_s=info.duration,
            provider=self.provider_name,
            model=self.model_name,
        )
