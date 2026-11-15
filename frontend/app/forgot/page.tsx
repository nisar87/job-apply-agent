"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { api } from "@/lib/api";

export default function Page() {
  return <Suspense><Forgot /></Suspense>;
}

function Forgot() {
  const [email, setEmail] = useState(useSearchParams().get("email") || "");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/api/auth/forgot", { method: "POST", json: { email } });
      setSent(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto mt-24 w-full max-w-sm px-4">
      <h1 className="text-3xl">Forgot your password?</h1>
      {sent ? (
        <div className="mt-4 space-y-3 text-sm">
          <p>If <strong>{email}</strong> has an account here, a link to choose a new password is on its way. It works once and expires in an hour.</p>
          <p className="text-slate">Nothing after a few minutes? Check your spam folder, or ask the admin for a reset link.</p>
          <p><Link className="underline" href="/login">Back to sign in</Link></p>
        </div>
      ) : (
        <>
          <p className="mt-2 text-sm text-slate">Enter the email you sign in with and we&apos;ll send you a link to choose a new password.</p>
          <form onSubmit={submit} className="mt-6 space-y-4">
            <label className="block text-sm">
              Email
              <input className="field mt-1" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" required />
            </label>
            {error && <p className="text-sm text-problem" role="alert">{error}</p>}
            <button className="btn" disabled={busy}>{busy ? "Sending…" : "Send reset link"}</button>
          </form>
          <p className="mt-6 text-sm text-slate">Remembered it? <Link className="underline" href="/login">Sign in</Link></p>
        </>
      )}
    </main>
  );
}
