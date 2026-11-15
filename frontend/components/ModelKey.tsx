"use client";

import { useState } from "react";
import { ErrorNote, errorText, useLoad } from "@/components/ui";
import { api } from "@/lib/api";

type KeyStatus = {
  policy: "platform" | "own" | "either"; source: "own" | "platform" | "none"; hint: string; has_own_key: boolean;
  own_key_cap_usd: number | null; platform_key_allowed: boolean; cap_usd: number; spent_today_usd: number; message: string;
};

const SOURCE: Record<KeyStatus["source"], string> = {
  own: "Running on your own key",
  platform: "Running on the service's key",
  none: "Stopped: no key to run on",
};

export default function ModelKey() {
  const status = useLoad<KeyStatus>("/api/account/model-key");
  const [key, setKey] = useState("");
  const [cap, setCap] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const k = status.data;

  async function call(method: "PUT" | "DELETE", json?: object, done = "Saved") {
    setBusy(true);
    setError("");
    setNote("");
    try {
      await api("/api/account/model-key", { method, json });
      setKey("");
      setCap("");
      setNote(done);
      status.reload();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  if (!k) return null;
  const platformOnly = k.policy === "platform";
  return (
    <section>
      <h2 className="mt-10 text-xl">AI key</h2>
      <p className="mt-1 max-w-prose text-sm text-slate">
        The agent uses Google Gemini to read jobs and write your emails.
        {platformOnly
          ? " This service provides the key, so you don't need your own."
          : " You can use your own free or paid key from Google AI Studio. Then you decide how much it may spend."}
      </p>
      <p className="mt-3 text-sm">
        <span className={`postmark ${k.source === "none" ? "text-problem" : "text-sent"}`}>{SOURCE[k.source]}</span>
        <span className="ml-3 text-slate">
          Spent today ${k.spent_today_usd.toFixed(2)}{k.cap_usd ? ` of $${k.cap_usd.toFixed(2)}` : " (no daily limit)"}
        </span>
      </p>
      <ErrorNote message={error} />
      {!platformOnly && (
        <div className="mt-3 max-w-xl space-y-3 text-sm">
          {k.has_own_key && (
            <p className="flex flex-wrap items-center gap-3">
              Your key ends in <code>{k.hint}</code>
              <button className="underline" disabled={busy} onClick={() => call("DELETE", undefined, "Key removed")}>Remove it</button>
            </p>
          )}
          <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); if (key.trim()) call("PUT", { key }, "Key checked with Google and saved"); }}>
            <label className="min-w-64 flex-1">{k.has_own_key ? "Replace with a new key" : "Your Gemini API key"}
              <input className="field mt-1" type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} placeholder="AIza…" />
            </label>
            <button className="btn" disabled={busy || !key.trim()}>{busy ? "Checking…" : "Save key"}</button>
          </form>
          <p className="text-xs text-slate">
            Get one at <a className="underline" href="https://aistudio.google.com/apikey" target="_blank" rel="noreferrer">aistudio.google.com/apikey</a>.
            It is stored encrypted and never shown again.
          </p>
          {k.has_own_key && (
            <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); call("PUT", cap.trim() ? { own_key_cap_usd: Number(cap) } : { clear_cap: true }); }}>
              <label>Daily spending limit on your key, in US$ (empty for none)
                <input className="field mt-1 max-w-32" type="number" min="0" max="100" step="0.5" value={cap}
                  onChange={(e) => setCap(e.target.value)} placeholder={k.own_key_cap_usd != null ? String(k.own_key_cap_usd) : "none"} />
              </label>
              <button className="btn btn-quiet" disabled={busy}>Set limit</button>
            </form>
          )}
        </div>
      )}
      {note && <p className="mt-2 text-sm text-sent">{note}</p>}
    </section>
  );
}
