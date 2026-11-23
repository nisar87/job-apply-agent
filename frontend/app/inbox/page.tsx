"use client";

import Link from "next/link";
import { useState } from "react";
import Shell from "@/components/Shell";
import { Empty, ErrorNote, errorText, useLoad } from "@/components/ui";
import { api, fmtDate } from "@/lib/api";

type Esc = {
  id: string; application_id: string; company: string; title: string; reason: string; urgency: string;
  suggested_reply: string; created_at: string;
  message: { sender: string; subject: string; body: string; received_at: string; summary: string } | null;
};
type Note = { id: string; kind: string; title: string; body: string; link: string | null; read: boolean; created_at: string };

function Item({ esc, onDone }: { esc: Esc; onDone: () => void }) {
  const [body, setBody] = useState(esc.suggested_reply);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function act(path: string, json?: unknown) {
    setBusy(true);
    setError("");
    try {
      await api(`/api/escalations/${esc.id}/${path}`, { method: "POST", json });
      onDone();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  }

  return (
    <li className={`border-l-4 bg-sheet p-5 ${esc.urgency === "urgent" ? "border-problem" : "border-waiting"}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-xl">{esc.company || "An employer"}</h2>
        <span className="text-sm text-slate">{esc.urgency === "urgent" ? "Urgent, " : ""}{fmtDate(esc.created_at)}</span>
      </div>
      <p className="text-sm text-slate">{esc.title}. {esc.reason} <Link className="underline" href={`/applications/${esc.application_id}`}>See the whole conversation</Link></p>
      {esc.message && (
        <blockquote className="letter mt-3 max-w-prose whitespace-pre-wrap border-l-2 border-rule pl-4 leading-7">
          <span className="block text-xs text-slate">From {esc.message.sender}</span>
          {esc.message.body}
        </blockquote>
      )}
      <label className="mt-4 block text-sm font-semibold">Your reply
        <span className="block text-xs font-normal text-slate">Suggested by the agent. It is sent from your Gmail only when you press send.</span>
        <textarea className="field letter mt-1 min-h-32 leading-7" value={body} onChange={(e) => setBody(e.target.value)} />
      </label>
      <ErrorNote message={error} />
      <div className="mt-3 flex flex-wrap gap-2">
        <button className="btn" disabled={busy || !body.trim()} onClick={() => act("answer", { body })}>Send reply</button>
        <button className="btn btn-quiet" disabled={busy} onClick={() => act("dismiss?replied_myself=true")}>I replied myself</button>
        <button className="btn btn-quiet" disabled={busy} onClick={() => act("dismiss")}>Dismiss</button>
      </div>
    </li>
  );
}

export default function Inbox() {
  const escs = useLoad<Esc[]>("/api/escalations");
  const notes = useLoad<Note[]>("/api/notifications?unread_only=true");
  const [syncing, setSyncing] = useState(false);

  async function sync() {
    setSyncing(true);
    await api("/api/inbox/sync", { method: "POST" }).catch(() => undefined);
    setSyncing(false);
    escs.reload();
    notes.reload();
  }
  async function read(id: string) {
    await api(`/api/notifications/${id}/read`, { method: "POST" }).catch(() => undefined);
    notes.reload();
  }
  const other = (notes.data || []).filter((n) => !n.kind.startsWith("escalation"));

  return (
    <Shell title="Replies" intro="Employer replies that need you. Everything routine is answered by the agent." actions={<button className="btn btn-quiet" disabled={syncing} onClick={sync}>{syncing ? "Checking…" : "Check for replies now"}</button>}>
      <ErrorNote message={escs.error} />
      {escs.data && escs.data.length === 0 && <Empty>No reply is waiting for you. Interview invitations, offers and questions the agent cannot answer appear here.</Empty>}
      <ul className="space-y-6">{(escs.data || []).map((e) => <Item key={e.id} esc={e} onDone={escs.reload} />)}</ul>
      {other.length > 0 && (
        <>
          <h2 className="mt-10 text-xl">Notices</h2>
          <ul className="mt-3 space-y-2">
            {other.map((n) => (
              <li key={n.id} className="card p-3 text-sm">
                <p className="font-semibold">{n.title}</p>
                {n.body && <p className="mt-1 whitespace-pre-wrap">{n.body}</p>}
                <p className="mt-2 flex gap-4 text-xs text-slate">
                  <span>{fmtDate(n.created_at)}</span>
                  {n.link && <Link className="underline" href={n.link}>Open</Link>}
                  <button className="underline" onClick={() => read(n.id)}>Mark as read</button>
                </p>
              </li>
            ))}
          </ul>
        </>
      )}
    </Shell>
  );
}
