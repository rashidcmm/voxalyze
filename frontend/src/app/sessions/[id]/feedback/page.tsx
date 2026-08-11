"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import ProtectedRoute from "@/components/ProtectedRoute";
import { useAuthStore } from "@/lib/authStore";
import { api, ApiError, FeedbackResponse } from "@/lib/api";
import RadarChart from "@/components/charts/RadarChart";
import TranscriptHighlighted from "@/components/TranscriptHighlighted";
import { DIMENSIONS, dimensionColorVar } from "@/lib/dimensions";

function FeedbackContent() {
  const params = useParams<{ id: string }>();
  const sessionId = params.id;
  const router = useRouter();
  const token = useAuthStore((s) => s.token);

  const [feedback, setFeedback] = useState<FeedbackResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  function fetchFeedback() {
    if (!token || !sessionId) return;
    api
      .getFeedback(token, sessionId)
      .then((f) => setFeedback(f))
      .catch((err) => {
        if (err instanceof ApiError && err.status === 404) {
          setError(
            "Feedback isn't ready yet — transcription and scoring can take up to a couple of minutes. Refresh to check again."
          );
        } else {
          setError(err instanceof ApiError ? err.message : "Could not load feedback.");
        }
      })
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    fetchFeedback();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, sessionId]);

  function refresh() {
    setLoading(true);
    setError(null);
    fetchFeedback();
  }

  if (loading) {
    return <CenteredMessage>Loading feedback...</CenteredMessage>;
  }

  if (error || !feedback) {
    return (
      <CenteredMessage>
        <p className="text-sm text-gray-500">{error}</p>
        <div className="mt-4 flex justify-center gap-2">
          <button
            onClick={refresh}
            className="rounded-md bg-black px-4 py-2 text-sm font-medium text-white dark:bg-white dark:text-black"
          >
            Refresh
          </button>
          <button
            onClick={() => router.push("/dashboard")}
            className="rounded-md border border-black/15 px-4 py-2 text-sm dark:border-white/15"
          >
            Back to dashboard
          </button>
        </div>
      </CenteredMessage>
    );
  }

  const notConfiguredNote =
    feedback.headline.clarity === null || feedback.headline.argumentation === null;

  return (
    <main className="mx-auto flex max-w-3xl flex-1 flex-col gap-6 p-6">
      <div>
        <p className="text-xs uppercase tracking-wide text-gray-500">
          {feedback.topic_category} · difficulty {feedback.topic_difficulty} ·{" "}
          {new Date(feedback.created_at).toLocaleString()}
        </p>
        <h1 className="mt-1 text-lg font-medium">{feedback.topic_text}</h1>
      </div>

      <section className="rounded-xl border border-black/10 p-5 dark:border-white/10">
        <h2 className="text-sm font-medium text-gray-500">Headline scores</h2>
        <div className="mt-4 grid grid-cols-2 gap-6 sm:grid-cols-[1fr_auto]">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {DIMENSIONS.map((dim) => (
              <div key={dim.key} className="rounded-lg border border-black/10 p-3 dark:border-white/10">
                <p className="text-xs text-gray-500">{dim.label}</p>
                <p className="text-xl font-semibold tabular-nums" style={{ color: dimensionColorVar(dim.key) }}>
                  {feedback.headline[dim.key] ?? "—"}
                </p>
              </div>
            ))}
            <div className="rounded-lg border border-black/10 p-3 dark:border-white/10">
              <p className="text-xs text-gray-500">Overall</p>
              <p className="text-xl font-semibold tabular-nums">{feedback.headline.overall}</p>
            </div>
          </div>
          <div className="hidden sm:block">
            <RadarChart scores={feedback.headline} />
          </div>
        </div>
        {notConfiguredNote && (
          <p className="mt-3 text-xs text-gray-500">
            Clarity and/or Argumentation aren&apos;t scored yet — they need Azure Speech and
            Anthropic API keys configured (see README).
          </p>
        )}
      </section>

      {feedback.relevance_drift_curve && feedback.relevance_drift_curve.length > 0 && (
        <section className="rounded-xl border border-black/10 p-5 dark:border-white/10">
          <h2 className="text-sm font-medium text-gray-500">Topic relevance over time</h2>
          <div className="mt-3 flex flex-col gap-1.5">
            {feedback.relevance_drift_curve.map((u, i) => (
              <div key={i} className="flex items-center gap-2" title={u.text}>
                <span className="w-10 shrink-0 text-[10px] tabular-nums text-gray-500">
                  {u.start_s.toFixed(0)}s
                </span>
                <div className="h-3 flex-1 rounded bg-black/5 dark:bg-white/10">
                  <div
                    className="h-3 rounded"
                    style={{
                      width: `${Math.round(u.similarity * 100)}%`,
                      backgroundColor: dimensionColorVar("relevance"),
                    }}
                  />
                </div>
                <span className="w-10 shrink-0 text-right text-[10px] tabular-nums text-gray-500">
                  {Math.round(u.similarity * 100)}%
                </span>
              </div>
            ))}
          </div>
        </section>
      )}

      {feedback.top_filler_words.length > 0 && (
        <section className="rounded-xl border border-black/10 p-5 dark:border-white/10">
          <h2 className="text-sm font-medium text-gray-500">Top filler words</h2>
          <div className="mt-3 flex flex-wrap gap-2">
            {feedback.top_filler_words.map((f) => (
              <span
                key={f.word}
                className="rounded-full border border-black/10 px-3 py-1 text-xs dark:border-white/10"
              >
                &ldquo;{f.word}&rdquo; × {f.count}
              </span>
            ))}
          </div>
        </section>
      )}

      {feedback.improvement_points && feedback.improvement_points.length > 0 && (
        <section className="rounded-xl border border-black/10 p-5 dark:border-white/10">
          <h2 className="text-sm font-medium text-gray-500">Improvement points</h2>
          <ul className="mt-3 list-disc space-y-1.5 pl-5 text-sm">
            {feedback.improvement_points.map((point, i) => (
              <li key={i}>{point}</li>
            ))}
          </ul>
          {feedback.argument_rationale && (
            <p className="mt-3 text-xs text-gray-500">{feedback.argument_rationale}</p>
          )}
        </section>
      )}

      <section className="rounded-xl border border-black/10 p-5 dark:border-white/10">
        <h2 className="text-sm font-medium text-gray-500">
          Transcript{" "}
          <span className="font-normal text-gray-400">
            (amber = slow segment, ⏸ = pause — red if &gt;2s)
          </span>
        </h2>
        <div className="mt-3 text-sm">
          <TranscriptHighlighted words={feedback.transcript_words} slowSegments={feedback.slow_segments} />
        </div>
      </section>

      <button
        onClick={() => router.push("/dashboard")}
        className="self-start rounded-md border border-black/15 px-4 py-2 text-sm dark:border-white/15"
      >
        Back to dashboard
      </button>
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

export default function FeedbackPage() {
  return (
    <ProtectedRoute>
      <FeedbackContent />
    </ProtectedRoute>
  );
}
