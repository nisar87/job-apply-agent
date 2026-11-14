"use client";

import { useState } from "react";
import Shell from "@/components/Shell";
import { ErrorNote, errorText, useLoad } from "@/components/ui";
import { api } from "@/lib/api";

type Me = { email: string; name: string; timezone: string; escalation_email: string | null; style_guide: string; sample_emails: string[] };
type Account = { id: string; address: string; provider: string; status: string };
type Block = { id: string; type: string; value: string; reason: string; removable: boolean };

export default function AccountPage() {
  const me = useLoad<Me>("/api/auth/me");
  const mail = useLoad<Account[]>("/api/mail/account");
  const blocks = useLoad<Block[]>("/api/blocks");
  const [edits, setEdits] = useState<Partial<Me>>({});
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [blockType, setBlockType] = useState("company");
  const [blockValue, setBlockValue] = useState("");
  const m = { ...(me.data || {}), ...edits } as Me;

  async function saveMe() {
    setError("");
    setNote("");
    try {
      await api("/api/auth/me", { method: "PATCH", json: edits });
      setNote("Saved");
      setEdits({});
      me.reload();
    } catch (e) {
      setError(errorText(e));
    }
  }

  async function connectGmail() {
    setError("");
    try {
      const { url } = await api<{ url: string }>("/api/mail/google/start");
      window.location.assign(url);
    } catch (e) {
      setError(errorText(e));
    }
  }

  async function wrap(fn: () => Promise<unknown>, after: () => void) {
    setError("");
    try {
      await fn();
      after();
    } catch (e) {
      setError(errorText(e));
    }
  }

  return (
    <Shell title="Account">
      <ErrorNote message={error || me.error} />
      <h2 className="text-xl">Gmail</h2>
      <p className="mt-1 max-w-prose text-sm text-slate">Applications are sent from your own account. The agent reads only the conversations it started and replies from employers you applied to.</p>
      <ul className="mt-3 space-y-2 text-sm">
        {(mail.data || []).map((a) => (
          <li key={a.id} className="flex flex-wrap items-center justify-between gap-2 border border-rule bg-sheet p-3">
            <span>{a.address} <span className={`postmark ml-2 ${a.status === "connected" ? "text-sent" : "text-problem"}`}>{a.status === "connected" ? "Connected" : "Needs reconnecting"}</span></span>
            <button className="btn btn-quiet" onClick={() => wrap(() => api(`/api/mail/account/${a.id}`, { method: "DELETE" }), mail.reload)}>Disconnect</button>
          </li>
        ))}
      </ul>
      <button className="btn mt-3" onClick={connectGmail}>{mail.data && mail.data.length ? "Reconnect Gmail" : "Connect Gmail"}</button>

      <h2 className="mt-10 text-xl">About you</h2>
      {me.data && (
        <div className="mt-3 grid gap-4 md:grid-cols-2">
          <label className="text-sm">Name on your emails<input className="field mt-1" value={m.name || ""} onChange={(e) => setEdits({ ...edits, name: e.target.value })} /></label>
          <label className="text-sm">Time zone<input className="field mt-1" value={m.timezone || ""} onChange={(e) => setEdits({ ...edits, timezone: e.target.value })} /></label>
          <label className="text-sm md:col-span-2">Send urgent replies (interviews, offers) to
            <input className="field mt-1" type="email" value={m.escalation_email || ""} onChange={(e) => setEdits({ ...edits, escalation_email: e.target.value })} />
          </label>
          <label className="text-sm md:col-span-2">How you write
            <span className="block text-xs text-slate">For example: direct and polite, short sentences, no buzzwords.</span>
            <textarea className="field mt-1 min-h-24" value={m.style_guide || ""} onChange={(e) => setEdits({ ...edits, style_guide: e.target.value })} />
          </label>
        </div>
      )}
      <div className="mt-4 flex items-center gap-3"><button className="btn" onClick={saveMe}>Save</button>{note && <span className="text-sm text-sent">{note}</span>}</div>

      <h2 className="mt-10 text-xl">Never contact</h2>
      <ul className="mt-3 space-y-1 text-sm">
        {(blocks.data || []).map((b) => (
          <li key={b.id} className="flex items-center justify-between border-b border-rule py-1">
            <span>{b.value} <span className="text-slate">({b.type}{b.reason ? `, ${b.reason}` : ""})</span></span>
            {b.removable ? <button className="underline" onClick={() => wrap(() => api(`/api/blocks/${b.id}`, { method: "DELETE" }), blocks.reload)}>Remove</button> : <span className="text-xs text-slate">They asked not to be contacted</span>}
          </li>
        ))}
      </ul>
      <form className="mt-3 flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); wrap(() => api("/api/blocks", { method: "POST", json: { type: blockType, value: blockValue } }), () => { setBlockValue(""); blocks.reload(); }); }}>
        <select className="field max-w-40" value={blockType} onChange={(e) => setBlockType(e.target.value)} aria-label="Kind">
          <option value="company">Company</option><option value="domain">Domain</option><option value="address">Email address</option>
        </select>
        <input className="field max-w-xs" value={blockValue} onChange={(e) => setBlockValue(e.target.value)} placeholder="Acme GmbH" required aria-label="Value" />
        <button className="btn btn-quiet">Add</button>
      </form>

      <h2 className="mt-10 text-xl">Your data</h2>
      <p className="mt-1 max-w-prose text-sm text-slate">Download everything stored about you, or delete your account. Deleting removes your CVs, applications and messages here and withdraws this app&apos;s access to your Gmail. It cannot be undone.</p>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <button className="btn btn-quiet" onClick={() => wrap(async () => {
          const data = await api("/api/account/export");
          const a = document.createElement("a");
          a.href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
          a.download = "job-apply-agent-data.json";
          a.click();
        }, () => undefined)}>Download my data</button>
        <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => {
          e.preventDefault();
          const password = new FormData(e.currentTarget).get("password");
          wrap(() => api("/api/account/delete", { method: "POST", json: { password } }), () => { window.localStorage.removeItem("jaa_token"); window.location.replace("/signup"); });
        }}>
          <label className="text-sm">Password, to confirm deletion<input className="field mt-1" type="password" name="password" required /></label>
          <button className="btn btn-danger">Delete my account</button>
        </form>
      </div>
    </Shell>
  );
}
