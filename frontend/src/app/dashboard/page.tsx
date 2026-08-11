"use client";

import { useEffect, useState } from "react";
import ProtectedRoute from "@/components/ProtectedRoute";
import { useAuthStore } from "@/lib/authStore";
import { api, ApiError, ProgressResponse } from "@/lib/api";
import { useRouter } from "next/navigation";
import TrendChart from "@/components/charts/TrendChart";
import RadarChart from "@/components/charts/RadarChart";

function DashboardContent() {
  const user = useAuthStore((s) => s.user);
  const token = useAuthStore((s) => s.token);
  const logout = useAuthStore((s) => s.logout);
  const router = useRouter();
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<ProgressResponse | null>(null);
  const [progressError, setProgressError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    api
      .getProgress(token)
      .then((p) => !cancelled && setProgress(p))
      .catch((err) => {
        if (cancelled) return;
        setProgressError(err instanceof ApiError ? err.message : "Could not load progress.");
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  function handleLogout() {
    logout();
    router.push("/login");
  }

  async function handleStartPractice() {
    if (!token) return;
    setStarting(true);
    setError(null);
    try {
      const session = await api.createSession(token);
      router.push(`/practice/${session.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not start a session. Try again.");
      setStarting(false);
    }
  }

  return (
    <main className="flex flex-1 flex-col p-6">
      <div className="flex items-center justify-between border-b border-black/10 pb-4 dark:border-white/10">
        <h1 className="text-xl font-semibold">
          Welcome{user ? `, ${user.name}` : ""}
        </h1>
        <button
          onClick={handleLogout}
          className="rounded-md border border-black/15 px-3 py-1.5 text-sm dark:border-white/15"
        >
          Log out
        </button>
      </div>

      <div className="mt-8 flex flex-col items-center gap-4 rounded-xl border border-dashed border-black/15 p-8 text-center dark:border-white/15">
        <p className="text-sm text-gray-500">
          Ready to practice? You&apos;ll get a random topic, a 60s prep timer, then record
          yourself for 2–5 minutes.
        </p>
        <button
          onClick={handleStartPractice}
          disabled={starting}
          className="rounded-md bg-black px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-black"
        >
          {starting ? "Starting..." : "Start practice"}
        </button>
        {error && <p className="text-sm text-red-600">{error}</p>}
      </div>

      <div className="mt-8 grid gap-6 lg:grid-cols-[2fr_1fr]">
        <div className="rounded-xl border border-black/10 p-5 dark:border-white/10">
          <h2 className="text-sm font-medium text-gray-500">Progress across sessions</h2>
          {progressError && <p className="mt-2 text-sm text-red-600">{progressError}</p>}
          {!progressError && progress && (
            <div className="mt-4">
              <TrendChart points={progress.points} />
            </div>
          )}
        </div>

        <div className="rounded-xl border border-black/10 p-5 dark:border-white/10">
          <h2 className="text-sm font-medium text-gray-500">Latest session</h2>
          {progress?.latest ? (
            <div className="mt-4 flex flex-col items-center">
              <RadarChart scores={progress.latest} />
              <p className="mt-2 text-2xl font-semibold tabular-nums">{progress.latest.overall}</p>
              <p className="text-xs text-gray-500">overall score</p>
            </div>
          ) : (
            <p className="mt-4 text-sm text-gray-500">Complete a session to see your radar chart.</p>
          )}
        </div>
      </div>
    </main>
  );
}

export default function DashboardPage() {
  return (
    <ProtectedRoute>
      <DashboardContent />
    </ProtectedRoute>
  );
}
