"use client";

import Link from "next/link";
import { Suspense, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import Shell from "@/components/Shell";
import { Empty, ErrorNote, Postmark, errorText, useLoad } from "@/components/ui";
import { api, fmtDate } from "@/lib/api";
import type { Application, Match } from "@/lib/types";

// The four states a person sees, plus what needs them and what the agent passed on.
const TABS: { key: string; label: string; statuses: string }[] = [
  { key: "log", label: "All", statuses: "" },
  { key: "applied", label: "Applied", statuses: "sent,queued,acknowledged,unconfirmed,dry_run" },
  { key: "replied", label: "Replied", statuses: "in_conversation,offer" },
  { key: "interview", label: "Interview", statuses: "interview" },
  { key: "closed", label: "Closed", statuses: "rejected,closed,no_response,undeliverable" },
  { key: "manual", label: "Needs you", statuses: "manual_apply,form_ready,unconfirmed,failed,awaiting_approval" },
  { key: "skipped", label: "Skipped", statuses: "" },
];

function toCsv(rows: Application[]): string {
  const head = ["Company", "Role", "Applied", "CV", "Score", "Status", "Recipient"];
  const esc = (v: unknown) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  return [head, ...rows.map((r) => [r.company, r.title, r.sent_at || "", r.cv_label, r.score ?? "", r.status, r.recipient || ""])]
    .map((line) => line.map(esc).join(","))
    .join("\n");
}

function Log() {
  const params = useSearchParams();
  const [tab, setTab] = useState(params.get("tab") || "log");
  const [status] = useState(params.get("status") || "");
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const query = useMemo(() => {
    const p = new URLSearchParams();
    const wanted = TABS.find((t) => t.key === tab)?.statuses || status;
    if (wanted) p.set("status", wanted);
    if (q) p.set("q", q);
    return `/api/applications?${p.toString()}`;
  }, [tab, status, q]);
  const apps = useLoad<Application[]>(tab === "skipped" ? null : query);
  const skipped = useLoad<Match[]>(tab === "skipped" ? "/api/matches?decision=skip" : null);

  async function applyAnyway(id: string) {
    setBusy(id);
    setError("");
    try {
      await api(`/api/matches/${id}/apply-anyway`, { method: "POST" });
      skipped.reload();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy("");
    }
  }

  function download() {
    const blob = new Blob([toCsv(apps.data || [])], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "applications.csv";
    a.click();
  }

  return (
    <Shell title="Applications" actions={tab === "log" && <button className="btn btn-quiet" onClick={download}>Download CSV</button>}>
      <div className="mb-4 flex flex-wrap gap-1 border-b border-ink text-sm" role="tablist">
        {TABS.map((t) => (
          <button key={t.key} role="tab" aria-selected={tab === t.key} onClick={() => setTab(t.key)}
            className={`px-3 py-2 ${tab === t.key ? "bg-ink text-white" : "hover:bg-rule"}`}>{t.label}</button>
        ))}
      </div>
      <ErrorNote message={error || apps.error || skipped.error} />
      {tab !== "skipped" && (
        <div className="mb-4 flex flex-wrap gap-3">
          <input className="field max-w-xs" placeholder="Search company or role" aria-label="Search company or role" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      )}
      {tab !== "skipped" && apps.data && (apps.data.length === 0 ? (
        <Empty>{tab === "manual" ? "Nothing needs you." : "Nothing here yet. Applications appear as the agent applies."}</Empty>
      ) : (
        <div className="overflow-x-auto">
          <table className="ledger">
            <thead><tr><th>Company</th><th>Role</th><th>{tab === "manual" ? "Found" : "Date"}</th><th>CV</th><th>Score</th><th>Status</th></tr></thead>
            <tbody>
              {apps.data.map((a) => (
                <tr key={a.id}>
                  <td><Link className="font-semibold underline" href={`/applications/${a.id}`}>{a.company || "Unknown company"}</Link></td>
                  <td>{a.title}{tab === "manual" && a.url && <> <a className="underline" href={a.url} target="_blank" rel="noreferrer">Open form</a></>}</td>
                  <td className="whitespace-nowrap">{fmtDate(a.sent_at || a.created_at)}</td>
                  <td>{a.cv_label}</td>
                  <td>{a.score ?? ""}</td>
                  <td><Postmark status={a.status} />{a.status === "skipped" && <span className="block text-xs text-slate">{a.skip_reason}</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
      {tab === "skipped" && skipped.data && (skipped.data.length === 0 ? (
        <Empty>No postings have been skipped. When the agent passes on a job, the reason shows here.</Empty>
      ) : (
        <div className="overflow-x-auto">
          <table className="ledger">
            <thead><tr><th>Company</th><th>Role</th><th>Score</th><th>Why it was skipped</th><th></th></tr></thead>
            <tbody>
              {skipped.data.map((m) => (
                <tr key={m.id}>
                  <td>{m.company}</td>
                  <td><a className="underline" href={m.url} target="_blank" rel="noreferrer">{m.title}</a></td>
                  <td>{m.total_score ?? ""}</td>
                  <td>
                    {m.reason}
                    {(m.rubric?.suggestions?.length ?? 0) > 0 && (
                      <span className="block text-xs text-slate">
                        Not on your CV: {m.rubric!.suggestions!.join("; ")}. Add one only if you have really done it.
                      </span>
                    )}
                  </td>
                  <td>{m.total_score !== null && m.route === "email" && (
                    <button className="btn btn-quiet" disabled={busy === m.id} onClick={() => applyAnyway(m.id)}>Apply anyway</button>
                  )}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </Shell>
  );
}

export default function Page() {
  return <Suspense><Log /></Suspense>;
}
