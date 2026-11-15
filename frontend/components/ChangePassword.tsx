"use client";

import { useState } from "react";
import { PasswordField } from "@/components/AuthForm";
import { ErrorNote, errorText } from "@/components/ui";
import { api } from "@/lib/api";

export default function ChangePassword() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const mismatch = repeat.length > 0 && repeat !== next;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (next !== repeat) return;
    setBusy(true);
    setError("");
    setNote("");
    try {
      await api("/api/auth/password", { method: "POST", json: { current_password: current, new_password: next } });
      setCurrent("");
      setNext("");
      setRepeat("");
      setNote("Password changed");
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <h2 className="mt-10 text-xl">Password</h2>
      <ErrorNote message={error} />
      <form onSubmit={submit} className="mt-3 grid max-w-xl gap-4">
        <PasswordField label="Current password" value={current} onChange={setCurrent} autoComplete="current-password" />
        <PasswordField label="New password" value={next} onChange={setNext} minLength={10} autoComplete="new-password" hint="At least 10 characters." />
        <PasswordField label="Repeat new password" value={repeat} onChange={setRepeat} minLength={10} autoComplete="new-password"
          error={mismatch ? "Doesn't match the new password." : undefined} />
        <div className="flex items-center gap-3">
          <button className="btn" disabled={busy || mismatch}>{busy ? "Saving…" : "Change password"}</button>
          {note && <span className="text-sm text-sent">{note}</span>}
        </div>
      </form>
    </section>
  );
}
