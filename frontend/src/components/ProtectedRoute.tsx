"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/lib/authStore";

export default function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const token = useAuthStore((s) => s.token);
  const user = useAuthStore((s) => s.user);
  const hydrateUser = useAuthStore((s) => s.hydrateUser);
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    // zustand persist rehydrates asynchronously; give it a tick before deciding.
    const check = async () => {
      if (token && !user) {
        await hydrateUser();
      }
      setChecked(true);
    };
    check();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  useEffect(() => {
    if (checked && !token) {
      router.replace("/login");
    }
  }, [checked, token, router]);

  if (!checked || !token) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-gray-500">
        Loading...
      </div>
    );
  }

  return <>{children}</>;
}
