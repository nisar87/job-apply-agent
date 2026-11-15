"use client";

import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import FormReview from "@/components/FormReview";
import Shell from "@/components/Shell";
import { ErrorNote, Postmark, errorText, useLoad } from "@/components/ui";
import { API_BASE, api, fmtDate } from "@/lib/api";
import type { Application } from "@/lib/types";

type Detail = {
  application: Application;
  posting: { title: string; url: string; description: string; fields: Record<string, unknown> };
  match: { total_score: number; reason: string; rubric: { requirements?: { requirement: string; status: string; evidence?: string }[]; gaps?: string[]; suggestions?: string[]; cv_label?: string; all_scores?: { cv: string; total: number }[] } } | null;
  cv_version_id: string | null;
  messages: { id: string; direction: string; sender: string; subject: string; body: string; received_at: string; category: string | null; summary: string }[];
  timeline: { id: string; actor: string; tool: string; reason: string; created_at: string }[];
};

export default function ApplicationPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { data, error, reload } = useLoad<Detail>(id ? `/api/applications/${id}` : null);
  const [subject, setSubject] = useState<string | null>(null);
  const [body, setBody] = useState<string | null>(null);
  const [actionError, setActionError] = useState("");
  const [busy, setBusy] = useState(false);
  const a = data?.application;
  const editable = a && ["awaiting_approval", "failed"].includes(a.status);

  async function act(path: string, json: unknown) {
    setBusy(true);
    setActionError("");
    try {
      await api(`/api/applications/${id}/${path}`, { method: "POST", json });
      setSubject(null);
      setBody(null);
      // Decided: go straight to the next draft waiting, or back to the list when this was the last one.
      const waiting = await api<Application[]>("/api/applications?status=awaiting_approval,failed");
      const next = waiting.find((x) => x.id !== id);
      router.push(next ? `/applications/${next.id}` : "/drafts");
      return;
    } catch (e) {
      setActionError(errorText(e));
    } finally {
      setBusy(false);
    }
    reload();
  }

  return (
    <Shell title={a ? `${a.company || "Unknown company"}` : "Application"}>
      <ErrorNote message={error || actionError} />
      {data && a && (
        <div className="grid gap-8 lg:grid-cols-[1fr_20rem]">
          <div className="min-w-0">
            <p className="text-sm text-slate">
              {a.title} · <a className="underline" href={data.posting.url} target="_blank" rel="noreferrer">Original posting</a>
            </p>
            <p className="mt-2 flex flex-wrap items-center gap-3 text-sm">
              <Postmark status={a.status} />
              {a.sent_at && <span>Sent {fmtDate(a.sent_at)}</span>}
              {a.recipient && <span>to {a.recipient}</span>}
              {a.cv_label && <span>with CV “{a.cv_label}”</span>}
            </p>
            {data.posting.description && (
              <details className="mt-3 text-sm">
                <summary className="cursor-pointer text-slate">Read the job ad</summary>
                <p className="mt-2 max-h-96 max-w-prose overflow-y-auto whitespace-pre-wrap border border-rule bg-sheet p-3">{data.posting.description}</p>
              </details>
            )}
            {a.skip_reason && <p className="mt-2 text-sm text-problem">{a.skip_reason}</p>}
            {a.check_results?.problems && a.check_results.problems.length > 0 && (
              <ul className="mt-2 list-disc pl-5 text-sm text-problem">{a.check_results.problems.map((p) => <li key={p}>{p}</li>)}</ul>
            )}
            {a.route === "form" && id && <FormReview id={id} status={a.status} onChange={reload} />}
            <h2 className="mt-6 text-xl">{editable ? "Draft" : a.route === "form" ? "Cover text" : "Email"}</h2>
            {editable ? (
              <div className="mt-2 space-y-3">
                <input className="field" aria-label="Subject" value={subject ?? a.subject} onChange={(e) => setSubject(e.target.value)} />
                <textarea className="field letter min-h-80 leading-7" aria-label="Email body" value={body ?? a.body} onChange={(e) => setBody(e.target.value)} />
                <div className="flex flex-wrap gap-2">
                  <button className="btn" disabled={busy} onClick={() => act("approve", { subject: subject ?? undefined, body: body ?? undefined })}>
                    {subject !== null || body !== null ? "Save changes and approve" : "Approve"}
                  </button>
                  <button className="btn btn-quiet" disabled={busy} onClick={() => act("reject", { reason: "rejected by you" })}>Reject</button>
                  <button className="btn btn-quiet" disabled={busy} onClick={() => {
                    if (window.confirm(`Never apply to ${a.company || "this company"} again? This cannot be undone from here.`)) {
                      act("reject", { reason: "never this company", never_company: true });
                    }
                  }}>Never this company</button>
                </div>
              </div>
            ) : (
              <article className="letter mt-2 max-w-prose whitespace-pre-wrap border border-rule bg-sheet p-5 leading-7">
                <p className="mb-3 font-semibold">{a.subject}</p>
                {a.body}
              </article>
            )}
            {data.messages.length > 1 && (
              <>
                <h2 className="mt-8 text-xl">Conversation</h2>
                <ol className="mt-2 space-y-3">
                  {data.messages.map((m) => (
                    <li key={m.id} className={`border-l-4 bg-sheet p-3 text-sm ${m.direction === "in" ? "border-sent" : "border-rule"}`}>
                      <p className="text-xs text-slate">{m.direction === "in" ? `From ${m.sender}` : "You"} · {fmtDate(m.received_at)}{m.category ? ` · ${m.category.replace(/_/g, " ")}` : ""}</p>
                      <p className="mt-1 whitespace-pre-wrap">{m.body}</p>
                    </li>
                  ))}
                </ol>
              </>
            )}
          </div>
          <aside className="text-sm">
            {data.match && (
              <section>
                <h2 className="text-xl">Match {data.match.total_score}</h2>
                <p className="mt-1 text-slate">{data.match.reason}</p>
                <ul className="mt-3 space-y-2">
                  {(data.match.rubric.requirements || []).map((r) => (
                    <li key={r.requirement}>
                      <span className={r.status === "met" ? "text-sent" : r.status === "partial" ? "text-waiting" : "text-problem"}>
                        {r.status === "met" ? "Met" : r.status === "partial" ? "Partly" : "Missing"}
                      </span>{" "}{r.requirement}
                      {r.evidence && <span className="block text-xs text-slate">{r.evidence}</span>}
                    </li>
                  ))}
                </ul>
                {(data.match.rubric.suggestions?.length ?? 0) > 0 && (
                  <p className="mt-3 text-xs text-slate">
                    Missing items are only suggestions. If you have done one of them, add it to your CV and the next
                    scores will count it. If not, leave your CV as it is: the search looks for jobs that fit what you have.
                  </p>
                )}
                {data.cv_version_id && (
                  <p className="mt-3"><a className="underline" href="#" onClick={async (e) => {
                    e.preventDefault();
                    const res = await fetch(`${API_BASE}/api/cv-versions/${data.cv_version_id}/file`, { headers: { Authorization: `Bearer ${localStorage.getItem("jaa_token")}` } });
                    const url = URL.createObjectURL(await res.blob());
                    const link = document.createElement("a");
                    link.href = url; link.download = "cv"; link.click();
                  }}>Download the CV that was used</a></p>
                )}
              </section>
            )}
            <section className="mt-8">
              <h2 className="text-xl">What happened, and why</h2>
              <ol className="mt-2 space-y-2">
                {data.timeline.map((t) => (
                  <li key={t.id} className="border-b border-rule pb-2">
                    <span className="text-xs text-slate">{fmtDate(t.created_at)} · {t.actor}</span>
                    <span className="block">{t.tool.replace(/_/g, " ")}</span>
                    {t.reason && <span className="block text-xs text-slate">{t.reason}</span>}
                  </li>
                ))}
              </ol>
            </section>
          </aside>
        </div>
      )}
    </Shell>
  );
}
