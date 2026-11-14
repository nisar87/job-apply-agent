"use client";

import { useState } from "react";
import Shell from "@/components/Shell";
import { ErrorNote, errorText, useLoad } from "@/components/ui";
import { api } from "@/lib/api";

type Overview = {
  global_pause: boolean;
  users: { id: string; email: string; status: string; plan: string; mail: string; spend_today_usd: number; failed_runs_24h: number; sent_24h: number }[];
};

export default function Operator() {
  const { data, error, reload } = useLoad<Overview>("/api/operator/overview");
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
