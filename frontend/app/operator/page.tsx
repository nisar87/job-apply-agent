"use client";

import { useState } from "react";
import Shell from "@/components/Shell";
import { ErrorNote, errorText, useLoad } from "@/components/ui";
import { api } from "@/lib/api";

type Overview = {
  global_pause: boolean;
  users: { id: string; email: string; status: string; plan: string; mail: string; spend_today_usd: number; failed_runs_24h: number; sent_24h: number }[];
};

type Health = { ok: boolean; alerts: { kind: string; detail: string }[] };
type BlockReq = { id: string; contact_email: string; company: string; message: string };

export default function Operator() {
  const { data, error, reload } = useLoad<Overview>("/api/operator/overview");
  const health = useLoad<Health>("/api/operator/health");
  const requests = useLoad<BlockReq[]>("/api/operator/block-requests");
  const [actionError, setActionError] = useState("");
  async function post(path: string) {
    setActionError("");
    try {
      await api(path, { method: "POST" });
      reload();
    } catch (e) {
      setActionError(errorText(e));
    }
  }
  return (
    <Shell title="Operator console">
      <ErrorNote message={error ? "This page is for the product operator." : actionError} />
      {data && (
        <>
          <p className="mb-4 flex items-center gap-3 text-sm">
            {data.global_pause ? <span className="postmark text-problem">All sending is paused</span> : <span className="postmark text-sent">Sending is on</span>}
            <button className={`btn ${data.global_pause ? "" : "btn-danger"}`} onClick={() => post(`/api/operator/global-pause?on=${!data.global_pause}`)}>
              {data.global_pause ? "Resume sending for everyone" : "Pause sending for everyone"}
            </button>
          </p>
          {health.data && (health.data.ok ? <p className="mb-4 text-sm text-sent">No open alerts.</p> : (
            <ul className="mb-4 border-l-4 border-problem bg-sheet px-4 py-3 text-sm">
              {health.data.alerts.map((a, i) => <li key={i}><span className="font-semibold">{a.kind.replace(/_/g, " ")}</span>: {a.detail}</li>)}
            </ul>
          ))}
          {requests.data && requests.data.length > 0 && (
            <section className="mb-6">
              <h2 className="text-xl">Employers asking not to be contacted</h2>
              <ul className="mt-2 space-y-2 text-sm">
                {requests.data.map((r) => (
                  <li key={r.id} className="flex flex-wrap items-center justify-between gap-2 border border-rule bg-sheet p-3">
                    <span>{r.contact_email}{r.company ? `, ${r.company}` : ""}{r.message ? `: ${r.message}` : ""}</span>
                    <span className="flex gap-2">
                      <button className="btn" onClick={async () => { await post(`/api/operator/block-requests/${r.id}?approve=true`); requests.reload(); }}>Block for everyone</button>
                      <button className="btn btn-quiet" onClick={async () => { await post(`/api/operator/block-requests/${r.id}?approve=false`); requests.reload(); }}>Decline</button>
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )}
          <div className="overflow-x-auto">
            <table className="ledger">
              <thead><tr><th>User</th><th>Account</th><th>Gmail</th><th>Sent, 24 h</th><th>Failed runs, 24 h</th><th>Model spend today</th><th></th></tr></thead>
              <tbody>
                {data.users.map((u) => (
                  <tr key={u.id}>
                    <td>{u.email}</td><td>{u.status}</td><td>{u.mail}</td><td>{u.sent_24h}</td><td>{u.failed_runs_24h}</td>
                    <td>${u.spend_today_usd.toFixed(2)}</td>
                    <td><button className="btn btn-quiet" onClick={() => post(`/api/operator/users/${u.id}/status?status=${u.status === "active" ? "paused" : "active"}`)}>{u.status === "active" ? "Pause" : "Resume"}</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Shell>
  );
}
