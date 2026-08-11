"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import ProtectedRoute from "@/components/ProtectedRoute";
import { useAuthStore } from "@/lib/authStore";
import { api, ApiError, SessionResponse } from "@/lib/api";

const PREP_SECONDS = 60;

// Prefer webm/opus (what MediaRecorder in Chrome/Edge/Firefox produces); fall back for Safari.
function pickMimeType(): string {
  const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];
  for (const type of candidates) {
    if (typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported(type)) {
      return type;
    }
  }
  return "";
}

type Stage =
  | "loading"
  | "load-error"
  | "prep"
  | "mic-denied"
  | "recording"
  | "uploading"
  | "upload-failed"
  | "done";

function PracticeContent() {
  const params = useParams<{ id: string }>();
  const sessionId = params.id;
  const router = useRouter();
  const token = useAuthStore((s) => s.token);

  const [session, setSession] = useState<SessionResponse | null>(null);
  const [stage, setStage] = useState<Stage>("loading");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [prepRemaining, setPrepRemaining] = useState(PREP_SECONDS);
  const [recordedSeconds, setRecordedSeconds] = useState(0);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const recordedBlobRef = useRef<Blob | null>(null);
  const mimeTypeRef = useRef<string>("");
  const prepIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const recIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Load the session once.
  useEffect(() => {
    if (!token || !sessionId) return;
    let cancelled = false;
    api
      .getSession(token, sessionId)
      .then((s) => {
        if (cancelled) return;
        setSession(s);
        if (s.status === "recording") {
          setStage("prep");
        } else {
          // Already recorded (revisiting the session) — show playback instead.
          setStage("done");
          api
            .getAudioObjectUrl(token, sessionId)
            .then((url) => !cancelled && setAudioUrl(url))
            .catch(() => {
              /* no audio yet for this status — fine */
            });
        }
      })
      .catch((err) => {
        if (cancelled) return;
        setErrorMsg(err instanceof ApiError ? err.message : "Could not load this session.");
        setStage("load-error");
      });
    return () => {
      cancelled = true;
    };
  }, [token, sessionId]);

  // Prep countdown. Recording starts from inside the interval tick (not a
  // separate effect reacting to prepRemaining hitting 0) so state updates
  // stay out of the effect body's direct execution path.
  useEffect(() => {
    if (stage !== "prep") return;
    prepIntervalRef.current = setInterval(() => {
      setPrepRemaining((r) => {
        const next = r - 1;
        if (next <= 0) {
          if (prepIntervalRef.current) clearInterval(prepIntervalRef.current);
          void startRecording();
          return 0;
        }
        return next;
      });
    }, 1000);
    return () => {
      if (prepIntervalRef.current) clearInterval(prepIntervalRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage]);

  const startRecording = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      const mimeType = pickMimeType();
      mimeTypeRef.current = mimeType;
      const recorder = mimeType
        ? new MediaRecorder(stream, { mimeType })
        : new MediaRecorder(stream);
      mediaRecorderRef.current = recorder;
      chunksRef.current = [];

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, {
          type: mimeTypeRef.current || "audio/webm",
        });
        recordedBlobRef.current = blob;
        stream.getTracks().forEach((t) => t.stop());
        void doUpload(blob);
      };

      recorder.start();
      setStage("recording");
      setRecordedSeconds(0);
      recIntervalRef.current = setInterval(() => {
        setRecordedSeconds((s) => s + 1);
      }, 1000);
    } catch {
      setStage("mic-denied");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function skipPrep() {
    if (prepIntervalRef.current) clearInterval(prepIntervalRef.current);
    setPrepRemaining(0);
    void startRecording();
  }

  function stopRecording() {
    if (recIntervalRef.current) clearInterval(recIntervalRef.current);
    mediaRecorderRef.current?.stop();
  }

  async function doUpload(blob: Blob) {
    if (!token || !sessionId) return;
    setStage("uploading");
    setErrorMsg(null);
    try {
      const extension = mimeTypeRef.current.includes("mp4") ? "m4a" : "webm";
      await api.uploadAudio(token, sessionId, blob, `recording.${extension}`);
      setStage("done");
      try {
        const url = await api.getAudioObjectUrl(token, sessionId);
        setAudioUrl(url);
      } catch {
        // playback fetch failing shouldn't block the "done" state
      }
    } catch (err) {
      setErrorMsg(
        err instanceof ApiError
          ? `Upload failed: ${err.message}`
          : "Upload failed — check your connection and retry."
      );
      setStage("upload-failed");
    }
  }

  function retryUpload() {
    if (recordedBlobRef.current) {
      void doUpload(recordedBlobRef.current);
    }
  }

  // Warn before leaving the tab while actively recording — the in-progress
  // recording lives only in browser memory and is not recoverable if lost.
  useEffect(() => {
    if (stage !== "recording") return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [stage]);

  // Cleanup on unmount.
  useEffect(() => {
    return () => {
      if (prepIntervalRef.current) clearInterval(prepIntervalRef.current);
      if (recIntervalRef.current) clearInterval(recIntervalRef.current);
      streamRef.current?.getTracks().forEach((t) => t.stop());
    };
  }, []);

  if (stage === "loading") {
    return <CenteredMessage>Loading session...</CenteredMessage>;
  }

  if (stage === "load-error") {
    return (
      <CenteredMessage>
        <p className="text-red-600">{errorMsg}</p>
        <button
          onClick={() => router.push("/dashboard")}
          className="mt-4 rounded-md border border-black/15 px-4 py-2 text-sm dark:border-white/15"
        >
          Back to dashboard
        </button>
      </CenteredMessage>
    );
  }

  return (
    <main className="mx-auto flex max-w-2xl flex-1 flex-col gap-6 p-6">
      {session && (
        <div className="rounded-xl border border-black/10 p-5 dark:border-white/10">
          <p className="text-xs uppercase tracking-wide text-gray-500">
            {session.topic.category} · difficulty {session.topic.difficulty}
          </p>
          <p className="mt-2 text-lg font-medium">{session.topic.text}</p>
        </div>
      )}

      {stage === "prep" && (
        <div className="flex flex-col items-center gap-4 rounded-xl border border-black/10 p-8 text-center dark:border-white/10">
          <p className="text-sm text-gray-500">Prep time — gather your thoughts.</p>
          <p className="text-5xl font-semibold tabular-nums">{prepRemaining}s</p>
          <button
            onClick={skipPrep}
            className="rounded-md bg-black px-4 py-2 text-sm font-medium text-white dark:bg-white dark:text-black"
          >
            Start recording now
          </button>
        </div>
      )}

      {stage === "mic-denied" && (
        <div className="flex flex-col items-center gap-3 rounded-xl border border-red-200 p-8 text-center dark:border-red-900">
          <p className="text-red-600">
            Microphone access was denied. Allow microphone access for this site in your
            browser settings, then try again.
          </p>
          <button
            onClick={() => void startRecording()}
            className="rounded-md bg-black px-4 py-2 text-sm font-medium text-white dark:bg-white dark:text-black"
          >
            Try again
          </button>
        </div>
      )}

      {stage === "recording" && (
        <div className="flex flex-col items-center gap-4 rounded-xl border border-black/10 p-8 text-center dark:border-white/10">
          <div className="flex items-center gap-2 text-red-600">
            <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-red-600" />
            Recording
          </div>
          <p className="text-5xl font-semibold tabular-nums">
            {formatTime(recordedSeconds)}
          </p>
          <button
            onClick={stopRecording}
            className="rounded-md bg-red-600 px-4 py-2 text-sm font-medium text-white"
          >
            Stop
          </button>
        </div>
      )}

      {stage === "uploading" && (
        <CenteredMessage>Uploading your recording...</CenteredMessage>
      )}

      {stage === "upload-failed" && (
        <div className="flex flex-col items-center gap-3 rounded-xl border border-red-200 p-8 text-center dark:border-red-900">
          <p className="text-red-600">{errorMsg}</p>
          <button
            onClick={retryUpload}
            className="rounded-md bg-black px-4 py-2 text-sm font-medium text-white dark:bg-white dark:text-black"
          >
            Retry upload
          </button>
        </div>
      )}

      {stage === "done" && (
        <div className="flex flex-col items-center gap-4 rounded-xl border border-black/10 p-8 text-center dark:border-white/10">
          <p className="font-medium">
            {session?.status === "recording"
              ? "Recorded and uploaded."
              : `Session status: ${session?.status}.`}
          </p>
          {audioUrl && <audio controls src={audioUrl} className="w-full" />}
          <p className="text-sm text-gray-500">
            Transcription, metrics and scoring run in the background — this can take up to a
            couple of minutes for a full session.
          </p>
          <div className="flex gap-2">
            <button
              onClick={() => router.push(`/sessions/${sessionId}/feedback`)}
              className="rounded-md bg-black px-4 py-2 text-sm font-medium text-white dark:bg-white dark:text-black"
            >
              View feedback
            </button>
            <button
              onClick={() => router.push("/dashboard")}
              className="rounded-md border border-black/15 px-4 py-2 text-sm dark:border-white/15"
            >
              Back to dashboard
            </button>
          </div>
        </div>
      )}
    </main>
  );
}

function CenteredMessage({ children }: { children: React.ReactNode }) {
  return (
    <main className="flex flex-1 items-center justify-center p-6 text-center text-sm text-gray-500">
      <div>{children}</div>
    </main>
  );
}

function formatTime(totalSeconds: number): string {
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export default function PracticePage() {
  return (
    <ProtectedRoute>
      <PracticeContent />
    </ProtectedRoute>
  );
}
