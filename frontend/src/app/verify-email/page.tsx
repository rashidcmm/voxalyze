"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";

function VerifyEmailContent() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token");
  const [status, setStatus] = useState<"pending" | "success" | "error">(
    token ? "pending" : "error"
  );
  const [message, setMessage] = useState(token ? "Verifying..." : "Missing verification token.");

  useEffect(() => {
    if (!token) {
      return;
    }
    api
      .verifyEmail(token)
      .then((res) => {
        setStatus("success");
        setMessage(res.message);
      })
      .catch((err) => {
        setStatus("error");
        setMessage(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
      });
  }, [token]);

  return (
    <main className="flex flex-1 items-center justify-center p-6">
      <div className="w-full max-w-sm space-y-4 rounded-xl border border-black/10 p-6 text-center dark:border-white/10">
        <h1 className="text-xl font-semibold">Verify your email</h1>
        <p className={`text-sm ${status === "error" ? "text-red-600" : "text-gray-500"}`}>{message}</p>
        {status === "success" && (
          <Link href="/login" className="text-sm underline">
            Log in
          </Link>
        )}
      </div>
    </main>
  );
}

export default function VerifyEmailPage() {
  return (
    <Suspense fallback={null}>
      <VerifyEmailContent />
    </Suspense>
  );
}
