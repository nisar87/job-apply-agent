"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { afterLogin, api, setToken } from "@/lib/api";

/** A password box with a Show/Hide switch. */
export function PasswordField({ label, value, onChange, minLength, autoComplete, hint, error }: {
  label: string; value: string; onChange: (v: string) => void; minLength?: number;
  autoComplete: "current-password" | "new-password"; hint?: string; error?: string;
}) {
  const [shown, setShown] = useState(false);
  return (
    <label className="block text-sm">
      {label}
      <span className="mt-1 flex gap-2">
        <input className="field" type={shown ? "text" : "password"} minLength={minLength} autoComplete={autoComplete}
          value={value} onChange={(e) => onChange(e.target.value)} required aria-invalid={error ? true : undefined} />
        <button type="button" className="btn btn-quiet shrink-0 px-3" onClick={() => setShown(!shown)}
          aria-label={shown ? `Hide ${label.toLowerCase()}` : `Show ${label.toLowerCase()}`}>{shown ? "Hide" : "Show"}</button>
      </span>
      {error ? <span className="mt-1 block text-xs text-problem">{error}</span>
        : hint && <span className="mt-1 block text-xs text-quiet">{hint}</span>}
    </label>
  );
}

export default function AuthForm({ mode }: { mode: "login" | "signup" }) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const signup = mode === "signup";
  const mismatch = signup && repeat.length > 0 && repeat !== password;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (signup && password !== repeat) {
      setError("The two passwords don't match.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
      const body = signup ? { email, password, name, timezone } : { email, password };
      const res = await api<{ access_token: string }>(`/api/auth/${mode}`, { method: "POST", json: body });
      setToken(res.access_token);
      router.push(signup ? "/setup" : afterLogin("/"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto mt-24 w-full max-w-sm px-4">
      <h1 className="text-3xl">{signup ? "Create your account" : "Sign in"}</h1>
      <p className="mt-2 text-sm text-slate">
        {signup
          ? "You describe the jobs you want. The agent applies from your own Gmail and keeps the record."
          : "Pick up where your applications left off."}
      </p>
      <form onSubmit={submit} className="mt-6 space-y-4">
        {signup && (
          <label className="block text-sm">
            Your name
            <input className="field mt-1" value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" required />
          </label>
        )}
        <label className="block text-sm">
          Email
          <input className="field mt-1" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" required />
        </label>
        <PasswordField label="Password" value={password} onChange={setPassword} minLength={signup ? 10 : undefined}
          autoComplete={signup ? "new-password" : "current-password"} hint={signup ? "At least 10 characters." : undefined} />
        {signup && (
          <PasswordField label="Repeat password" value={repeat} onChange={setRepeat} minLength={10} autoComplete="new-password"
            error={mismatch ? "Doesn't match the password above." : undefined} />
        )}
        {!signup && (
          <p className="text-sm"><Link className="underline" href={email ? `/forgot?email=${encodeURIComponent(email)}` : "/forgot"}>Forgot your password?</Link></p>
        )}
        {error && <p className="text-sm text-problem" role="alert">{error}</p>}
        <button className="btn" disabled={busy || mismatch}>
          {signup ? "Create account" : "Sign in"}
        </button>
      </form>
      <p className="mt-6 text-sm text-slate">
        {signup ? (
          <>Already have an account? <Link className="underline" href="/login">Sign in</Link></>
        ) : (
          <>New here? <Link className="underline" href="/signup">Create an account</Link></>
        )}
      </p>
    </main>
  );
}
