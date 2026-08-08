"use client";

import ProtectedRoute from "@/components/ProtectedRoute";
import { useAuthStore } from "@/lib/authStore";
import { useRouter } from "next/navigation";

function DashboardContent() {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const router = useRouter();

  function handleLogout() {
    logout();
    router.push("/login");
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

      <div className="mt-8 rounded-xl border border-dashed border-black/15 p-8 text-center text-sm text-gray-500 dark:border-white/15">
        Your practice sessions and progress trends will show up here.
        <br />
        (Recording &amp; scoring pipeline lands in later phases.)
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
