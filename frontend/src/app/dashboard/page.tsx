"use client";

import { useState } from "react";
import ProtectedRoute from "@/components/ProtectedRoute";
import { useAuthStore } from "@/lib/authStore";
import { api, ApiError } from "@/lib/api";
import { useRouter } from "next/navigation";

function DashboardContent() {
  const user = useAuthStore((s) => s.user);
  const token = useAuthStore((s) => s.token);
  const logout = useAuthStore((s) => s.logout);
  const router = useRouter();
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

      <div className="mt-8 rounded-xl border border-dashed border-black/15 p-8 text-center text-sm text-gray-500 dark:border-white/15">
        Your progress trends across sessions will show up here once scoring is wired up.
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
