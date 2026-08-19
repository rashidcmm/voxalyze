"""The backend's own LiveKit participant: joins each live room, subscribes to
every other participant's audio track, and for each one:
  1. runs energy-based VAD (app/rooms/vad.py) to build speech segments,
  2. streams the same PCM to Azure for live transcription
     (app/rooms/azure_stream.py). NOTE: the recognition results Azure sends
     back are NOT drained or persisted today — nothing reads
     AzureStreamingTranscriber.segments and no room_transcript_segments row
     is ever written from here. Wiring that up is Task 12 of
     docs/superpowers/plans/2026-08-19-gd-room-analytics-extension.md.
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
import time
import uuid
import wave
from typing import Callable

import numpy as np
from livekit import rtc

from app.core.config import get_settings
from app.core.storage import room_participant_audio_path
from app.rooms.azure_stream import AzureStreamingTranscriber
from app.rooms.live_stats import SpeechSegment
from app.rooms.livekit_tokens import issue_bot_token
from app.rooms.registry import RoomRegistry
from app.rooms.vad import FRAME_MS, FRAME_SAMPLES, SAMPLE_RATE_HZ, StreamingVAD

logger = logging.getLogger("rooms.bot")


class RoomBot:
    def __init__(self, room_id: str, registry: RoomRegistry, clock: Callable[[], float] = time.monotonic):
        self.room_id = room_id
        self.registry = registry
        # One RoomBot is constructed per room, on the first join (see
        # app/api/rooms.py's join_room), so this is the room's start reference
        # — the same moment live_stats_ws approximates with Room.created_at.
        # Every participant's StreamingVAD is seeded with its offset from here
        # so all their segments land on one shared timeline (see _vad_for).
        # `clock` is injectable so tests can advance room time deterministically.
        self._clock = clock
        self._room_start_ref = clock()
        self._vads: dict[str, StreamingVAD] = {}
        self._wave_writers: dict[str, wave.Wave_write] = {}
        self._transcribers: dict[str, AzureStreamingTranscriber] = {}
        # Leftover samples from an audio-stream event whose length wasn't a
        # whole multiple of FRAME_SAMPLES, carried into the next event so no
        # audio is dropped — see chunk_frames().
        self._frame_residuals: dict[str, np.ndarray] = {}
        # Participants close_participant() has run for and who have not since
        # reconnected. handle_audio_frame() drops frames for anyone in this
        # set instead of writing them, so a stray frame from a dead
        # connection (e.g. a buffered audio-stream event racing
        # participant_disconnected in run()) can't reopen and truncate an
        # already-closed WAV file.
        #
        # Rejoining a room reuses the same DB participant row — and therefore
        # the same LiveKit identity — whenever left_at is still NULL, which is
        # always (see app/api/rooms.py's join handler). So a browser refresh
        # or network blip fires participant_disconnected (-> close_participant)
        # and then track_subscribed again for the *same* identity: run()'s
        # on_track_subscribed calls reopen_participant() before that, which
        # discards the id from this set so recording/VAD/transcription resume.
        # Without that, this set would be add-only and every reconnect during
        # a live room would be silently blackholed for its remaining lifetime.
        self._closed_participants: set[str] = set()

    def _vad_for(self, participant_id: str) -> StreamingVAD:
        if participant_id not in self._vads:
            # Seeded with how far into the room this participant's first frame
            # arrived, so a late joiner's segments aren't timestamped from
            # their own zero — see StreamingVAD.start_offset_s.
            offset_s = max(0.0, self._clock() - self._room_start_ref)
            self._vads[participant_id] = StreamingVAD(participant_id=participant_id, start_offset_s=offset_s)
        return self._vads[participant_id]

    def chunk_frames(self, participant_id: str, samples: np.ndarray) -> list[np.ndarray]:
        """Split an arbitrary-length int16 PCM buffer into whole FRAME_SAMPLES
        frames, carrying any remainder into the next call for the same
        participant.

        rtc.AudioStream delivers events at whatever cadence the underlying
        WebRTC stack picks (commonly 10ms / 160 samples at 16kHz) — chunking
        by slicing each event independently would drop every event smaller
        than one frame (i.e. all of them) and lose the tail of every event
        that isn't an exact multiple. run() also asks for frame_size_ms=20,
        but that's a request to the SDK, not a guarantee, so the residual
        buffer here is what actually makes the pipeline lossless."""
        residual = self._frame_residuals.get(participant_id)
        if residual is not None and residual.size:
            buffer = np.concatenate((residual, samples))
        else:
            buffer = samples
        frame_count = buffer.size // FRAME_SAMPLES
        consumed = frame_count * FRAME_SAMPLES
        self._frame_residuals[participant_id] = np.array(buffer[consumed:], dtype=np.int16)
        return [buffer[i : i + FRAME_SAMPLES] for i in range(0, consumed, FRAME_SAMPLES)]

    def _start_transcriber(self, participant_id: str, loop: asyncio.AbstractEventLoop) -> None:
        """(Re)open the Azure streaming transcriber for a participant. A
        reconnect fires track_subscribed again for the same identity, so any
        transcriber already open for it must be closed first — otherwise the
        old one is overwritten, leaking its push stream and recognizer."""
        previous = self._transcribers.pop(participant_id, None)
        if previous is not None:
            try:
                previous.close()
            except Exception:  # noqa: BLE001 — a failed close must not block the replacement
                logger.warning(
                    "failed to close the previous transcriber for %s (room %s)", participant_id, self.room_id
                )
        try:
            self._transcribers[participant_id] = AzureStreamingTranscriber(loop)
        except Exception:  # noqa: BLE001 — transcription is best-effort; VAD/recording carry on
            logger.warning("live transcription unavailable for %s (room %s)", participant_id, self.room_id)

    def _is_valid_identity(self, identity: str) -> bool:
        """LiveKit's participant.identity is one trust hop further out than
        the server-generated ids app/core/storage.py assumes: it arrives over
        the wire and becomes a filesystem path component (and a dict key).
        join_room always issues it as a RoomParticipant UUID, so anything else
        is bogus — log and skip rather than crash the bot."""
        try:
            uuid.UUID(identity)
            return True
        except (ValueError, AttributeError, TypeError):
            logger.warning(
                "ignoring livekit participant with a non-uuid identity %r in room %s", identity, self.room_id
            )
            return False

    def _wave_writer_for(self, participant_id: str) -> wave.Wave_write:
        if participant_id not in self._wave_writers:
            path = room_participant_audio_path(self.room_id, participant_id)
            # A file already sitting at this path means a previous session
            # (before a reconnect) already recorded and closed it. The `wave`
            # module has no append mode, and reopening with "wb" would
            # truncate that prior audio, so read it back and rewrite it as
            # the seed of the new writer before any new frames land.
            previous_frames = b""
            if path.exists() and path.stat().st_size > 0:
                with wave.open(str(path), "rb") as reader:
                    previous_frames = reader.readframes(reader.getnframes())
            writer = wave.open(str(path), "wb")
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(SAMPLE_RATE_HZ)
            if previous_frames:
                writer.writeframes(previous_frames)
            self._wave_writers[participant_id] = writer
        return self._wave_writers[participant_id]

    def handle_audio_frame(self, participant_id: str, pcm: np.ndarray) -> None:
        """pcm: one 20ms int16 mono frame at 16kHz (FRAME_SAMPLES samples)."""
        if participant_id in self._closed_participants:
            logger.warning(
                "dropped a late audio frame for already-closed participant %s in room %s",
                participant_id,
                self.room_id,
            )
            return
        # Synchronous disk write on the event loop: acceptable at this
        # project's stated scale (<=6 participants per room, 640 bytes per
        # 20ms frame); would need offloading to a thread/executor before any
        # larger room size.
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

    def reopen_participant(self, participant_id: str) -> None:
        """Call when the same LiveKit identity subscribes a new audio track
        again (run()'s track_subscribed) after having been closed — i.e. a
        reconnect, not a stray frame from a dead connection. Un-blackholes
        the identity so handle_audio_frame stops dropping its frames; a
        no-op if the identity was never closed (e.g. a first-time join)."""
        self._closed_participants.discard(participant_id)

    def close_participant(self, participant_id: str) -> None:
        self._closed_participants.add(participant_id)
        self._frame_residuals.pop(participant_id, None)
        # Popped (not just read) for symmetry with _wave_writers/_transcribers
        # below: a room churning through join/leave cycles otherwise retains
        # every departed participant's VAD state for the room's lifetime.
        # Safe now that VAD clocks are room-relative — a reconnect builds a
        # fresh VAD seeded with the current room offset, so its segments still
        # continue the shared timeline instead of restarting at 0.0.
        vad = self._vads.pop(participant_id, None)
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
            self._start_transcriber(participant_id, loop)
            # frame_size_ms=20 asks the SDK to deliver events already sized to
            # exactly FRAME_SAMPLES; chunk_frames() still buffers any remainder
            # so a stack that ignores the hint can't silently drop audio.
            audio_stream = rtc.AudioStream(
                track, sample_rate=SAMPLE_RATE_HZ, num_channels=1, frame_size_ms=FRAME_MS
            )
            async for event in audio_stream:
                samples = np.frombuffer(event.frame.data, dtype=np.int16)
                for frame in self.chunk_frames(participant_id, samples):
                    self.handle_audio_frame(participant_id, frame)

        @room.on("track_subscribed")
        def on_track_subscribed(track, publication, participant):
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                if not self._is_valid_identity(participant.identity):
                    return
                # A new audio track being subscribed for this identity means
                # a live connection is behind it — whether this is a
                # first-time join or a reconnect after participant_disconnected
                # closed the identity earlier. Un-blackhole it either way (a
                # no-op if it was never closed) before forwarding frames.
                self.reopen_participant(participant.identity)
                asyncio.ensure_future(_forward_track(participant.identity, track))

        @room.on("participant_disconnected")
        def on_participant_disconnected(participant):
            if not self._is_valid_identity(participant.identity):
                return
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
