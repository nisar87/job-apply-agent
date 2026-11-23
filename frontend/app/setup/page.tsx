"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import JobGmail from "@/components/JobGmail";
import MainEmail from "@/components/MainEmail";
import Shell from "@/components/Shell";
import { ErrorNote, errorText, useLoad } from "@/components/ui";
import { api } from "@/lib/api";
import type { CV, CampaignConfig } from "@/lib/types";

type Status = { has_cv: boolean; mail_connected: boolean; escalation_email: string | null };
type Profile = {
  name?: string; location?: string; total_years_experience?: number; level?: string; role_families?: string[];
  skills?: { name: string }[]; languages?: { name: string; level?: string }[]; roles?: { title: string }[];
};
type Reply = { needs?: string[]; reply: string; config: CampaignConfig; campaign_name: string; preferences: string[]; ready: boolean };

const STEP_TITLES = ["Your CV", "Your Gmail", "What you want", "Review"];

function Understood({ cvId }: { cvId: string }) {
  const profile = useLoad<{ profile: Profile }>(`/api/cvs/${cvId}/profile`);
  const p = profile.data?.profile;
  if (!p) return <p className="mt-4 text-sm text-slate">Reading your CV…</p>;
  const rows: [string, string][] = [
    ["Name", p.name || "not found"],
    ["Based in", p.location || "not found"],
    ["Experience", p.total_years_experience ? `${Math.round(p.total_years_experience)} years` : "not found"],
    ["Work you do", p.roles?.[0]?.title || [p.level, ...(p.role_families || [])].filter(Boolean).join(" ") || "not found"],
    ["Main skills", (p.skills || []).slice(0, 8).map((s) => s.name).join(", ") || "not found"],
    ["Languages", (p.languages || []).map((l) => l.name).join(", ") || "not found"],
  ];
  return (
    <section className="mt-6" aria-label="What I understood">
      <h3 className="text-xl">Here is what I understood</h3>
      <dl className="mt-2 max-w-xl text-sm">
        {rows.map(([k, v]) => (
          <div key={k} className="flex justify-between gap-4 border-b border-rule py-1"><dt className="text-slate">{k}</dt><dd className="text-right">{v}</dd></div>
        ))}
      </dl>
      <p className="mt-2 text-xs text-slate">Something wrong? Carry on; you can correct it any time under Settings, Your CV and facts.</p>
    </section>
  );
}

export default function Setup() {
  const router = useRouter();
  const status = useLoad<Status>("/api/setup/status");
  const cvs = useLoad<CV[]>("/api/cvs");
  const [step, setStep] = useState(0);
  const [started, setStarted] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [wish, setWish] = useState("");
  const [plan, setPlan] = useState<Reply | null>(null);
  const [about, setAbout] = useState({ salary: "", start: "", phone: "" });
  const [tryFirst, setTryFirst] = useState(true);
  const facts = useLoad<Record<string, unknown>>("/api/fact-sheet");

  // Resume where the person left off: the first step that is not done yet.
  useEffect(() => {
    if (started || !status.data || !cvs.data) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setStarted(true);
    setStep(!status.data.has_cv ? 0 : !status.data.mail_connected || !status.data.escalation_email ? 1 : 2);
  }, [status.data, cvs.data, started]);
  useEffect(() => {
    const f = facts.data;
    if (!f) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setAbout((a) => ({
      salary: a.salary || (f.salary_expectation ? `${f.salary_expectation}${f.salary_currency ? " " + f.salary_currency : ""}` : ""),
      start: a.start || String(f.earliest_start || f.notice_period || ""), phone: a.phone || String(f.phone || ""),
    }));
  }, [facts.data]);

  // Nothing to type: the agent plans the search from the CV. The box only changes that plan.
  useEffect(() => {
    if (step !== 2 || plan || busy || wish.trim()) return;
    void readWish(undefined, FROM_CV);
  }, [step]); // eslint-disable-line react-hooks/exhaustive-deps

  const cv = (cvs.data || []).find((c) => c.active);

  async function upload(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const formEl = e.currentTarget;
    const form = new FormData(formEl);
    form.set("label", "My CV");
    setBusy(true);
    setError("");
    try {
      await api("/api/cvs", { method: "POST", body: form });
      cvs.reload();
      status.reload();
    } catch (err) {
      setError(`I could not read that file: ${errorText(err)} Please try another file (PDF, DOCX or TXT, up to 5 MB).`);
    } finally {
      setBusy(false);
    }
  }

  const FROM_CV = "Find jobs that fit my CV and level. Remote or with relocation, anywhere in the world.";
  async function readWish(e?: React.FormEvent, text?: string) {
    e?.preventDefault();
    setBusy(true);
    setError("");
    try {
      const out = await api<Reply>("/api/setup/turn", { method: "POST", json: { history: [{ role: "user", text: text ?? wish }], draft: text ? undefined : plan?.config } });
      setPlan(out);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function saveStep3() {
    setBusy(true);
    setError("");
    try {
      const current = await api<Record<string, unknown>>("/api/fact-sheet");
      await api("/api/fact-sheet", { method: "PUT", json: {
        ...current,
        ...(about.salary.trim() ? { salary_expectation: about.salary.trim() } : {}),
        ...(about.start.trim() ? { earliest_start: about.start.trim() } : {}),
        ...(about.phone.trim() ? { phone: about.phone.trim() } : {}),
      } });
      setStep(3);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function start() {
    if (!plan) return;
    setBusy(true);
    setError("");
    try {
      const config = { ...plan.config, daily_quota: Math.min(plan.config.daily_quota || 10, 10) };
      await api("/api/campaigns", { method: "POST", json: { name: plan.campaign_name, config, preferences: plan.preferences, state: tryFirst ? "dry_run" : "active" } });
      router.push("/");
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell title="Set up your agent">
      <p className="text-sm text-slate" aria-live="polite">Step {step + 1} of 4 · {STEP_TITLES[step]}</p>
      <ErrorNote message={error} />

      {step === 0 && (
        <section className="mt-4 max-w-xl">
          <h2 className="text-xl">Your CV</h2>
          {!cv ? (
            <form onSubmit={upload} className="mt-3 border border-dashed border-rule p-6">
              <label className="text-sm">Choose your CV (PDF, DOCX or TXT, up to 5 MB)
                <input className="field mt-2" type="file" name="file" accept=".pdf,.docx,.txt" required />
              </label>
              <button className="btn mt-4" disabled={busy}>{busy ? "Reading your CV…" : "Read my CV"}</button>
            </form>
          ) : (
            <>
              <Understood cvId={cv.id} />
              <button className="btn mt-6" onClick={() => setStep(1)}>Looks right</button>
            </>
          )}
        </section>
      )}

      {step === 1 && (
        <section className="mt-4 max-w-xl">
          <h2 className="text-xl">Your Gmail</h2>
          <p className="mt-1 text-sm text-slate">Applications go out from this address and replies come back to it. Use an address kept for job hunting.</p>
          <JobGmail onConnected={status.reload} />
          <div className="mt-6"><MainEmail onSaved={status.reload} /></div>
          <div className="mt-6 flex gap-2">
            <button className="btn btn-quiet" onClick={() => setStep(0)}>Back</button>
            <button className="btn" disabled={!status.data?.mail_connected || !status.data?.escalation_email}
              onClick={() => setStep(2)}>Continue</button>
            {status.data?.mail_connected && !status.data?.escalation_email && <span className="self-center text-sm text-slate">Add your main email above to continue.</span>}
          </div>
        </section>
      )}

      {step === 2 && (
        <section className="mt-4 max-w-xl">
          <h2 className="text-xl">What you want</h2>
          <form onSubmit={(e) => readWish(e)}>
            <label className="text-sm">I planned the search from your CV (see below). Want something different? Say it in plain words: where, how many a day, what to avoid. Otherwise just continue.
              <textarea className="field mt-1 min-h-28" value={wish} onChange={(e) => setWish(e.target.value)} required
                placeholder="Remote or relocation jobs in Europe, Canada and the US, Austria first, not in Pakistan, 5 to 8 a day." />
            </label>
            <button className="btn btn-quiet mt-2" disabled={busy || !wish.trim()}>{busy ? "Reading…" : plan ? "Change the plan" : "Read this"}</button>
          </form>
          {plan && <p className="mt-3 border-l-4 border-rule bg-sheet px-3 py-2 text-sm">{plan.reply}</p>}
          {plan?.needs?.includes("where") && <p className="mt-3 text-sm text-problem">Add where to look to your text, then press &quot;Change the plan&quot;.</p>}
          <div className="mt-6 max-w-xl">
            <p className="text-sm font-semibold">About you, once (employers ask for these)</p>
            <div className="mt-2 grid gap-3 sm:grid-cols-3">
              <label className="text-sm">Expected salary a year
                <input className="field mt-1" value={about.salary} onChange={(e) => setAbout({ ...about, salary: e.target.value })} placeholder="60000 EUR" />
              </label>
              <label className="text-sm">Earliest start or notice
                <input className="field mt-1" value={about.start} onChange={(e) => setAbout({ ...about, start: e.target.value })} placeholder="2 weeks" />
              </label>
              <label className="text-sm">Phone (optional)
                <input className="field mt-1" value={about.phone} onChange={(e) => setAbout({ ...about, phone: e.target.value })} placeholder="+43…" />
              </label>
            </div>
            <p className="mt-1 text-xs text-slate">Leave any empty: the agent never makes these up. It asks you only if an employer needs one.</p>
          </div>
          <div className="mt-6 flex gap-2">
            <button className="btn btn-quiet" onClick={() => setStep(1)}>Back</button>
            <button className="btn" disabled={busy || !plan || !!plan.needs?.length} onClick={saveStep3}>Continue</button>
          </div>
        </section>
      )}

      {step === 3 && plan && (
        <section className="mt-4 max-w-xl">
          <h2 className="text-xl">Review</h2>
          <p className="mt-2 text-sm font-semibold">Your agent will</p>
          <ul className="list-disc pl-5 text-sm">
            <li>look for new jobs every few hours</li>
            <li>apply to up to 10 a day that fit your CV</li>
            <li>answer routine questions from employers</li>
          </ul>
          <p className="mt-3 text-sm font-semibold">It will ask you about</p>
          <ul className="list-disc pl-5 text-sm">
            <li>interview times, salary and offers</li>
            <li>anything it cannot answer from your CV</li>
          </ul>
          <label className="mt-5 flex items-start gap-2 text-sm">
            <input type="checkbox" className="mt-1" checked={tryFirst} onChange={(e) => setTryFirst(e.target.checked)} />
            <span><strong>Try it first</strong>: prepare everything, send nothing. You can switch this off in Settings once you have seen what it would send.</span>
          </label>
          <div className="mt-6 flex gap-2">
            <button className="btn btn-quiet" onClick={() => setStep(2)}>Back</button>
            <button className="btn" disabled={busy} onClick={start}>{busy ? "Starting…" : "Start my agent"}</button>
          </div>
        </section>
      )}
    </Shell>
  );
}
