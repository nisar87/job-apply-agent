"use client";

import { Progress } from "@/components/Progress";
import Link from "next/link";
import { useState } from "react";
import Shell from "@/components/Shell";
import { Empty, ErrorNote, errorText, useLoad } from "@/components/ui";
import { api, fmtDate } from "@/lib/api";
import type { Application } from "@/lib/types";

function WhyItFits({ a }: { a: Application }) {
  if (!a.match_summary && !(a.match_strengths || []).length) return null;
  return (
    <details className="mt-3 text-sm">
      <summary className="cursor-pointer text-slate">Why it matched (score {a.score} of 100)</summary>
      {a.match_summary && <p className="mt-1 max-w-prose">{a.match_summary}</p>}
      {(a.match_strengths || []).length > 0 && (
        <ul className="mt-1 list-disc pl-5">{a.match_strengths!.map((s) => <li key={s}>{s}</li>)}</ul>
      )}
    </details>
  );
}

function DraftCard({ a, onDone }: { a: Application; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [full, setFull] = useState(false);

  async function act(path: string, json: unknown = {}) {
    setBusy(true);
    setError("");
    try {
      await api(`/api/applications/${a.id}/${path}`, { method: "POST", json });
      onDone();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  const long = a.body.length > 700;
  return (
    <li className="card p-4 sm:p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-xl">{a.company || "Unknown company"}</h2>
        <span className="text-sm text-slate">Score {a.score} of 100 · expires {fmtDate(a.expires_at)}</span>
      </div>
      <p className="text-sm text-slate">
        {a.title}
        {a.url && <> · <a className="underline" href={a.url} target="_blank" rel="noreferrer">See the job ad</a></>}
      </p>
      <WhyItFits a={a} />
      {a.status === "failed" && <p className="mt-2 text-sm text-problem">Needs fixing before it can be sent: {a.skip_reason}</p>}
      <p className="letter mt-3 max-w-prose whitespace-pre-wrap leading-7">
        <span className="font-semibold">{a.subject}</span>{"\n"}{long && !full ? `${a.body.slice(0, 700)}…` : a.body}
      </p>
      {long && <button className="mt-1 text-sm underline" onClick={() => setFull(!full)}>{full ? "Show less" : "Show the whole email"}</button>}
      {error && <p className="mt-2 text-sm text-problem" role="alert">{error}</p>}
      <div className="mt-4 flex flex-wrap gap-2">
        {a.status === "awaiting_approval" && <button className="btn" disabled={busy} onClick={() => act("approve")}>Approve</button>}
        <Link className="btn btn-quiet" href={`/applications/${a.id}`}>Open and edit</Link>
        <button className="btn btn-quiet" disabled={busy} onClick={() => act("reject", { reason: "rejected by you" })}>Reject</button>
      </div>
      {busy && <Progress label="Working on it…" hint="Sending goes through the safety checks first, so it can take a few seconds." />}
    </li>
  );
}

function OnSiteCard({ a, onDone }: { a: Application; onDone: () => void }) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  async function act(path: string, label: string) {
    setBusy(label);
    setError("");
    try {
      await api(`/api/applications/${a.id}/${path}`, { method: "POST", json: {} });
      onDone();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy("");
    }
  }

  return (
    <li className="card p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-lg">{a.company || "Unknown company"}</h3>
        <span className="text-sm text-slate">Score {a.score} of 100</span>
      </div>
      <p className="text-sm text-slate">{a.title}{a.status === "form_ready" ? " · form filled, waiting for your check" : ""}</p>
      <WhyItFits a={a} />
      {error && <p className="mt-2 text-sm text-problem" role="alert">{error}</p>}
      <div className="mt-3 flex flex-wrap gap-2">
        {a.url && <a className="btn" href={a.url} target="_blank" rel="noreferrer">Open the job</a>}
        {a.status === "manual_apply" && (
          <button className="btn btn-quiet" disabled={!!busy} onClick={() => act("form/prepare", "fill")}>
            {busy === "fill" ? "Filling in the form…" : "Fill the form for me"}
          </button>
        )}
        {a.status === "form_ready" && <Link className="btn btn-quiet" href={`/applications/${a.id}`}>Check the filled form</Link>}
        <button className="btn btn-quiet" disabled={!!busy} onClick={() => act("applied", "applied")}>I applied</button>
        <button className="btn btn-quiet" disabled={!!busy} onClick={() => act("skip", "skip")}>Skip</button>
      </div>
      {busy === "fill" && <Progress label="Filling in the employer's form…" hint="The agent opens the page and fills it from your CV. This takes up to a minute." />}
    </li>
  );
}

export default function Drafts() {
  const drafts = useLoad<Application[]>("/api/applications?status=awaiting_approval,failed");
  const onSite = useLoad<Application[]>("/api/applications?status=manual_apply,form_ready,unconfirmed");
  const reload = () => {
    drafts.reload();
    onSite.reload();
  };
  const loading = !drafts.data && !drafts.error;

  return (
    <Shell title="Drafts" intro="Applications waiting for your approval.">
      <p className="mb-4 max-w-prose text-sm text-slate">
        Emails the agent wrote for you to check. Approve one and it goes out in your next send window.
      </p>
      <ErrorNote message={drafts.error || onSite.error} />
      {loading && <p className="text-sm text-slate">Loading…</p>}
      {drafts.data && drafts.data.length === 0 && <Empty>No drafts are waiting. New ones appear before your next send window.</Empty>}
      <ul className="space-y-6">{(drafts.data || []).map((a) => <DraftCard key={a.id} a={a} onDone={reload} />)}</ul>

      {onSite.data && onSite.data.length > 0 && (
        <section className="mt-10">
          <h2 className="text-xl">Apply on the employer&apos;s site ({onSite.data.length})</h2>
          <p className="mt-1 max-w-prose text-sm text-slate">
            These good matches take applications only through their own website. Open the job and apply there, or let
            the agent fill the form for you to check. Press “I applied” afterwards so replies and cooldowns are tracked.
            Each stays here for 14 days.
          </p>
          <ul className="mt-4 space-y-3">{onSite.data.map((a) => <OnSiteCard key={a.id} a={a} onDone={reload} />)}</ul>
        </section>
      )}
    </Shell>
  );
}
