"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { afterLogin, api, setToken } from "@/lib/api";

export default function AuthForm({ mode }: { mode: "login" | "signup" }) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
      const body = mode === "signup" ? { email, password, name, timezone } : { email, password };
      const res = await api<{ access_token: string }>(`/api/auth/${mode}`, { method: "POST", json: body });
      setToken(res.access_token);
      router.push(mode === "signup" ? "/setup" : afterLogin("/"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto mt-24 w-full max-w-sm px-4">
      <h1 className="text-3xl">{mode === "signup" ? "Create your account" : "Sign in"}</h1>
      <p className="mt-2 text-sm text-slate">
        {mode === "signup"
          ? "You describe the jobs you want. The agent applies from your own Gmail and keeps the record."
          : "Pick up where your applications left off."}
      </p>
      <form onSubmit={submit} className="mt-6 space-y-4">
        {mode === "signup" && (
          <label className="block text-sm">
            Your name
            <input className="field mt-1" value={name} onChange={(e) => setName(e.target.value)} required />
          </label>
        )}
        <label className="block text-sm">
          Email
          <input className="field mt-1" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label className="block text-sm">
          Password
          <input
            className="field mt-1"
            type="password"
            minLength={mode === "signup" ? 10 : undefined}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
          {mode === "signup" && <span className="mt-1 block text-xs text-quiet">At least 10 characters.</span>}
        </label>
        {error && <p className="text-sm text-problem" role="alert">{error}</p>}
        <button className="btn" disabled={busy}>
          {mode === "signup" ? "Create account" : "Sign in"}
        </button>
      </form>
      <p className="mt-6 text-sm text-slate">
        {mode === "signup" ? (
          <>Already have an account? <Link className="underline" href="/login">Sign in</Link></>
        ) : (
          <>New here? <Link className="underline" href="/signup">Create an account</Link></>
        )}
      </p>
    </main>
  );
}
