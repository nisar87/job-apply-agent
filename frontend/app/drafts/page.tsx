"use client";

import Link from "next/link";
import { useState } from "react";
import Shell from "@/components/Shell";
import { Empty, ErrorNote, errorText, useLoad } from "@/components/ui";
import { api, fmtDate } from "@/lib/api";
import type { Application } from "@/lib/types";

export default function Drafts() {
  const { data, error, reload } = useLoad<Application[]>("/api/applications?status=awaiting_approval,failed");
  const [busy, setBusy] = useState("");
  const [actionError, setActionError] = useState("");

  async function act(id: string, path: string, json: unknown = {}) {
    setBusy(id);
    setActionError("");
    try {
      await api(`/api/applications/${id}/${path}`, { method: "POST", json });
    } catch (e) {
      setActionError(errorText(e));
    } finally {
      setBusy("");
      reload();
    }
  }

  return (
    <Shell title="Drafts">
      <p className="mb-4 max-w-prose text-sm text-slate">
        Each draft was written for one posting from your CV. Approve it to send in your next send window, or open it to edit first.
      </p>
      <ErrorNote message={error || actionError} />
      {data && data.length === 0 && <Empty>No drafts are waiting. New ones appear before your next send window.</Empty>}
      <ul className="space-y-6">
        {(data || []).map((a) => (
          <li key={a.id} className="border border-rule bg-sheet p-5">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-xl">{a.company || "Unknown company"}</h2>
              <span className="text-sm text-slate">Score {a.score} · CV “{a.cv_label}” · expires {fmtDate(a.expires_at)}</span>
            </div>
            <p className="text-sm text-slate">{a.title} · to {a.recipient}</p>
            {a.status === "failed" && <p className="mt-2 text-sm text-problem">Needs fixing before it can be sent: {a.skip_reason}</p>}
            <p className="letter mt-3 max-w-prose whitespace-pre-wrap leading-7">
              <span className="font-semibold">{a.subject}</span>{"\n"}{a.body.length > 700 ? `${a.body.slice(0, 700)}…` : a.body}
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              {a.status === "awaiting_approval" && <button className="btn" disabled={busy === a.id} onClick={() => act(a.id, "approve")}>Approve</button>}
              <Link className="btn btn-quiet" href={`/applications/${a.id}`}>Open and edit</Link>
              <button className="btn btn-quiet" disabled={busy === a.id} onClick={() => act(a.id, "reject", { reason: "rejected by you" })}>Reject</button>
            </div>
          </li>
        ))}
      </ul>
    </Shell>
  );
}
