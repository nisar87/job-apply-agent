"use client";

import { useState } from "react";
import Shell from "@/components/Shell";
import SignupSettings from "@/components/SignupSettings";
import { ErrorNote, errorText, useLoad } from "@/components/ui";
import { api } from "@/lib/api";

type Overview = {
  global_pause: boolean;
  users: { id: string; email: string; status: string; mail: string; failed_runs_24h: number; sent_24h: number }[];
};
type UserRow = {
  id: string; email: string; status: string; key_source: "own" | "platform" | "none"; has_own_key: boolean;
  platform_key_allowed: boolean; platform_cap_usd: number | null; cap_usd: number; daily_send_limit: number;
  is_admin: boolean; spend_today_usd: number; real_sending: boolean; production: boolean;
};
type Platform = { key_policy: "platform" | "own" | "either"; default_cap_usd: number; platform_key_configured: boolean };
type Health = { ok: boolean; alerts: { kind: string; detail: string }[] };
type BlockReq = { id: string; contact_email: string; company: string; message: string };
type Attempt = {
  at: string; user: string; job: string; application_id: string; url: string; hiring_system: string; step: string;
  automatic: boolean; attempt: number | null; final: boolean | null; kind: string; reason: string;
};
type FormLog = { days: number; filled: number; submitted: number; failed: number; by_kind: Record<string, number>; attempts: Attempt[] };

const POLICIES: { value: Platform["key_policy"]; label: string; detail: string }[] = [
  { value: "platform", label: "My key for everyone", detail: "Every user runs on your Gemini key, within the limits below. Users cannot add their own." },
  { value: "either", label: "Their key, or mine if allowed", detail: "A user's own key when they add one; otherwise your key, for users you allow." },
  { value: "own", label: "Everyone brings their own key", detail: "Nobody runs on your key. A user without a key is stopped until they add one." },
];
const SOURCE = { own: "Own key", platform: "Your key", none: "No key" };

export default function Admin() {
  const overview = useLoad<Overview>("/api/operator/overview");
  const users = useLoad<UserRow[]>("/api/operator/users");
  const platform = useLoad<Platform>("/api/operator/settings");
  const health = useLoad<Health>("/api/operator/health");
  const requests = useLoad<BlockReq[]>("/api/operator/block-requests");
  const forms = useLoad<FormLog>("/api/operator/form-attempts?days=14");
  const [error, setError] = useState("");
  const [cap, setCap] = useState("");
  const [reset, setReset] = useState<{ email: string; link: string } | null>(null);

  async function act(fn: () => Promise<unknown>, ...reloads: (() => void)[]) {
    setError("");
    try {
      await fn();
      reloads.forEach((r) => r());
    } catch (e) {
      setError(errorText(e));
    }
  }
  const patchUser = (id: string, json: object) =>
    act(() => api(`/api/operator/users/${id}`, { method: "PATCH", json }), users.reload);
  const saveSettings = (json: object) =>
    act(() => api("/api/operator/settings", { method: "PUT", json }), platform.reload, users.reload);

  if (overview.error) {
    return <Shell title="Admin"><ErrorNote message="This page is for the admin." /></Shell>;
  }
  const extra = Object.fromEntries((overview.data?.users || []).map((u) => [u.id, u]));
  const p = platform.data;

  return (
    <Shell title="Admin">
      <ErrorNote message={error} />

      {overview.data && (
        <p className="mb-4 flex flex-wrap items-center gap-3 text-sm">
          {overview.data.global_pause ? <span className="postmark text-problem">All sending is paused</span> : <span className="postmark text-sent">Sending is on</span>}
          <button className={`btn ${overview.data.global_pause ? "" : "btn-danger"}`}
            onClick={() => act(() => api(`/api/operator/global-pause?on=${!overview.data!.global_pause}`, { method: "POST" }), overview.reload)}>
            {overview.data.global_pause ? "Resume sending for everyone" : "Pause sending for everyone"}
          </button>
        </p>
      )}
      {health.data && (health.data.ok ? <p className="mb-4 text-sm text-sent">No open alerts.</p> : (
        <ul className="mb-4 border-l-4 border-problem bg-sheet px-4 py-3 text-sm">
          {health.data.alerts.map((a, i) => <li key={i}><span className="font-semibold">{a.kind.replace(/_/g, " ")}</span>: {a.detail}</li>)}
        </ul>
      ))}

      {p && (
        <section className="mb-8">
          <h2 className="text-xl">AI keys</h2>
          {!p.platform_key_configured && <p className="mt-1 text-sm text-problem">Your own Gemini key is not set on the server, so only users&apos; own keys can work.</p>}
          <div className="mt-3 grid gap-2 md:grid-cols-3">
            {POLICIES.map((o) => (
              <label key={o.value} className={`cursor-pointer border p-3 text-sm ${p.key_policy === o.value ? "border-ink bg-sheet" : "border-rule"}`}>
                <span className="flex items-center gap-2 font-semibold">
                  <input type="radio" name="policy" checked={p.key_policy === o.value} onChange={() => saveSettings({ key_policy: o.value })} />
                  {o.label}
                </span>
                <span className="mt-1 block text-slate">{o.detail}</span>
              </label>
            ))}
          </div>
          {p.key_policy !== "own" && (
            <form className="mt-3 flex flex-wrap items-end gap-2 text-sm" onSubmit={(e) => { e.preventDefault(); if (cap.trim()) { saveSettings({ default_cap_usd: Number(cap) }); setCap(""); } }}>
              <label>Daily limit per user on your key, in US$
                <input className="field mt-1 max-w-32" type="number" min="0" max="100" step="0.25" value={cap} onChange={(e) => setCap(e.target.value)} placeholder={String(p.default_cap_usd)} />
              </label>
              <button className="btn btn-quiet">Set</button>
              <span className="text-xs text-slate">Now ${p.default_cap_usd.toFixed(2)} a day. You can change it per user below.</span>
            </form>
          )}
        </section>
      )}

      <SignupSettings />

      {users.data && (
        <section className="mb-8">
          <h2 className="text-xl">Users</h2>
          <div className="mt-2 overflow-x-auto">
            <table className="ledger">
              <thead><tr><th>User</th><th>Gmail</th><th>AI key</th><th>May use your key</th><th>Daily limit (US$)</th><th>Spent today</th><th>Sends a day</th><th>Sent, 24 h</th><th>Failed runs</th><th title="On the test site applications go to the sandbox inbox unless this is ticked">Real sending</th><th></th></tr></thead>
              <tbody>
                {users.data.map((u) => {
                  const o = extra[u.id];
                  return (
                    <tr key={u.id}>
                      <td>{u.email}{u.is_admin && <span className="ml-1 text-xs text-slate">(admin)</span>}{u.status !== "active" && <span className="ml-1 text-xs text-problem">{u.status}</span>}</td>
                      <td>{o?.mail ?? ""}</td>
                      <td className={u.key_source === "none" ? "text-problem" : ""}>{SOURCE[u.key_source]}</td>
                      <td><input type="checkbox" aria-label={`Allow ${u.email} to use your key`} checked={u.platform_key_allowed} disabled={p?.key_policy === "own"}
                        onChange={(e) => patchUser(u.id, { platform_key_allowed: e.target.checked })} /></td>
                      <td>
                        {u.key_source === "own" ? <span className="text-xs text-slate">their choice</span> : (
                          <input className="field w-20 py-1" type="number" min="0" max="100" step="0.25" aria-label={`Daily limit for ${u.email}`}
                            defaultValue={u.platform_cap_usd ?? ""} placeholder={String(u.cap_usd)}
                            onBlur={(e) => {
                              const v = e.target.value.trim();
                              if (v === String(u.platform_cap_usd ?? "")) return;
                              patchUser(u.id, v ? { platform_cap_usd: Number(v) } : { use_default_cap: true });
                            }} />
                        )}
                      </td>
                      <td>${u.spend_today_usd.toFixed(2)}</td>
                      <td><input className="field w-16 py-1" type="number" min="0" max="100" aria-label={`Sends a day for ${u.email}`} defaultValue={u.daily_send_limit}
                        onBlur={(e) => { const v = Number(e.target.value); if (v !== u.daily_send_limit) patchUser(u.id, { daily_send_limit: v }); }} /></td>
                      <td>{o?.sent_24h ?? 0}</td>
                      <td>{o?.failed_runs_24h ?? 0}</td>
                      <td>{u.production ? <span className="text-xs text-slate">always</span> : (
                        <input type="checkbox" aria-label={`Send ${u.email}'s applications to real employers`} checked={u.real_sending}
                          onChange={(e) => {
                            if (e.target.checked && !window.confirm(`Send ${u.email}'s approved applications to the real employers? Until now they went to the sandbox inbox.`)) return;
                            patchUser(u.id, { real_sending: e.target.checked });
                          }} />
                      )}</td>
                      <td className="whitespace-nowrap"><button className="btn btn-quiet" onClick={() => act(() => api(`/api/operator/users/${u.id}/status?status=${u.status === "active" ? "paused" : "active"}`, { method: "POST" }), users.reload, overview.reload)}>
                        {u.status === "active" ? "Pause" : "Resume"}</button>
                        <button className="btn btn-quiet ml-1" title="A one-time link to choose a new password, valid for an hour"
                          onClick={() => act(async () => setReset({ email: u.email, ...(await api<{ link: string }>(`/api/operator/users/${u.id}/reset-link`, { method: "POST" })) }))}>
                          Reset link</button></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {reset && (
            <div className="mt-3 border border-rule bg-sheet p-3 text-sm">
              <p>Password reset link for <strong>{reset.email}</strong>. Send it to them privately; it works once and expires in an hour.</p>
              <p className="mt-2 flex flex-wrap items-center gap-2">
                <input className="field min-w-0 flex-1 font-mono text-xs" readOnly value={reset.link} aria-label="Reset link" onFocus={(e) => e.target.select()} />
                <button className="btn btn-quiet" onClick={() => navigator.clipboard?.writeText(reset.link)}>Copy</button>
                <button className="btn btn-quiet" onClick={() => setReset(null)}>Close</button>
              </p>
            </div>
          )}
        </section>
      )}

      {forms.data && (
        <section className="mb-8">
          <h2 className="text-xl">Web forms, last {forms.data.days} days</h2>
          <p className="mt-1 text-sm">
            Filled {forms.data.filled} · submitted {forms.data.submitted} · could not fill or submit {forms.data.failed}
            {Object.keys(forms.data.by_kind).length > 0 && (
              <span className="text-slate"> ({Object.entries(forms.data.by_kind).map(([k, n]) => `${k} ${n}`).join(", ")})</span>
            )}
          </p>
          {forms.data.attempts.length > 0 && (
            <div className="mt-2 overflow-x-auto">
              <table className="ledger">
                <thead><tr><th>When</th><th>Job</th><th>Site</th><th>Step</th><th>Try</th><th>Why</th></tr></thead>
                <tbody>
                  {forms.data.attempts.map((a, i) => (
                    <tr key={i}>
                      <td className="whitespace-nowrap">{new Date(a.at).toLocaleString()}</td>
                      <td>{a.job || "(job removed)"}<span className="block text-xs text-slate">{a.user}</span></td>
                      <td>{a.url ? <a className="underline" href={a.url} target="_blank" rel="noreferrer">{a.hiring_system || "open"}</a> : a.hiring_system}</td>
                      <td>{a.step}{a.automatic ? " (auto)" : ""}</td>
                      <td>{a.attempt ? `${a.attempt}${a.final ? ", last" : ""}` : ""}</td>
                      <td><span className="font-semibold">{a.kind}</span><span className="block text-xs text-slate">{a.reason}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {requests.data && requests.data.length > 0 && (
        <section className="mb-8">
          <h2 className="text-xl">Employers asking not to be contacted</h2>
          <ul className="mt-2 space-y-2 text-sm">
            {requests.data.map((r) => (
              <li key={r.id} className="flex flex-wrap items-center justify-between gap-2 border border-rule bg-sheet p-3">
                <span>{r.contact_email}{r.company ? `, ${r.company}` : ""}{r.message ? `: ${r.message}` : ""}</span>
                <span className="flex gap-2">
                  <button className="btn" onClick={() => act(() => api(`/api/operator/block-requests/${r.id}?approve=true`, { method: "POST" }), requests.reload)}>Block for everyone</button>
                  <button className="btn btn-quiet" onClick={() => act(() => api(`/api/operator/block-requests/${r.id}?approve=false`, { method: "POST" }), requests.reload)}>Decline</button>
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </Shell>
  );
}
