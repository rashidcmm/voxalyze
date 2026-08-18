"""The backend's own LiveKit participant: joins each live room, subscribes to
every other participant's audio track, and for each one:
  1. runs energy-based VAD (app/rooms/vad.py) to build speech segments,
  2. streams the same PCM to Azure for live transcription (app/rooms/azure_stream.py),
  3. records the raw audio to local disk (audio only — no video, per the
     design spec's Non-goals).

Runs in-process (spawned via asyncio.create_task, tracked in
app/rooms/registry.py) rather than as a separate daemon — see this plan's
Global Constraints.

`handle_audio_frame`/`close_participant` are the two methods covered by unit
tests. `run()` is the thin LiveKit-connection shell around them — not unit
tested by design (see this task's header note); verified manually per the
"Manual verification" section at the end of this plan.
"""
import asyncio
import logging
import wave

import numpy as np
from livekit import rtc

from app.core.config import get_settings
from app.core.storage import room_participant_audio_path
from app.rooms.azure_stream import AzureStreamingTranscriber
from app.rooms.live_stats import SpeechSegment
from app.rooms.livekit_tokens import issue_bot_token
from app.rooms.registry import RoomRegistry
from app.rooms.vad import FRAME_SAMPLES, SAMPLE_RATE_HZ, StreamingVAD

logger = logging.getLogger("rooms.bot")


class RoomBot:
    def __init__(self, room_id: str, registry: RoomRegistry):
        self.room_id = room_id
        self.registry = registry
        self._vads: dict[str, StreamingVAD] = {}
        self._wave_writers: dict[str, wave.Wave_write] = {}
        self._transcribers: dict[str, AzureStreamingTranscriber] = {}

    def _vad_for(self, participant_id: str) -> StreamingVAD:
        if participant_id not in self._vads:
            self._vads[participant_id] = StreamingVAD(participant_id=participant_id)
        return self._vads[participant_id]

    def _wave_writer_for(self, participant_id: str) -> wave.Wave_write:
        if participant_id not in self._wave_writers:
            path = room_participant_audio_path(self.room_id, participant_id)
            writer = wave.open(str(path), "wb")
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(SAMPLE_RATE_HZ)
            self._wave_writers[participant_id] = writer
        return self._wave_writers[participant_id]

    def handle_audio_frame(self, participant_id: str, pcm: np.ndarray) -> None:
        """pcm: one 20ms int16 mono frame at 16kHz (FRAME_SAMPLES samples)."""
        self._wave_writer_for(participant_id).writeframes(pcm.tobytes())

        vad = self._vad_for(participant_id)
        segments_before = len(vad.closed_segments)
        vad.push_frame(pcm)
        for start_s, end_s in vad.closed_segments[segments_before:]:
            self.registry.record_segment(
                self.room_id, SpeechSegment(participant_id=participant_id, start_s=start_s, end_s=end_s)
            )

        transcriber = self._transcribers.get(participant_id)
        if transcriber is not None:
            transcriber.push_pcm(pcm.tobytes())

    def close_participant(self, participant_id: str) -> None:
        vad = self._vads.get(participant_id)
        if vad is not None:
            segments_before = len(vad.closed_segments)
            vad.flush()
            for start_s, end_s in vad.closed_segments[segments_before:]:
                self.registry.record_segment(
                    self.room_id, SpeechSegment(participant_id=participant_id, start_s=start_s, end_s=end_s)
                )
        writer = self._wave_writers.pop(participant_id, None)
        if writer is not None:
            writer.close()
        transcriber = self._transcribers.pop(participant_id, None)
        if transcriber is not None:
            transcriber.close()

    async def run(self) -> None:
        """Connects to the LiveKit room as the analysis bot, subscribes to
        every participant's audio track, and forwards received frames to
        handle_audio_frame. Runs until cancelled (see registry.stop_bot)."""
        settings = get_settings()
        room = rtc.Room()
        loop = asyncio.get_event_loop()

        async def _forward_track(participant_id: str, track: rtc.Track) -> None:
            try:
                self._transcribers[participant_id] = AzureStreamingTranscriber(loop)
            except Exception:
                logger.warning("live transcription unavailable for %s (room %s)", participant_id, self.room_id)
            audio_stream = rtc.AudioStream(track, sample_rate=SAMPLE_RATE_HZ, num_channels=1)
            async for event in audio_stream:
                samples = np.frombuffer(event.frame.data, dtype=np.int16)
                for i in range(0, len(samples) - FRAME_SAMPLES + 1, FRAME_SAMPLES):
                    self.handle_audio_frame(participant_id, samples[i : i + FRAME_SAMPLES])

        @room.on("track_subscribed")
        def on_track_subscribed(track, publication, participant):
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                asyncio.ensure_future(_forward_track(participant.identity, track))

        @room.on("participant_disconnected")
        def on_participant_disconnected(participant):
            self.close_participant(participant.identity)

        token = issue_bot_token(room_name=self.room_id)
        await room.connect(settings.livekit_url, token)
        logger.info("room bot connected to room %s", self.room_id)

        try:
            await asyncio.Future()  # runs until this task is cancelled
        finally:
            for participant_id in list(self._wave_writers.keys()):
                self.close_participant(participant_id)
            await room.disconnect()
