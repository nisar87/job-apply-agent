"use client";

import { useState } from "react";
import { ErrorNote, errorText, useLoad } from "@/components/ui";
import { api } from "@/lib/api";

type KeyStatus = {
  source: "own" | "platform" | "none"; hint: string; has_own_key: boolean; claude_hint: string; has_claude_key: boolean;
  own_key_cap_usd: number | null; cap_usd: number; spent_today_usd: number; message: string;
};

function Row({ label, hint, has, placeholder, onSave, onRemove, busy }: {
  label: string; hint: string; has: boolean; placeholder: string; busy: boolean;
  onSave: (key: string) => void; onRemove: () => void;
}) {
  const [key, setKey] = useState("");
  return (
    <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); if (key.trim()) { onSave(key.trim()); setKey(""); } }}>
      <label className="min-w-64 flex-1">{label}
        <input className="field mt-1" type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)}
          placeholder={has ? `Saved, ends in ${hint}. Paste a new one to replace it` : placeholder} />
      </label>
      <button className="btn" disabled={busy || !key.trim()}>{busy ? "Checking…" : "Save"}</button>
      {has && <button type="button" className="underline" disabled={busy} onClick={onRemove}>Remove</button>}
    </form>
  );
}

export default function ModelKey() {
  const status = useLoad<KeyStatus>("/api/account/model-key");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const k = status.data;

  async function call(method: "PUT" | "DELETE", json?: object, done = "Saved", query = "") {
    setBusy(true);
    setError("");
    setNote("");
    try {
      await api(`/api/account/model-key${query}`, { method, json });
      setNote(done);
      status.reload();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  if (!k) return null;
  const using = k.has_claude_key && k.has_own_key ? "Your Claude and Gemini keys: Claude does the hard tasks, Gemini the quick ones, and each covers for the other."
    : k.has_claude_key ? "Your Claude key."
    : k.has_own_key ? "Your Gemini key."
    : k.source === "platform" ? "The service's keys." : "No key: nothing can run.";
  return (
    <section>
      <p className="max-w-prose text-sm text-slate">Give a Claude key, a Gemini key, or both. Give neither and the service&apos;s own keys are used.</p>
      <p className="mt-2 text-sm"><b>In use:</b> {using}
        <span className="ml-2 text-slate">Spent today ${k.spent_today_usd.toFixed(2)}{k.cap_usd ? ` of $${k.cap_usd.toFixed(2)}` : ""}</span></p>
      {k.message && <p className="mt-1 text-sm text-problem">{k.message}</p>}
      <ErrorNote message={error} />
      <div className="mt-3 max-w-xl space-y-3 text-sm">
        <Row label="Claude key (optional)" hint={k.claude_hint} has={k.has_claude_key} placeholder="sk-ant-…" busy={busy}
          onSave={(key) => call("PUT", { claude_key: key }, "Claude key checked and saved")}
          onRemove={() => call("DELETE", undefined, "Claude key removed", "?which=claude")} />
        <Row label="Gemini key (optional)" hint={k.hint} has={k.has_own_key} placeholder="AIza…" busy={busy}
          onSave={(key) => call("PUT", { key }, "Gemini key checked and saved")}
          onRemove={() => call("DELETE", undefined, "Gemini key removed", "?which=gemini")} />
        <p className="text-xs text-slate">Keys are stored encrypted and never shown again. Get them at console.anthropic.com and aistudio.google.com/apikey.</p>
      </div>
      {note && <p className="mt-2 text-sm text-sent">{note}</p>}
    </section>
  );
}
