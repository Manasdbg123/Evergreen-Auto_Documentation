"use client";

import { useState } from "react";
import { useSearchParams } from "next/navigation";
import { requestOtp, verifyOtp } from "@/lib/auth";
import ErrorBox from "./ErrorBox";

export default function LoginPage() {
  const searchParams = useSearchParams();
  const callbackUrl = searchParams.get("callbackUrl") || "/";
  const [step, setStep] = useState<"email" | "code">("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleRequestOtp() {
    setIsSubmitting(true);
    setError(null);
    try {
      await requestOtp(email);
      setStep("code");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleVerify() {
    setIsSubmitting(true);
    setError(null);
    try {
      await verifyOtp(email, code);
      window.location.href = callbackUrl;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Invalid code");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="h-screen flex items-center justify-center">
      <div className="w-full max-w-sm">
        <h1 className="text-xl font-semibold text-neutral-900 mb-1">Sign in</h1>
        <p className="text-sm text-neutral-500 mb-6">
          Use your company email to continue.
        </p>

        {step === "email" ? (
          <>
            <input
              type="email"
              placeholder="you@clueso.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full mb-3 px-4 py-2 border border-neutral-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900"
            />
            <button
              onClick={handleRequestOtp}
              disabled={!email || isSubmitting}
              className="w-full py-2.5 rounded-lg bg-neutral-900 text-white text-sm font-medium disabled:opacity-40"
            >
              {isSubmitting ? "Sending..." : "Send code"}
            </button>
          </>
        ) : (
          <>
            <p className="text-xs text-neutral-400 mb-3">
              Code sent to {email}
            </p>
            <input
              type="text"
              placeholder="6-digit code"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              maxLength={6}
              className="w-full mb-3 px-4 py-2 border border-neutral-300 rounded-lg text-sm tracking-widest text-center focus:outline-none focus:ring-2 focus:ring-neutral-900"
            />
            <button
              onClick={handleVerify}
              disabled={code.length !== 6 || isSubmitting}
              className="w-full py-2.5 rounded-lg bg-neutral-900 text-white text-sm font-medium disabled:opacity-40"
            >
              {isSubmitting ? "Verifying..." : "Verify"}
            </button>
          </>
        )}

        {error && <ErrorBox error={error} />}
      </div>
    </main>
  );
}
