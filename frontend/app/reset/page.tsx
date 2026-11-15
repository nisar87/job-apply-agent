"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { PasswordField } from "@/components/AuthForm";
import { api, setToken } from "@/lib/api";

export default function Page() {
  return <Suspense><Reset /></Suspense>;
}

function Reset() {
  const router = useRouter();
  const token = useSearchParams().get("token") || "";
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const mismatch = repeat.length > 0 && repeat !== password;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (password !== repeat) {
      setError("The two passwords don't match.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const res = await api<{ access_token: string }>("/api/auth/reset", { method: "POST", json: { token, password } });
      setToken(res.access_token);
      router.push("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto mt-24 w-full max-w-sm px-4">
      <h1 className="text-3xl">Choose a new password</h1>
      {!token ? (
        <p className="mt-4 text-sm">This link is incomplete. <Link className="underline" href="/forgot">Ask for a new one</Link>.</p>
      ) : (
        <form onSubmit={submit} className="mt-6 space-y-4">
          <PasswordField label="New password" value={password} onChange={setPassword} minLength={10} autoComplete="new-password" hint="At least 10 characters." />
          <PasswordField label="Repeat new password" value={repeat} onChange={setRepeat} minLength={10} autoComplete="new-password"
            error={mismatch ? "Doesn't match the password above." : undefined} />
          {error && (
            <p className="text-sm text-problem" role="alert">
              {error} {error.includes("expired") && <Link className="underline" href="/forgot">Send a new link</Link>}
            </p>
          )}
          <button className="btn" disabled={busy || mismatch}>{busy ? "Saving…" : "Save and sign in"}</button>
        </form>
      )}
    </main>
  );
}
