"use client";

import { useState } from "react";
import Shell from "@/components/Shell";
import { ErrorNote, errorText, useLoad } from "@/components/ui";
import { api } from "@/lib/api";
import type { CV } from "@/lib/types";

const FACTS: { key: string; label: string; hint?: string }[] = [
  { key: "location", label: "Where you live" },
  { key: "home_country", label: "Country you live in", hint: "Two-letter code or name, e.g. pk or Pakistan. Remote jobs that cannot hire there are skipped" },
  { key: "notice_period", label: "Notice period" },
  { key: "earliest_start", label: "Earliest start date" },
  { key: "salary_expectation", label: "Salary expectation per year", hint: "The agent states this only if you switch that on in a search" },
  { key: "salary_currency", label: "Currency" },
  { key: "willing_to_relocate", label: "Willing to relocate" },
  { key: "willing_to_travel", label: "Willing to travel" },
  { key: "languages", label: "Languages and levels" },
  { key: "phone", label: "Phone number" },
  { key: "interview_availability", label: "When you can take interviews" },
];

function CvRow({ cv, onChange }: { cv: CV; onChange: () => void }) {
  const [keywords, setKeywords] = useState(cv.keywords.join(", "));
  const [label, setLabel] = useState(cv.label);
  const [error, setError] = useState("");
  async function patch(json: unknown) {
    try {
      await api(`/api/cvs/${cv.id}`, { method: "PATCH", json });
      onChange();
    } catch (e) {
      setError(errorText(e));
    }
  }
  return (
    <li className="border border-rule bg-sheet p-4">
      <ErrorNote message={error} />
      <div className="grid gap-3 md:grid-cols-[12rem_1fr_auto] md:items-end">
        <label className="text-sm">Label<input className="field mt-1" value={label} onChange={(e) => setLabel(e.target.value)} /></label>
        <label className="text-sm">Use this CV when the posting mentions
          <input className="field mt-1" placeholder="backend, python, data platform" value={keywords} onChange={(e) => setKeywords(e.target.value)} />
        </label>
        <div className="flex flex-wrap gap-2">
          <button className="btn btn-quiet" onClick={() => patch({ label, keywords: keywords.split(",").map((k) => k.trim()).filter(Boolean) })}>Save</button>
          {cv.is_default ? <span className="postmark text-sent">Default</span> : <button className="btn btn-quiet" onClick={() => patch({ is_default: true })}>Make default</button>}
          <button className="btn btn-quiet" onClick={() => patch({ active: !cv.active })}>{cv.active ? "Stop using" : "Use again"}</button>
        </div>
      </div>
    </li>
  );
}

export default function Cvs() {
  const cvs = useLoad<CV[]>("/api/cvs");
  const facts = useLoad<Record<string, string>>("/api/fact-sheet");
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const current = { ...(facts.data || {}), ...edits } as Record<string, string>;

  async function upload(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const formEl = e.currentTarget;   // read before awaiting: the event is cleared afterwards
    const form = new FormData(formEl);
    setBusy(true);
    setError("");
    try {
      await api("/api/cvs", { method: "POST", body: form });
      formEl.reset();
      cvs.reload();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function saveFacts() {
    setNote("");
    try {
      const { work_authorisation, ...rest } = current as Record<string, unknown>;
      await api("/api/fact-sheet", { method: "PUT", json: { ...rest, ...(work_authorisation ? { work_authorisation } : {}) } });
      setNote("Saved");
      facts.reload();
    } catch (err) {
      setError(errorText(err));
    }
  }

  const active = (cvs.data || []).filter((c) => c.active).length;
  return (
    <Shell title="CVs and facts">
      <ErrorNote message={error || cvs.error || facts.error} />
      <h2 className="text-xl">Your CVs</h2>
      <p className="mt-1 max-w-prose text-sm text-slate">Keep up to four. For each job the agent scores the best-fitting ones and sends the winner. Every application records which CV went out.</p>
      <ul className="mt-4 space-y-3">{(cvs.data || []).map((cv) => <CvRow key={cv.id + cv.is_default + cv.active} cv={cv} onChange={cvs.reload} />)}</ul>
      {active < 4 && (
        <form onSubmit={upload} className="mt-4 grid gap-3 border border-dashed border-rule p-4 md:grid-cols-[1fr_12rem_1fr_auto] md:items-end">
          <label className="text-sm">File (PDF, DOCX or TXT, up to 5 MB)<input className="field mt-1" type="file" name="file" accept=".pdf,.docx,.txt" required /></label>
          <label className="text-sm">Label<input className="field mt-1" name="label" placeholder="Backend" required /></label>
          <label className="text-sm">Keywords<input className="field mt-1" name="keywords" placeholder="backend, python" /></label>
          <button className="btn" disabled={busy}>{busy ? "Reading…" : "Upload CV"}</button>
        </form>
      )}
      <h2 className="mt-10 text-xl">Fact sheet</h2>
      <p className="mt-1 max-w-prose text-sm text-slate">The agent answers routine questions from these facts and your CV. Anything not written here, it does not know and will not claim.</p>
      <div className="mt-4 grid gap-4 md:grid-cols-2">
        {FACTS.map((f) => (
          <label key={f.key} className="text-sm">
            <span className="font-semibold">{f.label}</span>
            {f.hint && <span className="block text-xs text-slate">{f.hint}</span>}
            <input className="field mt-1" value={typeof current[f.key] === "string" ? current[f.key] : ""} onChange={(e) => setEdits((old) => ({ ...old, [f.key]: e.target.value }))} />
          </label>
        ))}
      </div>
      <div className="mt-4 flex items-center gap-3"><button className="btn" onClick={saveFacts}>Save facts</button>{note && <span className="text-sm text-sent">{note}</span>}</div>
    </Shell>
  );
}
