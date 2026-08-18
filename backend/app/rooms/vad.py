"""Lightweight energy-based voice-activity detection over a stream of PCM
audio frames — deliberately not a native VAD library (e.g. webrtcvad), to
avoid another native dependency on top of what LiveKit/Azure already need.
Good enough to turn "is this participant's mic producing speech-level audio
right now" into segments; not meant to be broadcast-grade.
"""
from dataclasses import dataclass, field

import numpy as np

SAMPLE_RATE_HZ = 16_000
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE_HZ * FRAME_MS // 1000  # 320 samples/frame at 16kHz/20ms
RMS_SPEECH_THRESHOLD = 300.0  # int16 RMS floor; silence/room noise sits well below this
HANGOVER_FRAMES = 15  # ~300ms of below-threshold audio before a segment is considered ended


@dataclass
class StreamingVAD:
    """Feed 20ms int16 PCM frames in order via push_frame(); closed_segments
    fills in with (start_s, end_s) tuples as speech ends. Stateful per
    participant — one instance per participant's audio track (see
    app/rooms/bot.py).

    A segment only closes once HANGOVER_FRAMES of silence confirm speech has
    really ended (so a brief pause mid-sentence doesn't split it), but the
    recorded end_s is the moment speech actually stopped — the *first* silent
    frame of that run — not the later moment the hangover confirms it;
    otherwise every segment's end would be inflated by ~300ms."""

    participant_id: str
    closed_segments: list[tuple[float, float]] = field(default_factory=list)
    _elapsed_s: float = 0.0
    _speech_start_s: float | None = None
    _speech_end_candidate_s: float | None = None
    _silence_run: int = 0

    def push_frame(self, pcm_frame: np.ndarray) -> None:
        if pcm_frame.size != FRAME_SAMPLES:
            raise ValueError(f"expected {FRAME_SAMPLES}-sample frames, got {pcm_frame.size}")

        rms = float(np.sqrt(np.mean(pcm_frame.astype(np.float64) ** 2)))
        is_speech = rms >= RMS_SPEECH_THRESHOLD
        frame_start_s = self._elapsed_s
        self._elapsed_s += FRAME_MS / 1000.0

        if is_speech:
            self._silence_run = 0
            if self._speech_start_s is None:
                self._speech_start_s = frame_start_s
        elif self._speech_start_s is not None:
            if self._silence_run == 0:
                self._speech_end_candidate_s = frame_start_s  # true moment speech stopped
            self._silence_run += 1
            if self._silence_run >= HANGOVER_FRAMES:
                self.closed_segments.append((self._speech_start_s, self._speech_end_candidate_s))
                self._speech_start_s = None
                self._silence_run = 0

    def flush(self) -> None:
        """Call when the track ends — closes any still-open speech segment."""
        if self._speech_start_s is not None:
            end_s = self._speech_end_candidate_s if self._silence_run > 0 else self._elapsed_s
            self.closed_segments.append((self._speech_start_s, end_s))
            self._speech_start_s = None
