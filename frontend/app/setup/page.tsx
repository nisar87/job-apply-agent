"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import JobGmail from "@/components/JobGmail";
import MainEmail from "@/components/MainEmail";
import ModelKey from "@/components/ModelKey";
import Shell from "@/components/Shell";
import { ErrorNote, errorText, useLoad } from "@/components/ui";
import { api } from "@/lib/api";
import type { CV, CampaignConfig } from "@/lib/types";

type Status = { has_cv: boolean; mail_connected: boolean; escalation_email: string | null };
type Reply = { needs?: string[]; reply: string; config: CampaignConfig; campaign_name: string; preferences: string[]; ready: boolean };

// One box. Say what you want; the agent reads the CV once, plans the search and starts.
export default function Setup() {
  const router = useRouter();
  const status = useLoad<Status>("/api/setup/status");
  const cvs = useLoad<CV[]>("/api/cvs");
  const key = useLoad<{ source: string }>("/api/account/model-key");
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [answer, setAnswer] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const hasCv = !!(cvs.data || []).some((c) => c.active) || !!file;
  const keyReady = !!key.data && key.data.source !== "none";
  const mailReady = !!status.data?.mail_connected && !!status.data?.escalation_email;

  async function go(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setAnswer("");
    try {
      if (file) {
        const form = new FormData();
        form.set("file", file);
        form.set("label", "My CV");
        const existing = (cvs.data || []).find((c) => c.is_default) || (cvs.data || [])[0];
        if (existing) { form.set("label", existing.label); form.set("cv_id", existing.id); }   // a new file replaces the old one
        await api("/api/cvs", { method: "POST", body: form });
        setFile(null);
        cvs.reload();
        status.reload();
      }
      const wish = text.trim() || "Find jobs that fit my CV and level. Remote or with relocation, anywhere in the world.";
      const plan = await api<Reply>("/api/setup/turn", { method: "POST", json: { history: [{ role: "user", text: wish }] } });
      if (plan.needs?.length) {
        setAnswer(plan.reply);
        return;
      }
      const config = { ...plan.config, daily_quota: Math.min(plan.config.daily_quota || 10, 10) };
      // A new description replaces the old searches, so two never run side by side.
      const old = await api<{ id: string; state: string }[]>("/api/campaigns");
      for (const c of old) if (c.state !== "paused") await api(`/api/campaigns/${c.id}`, { method: "PATCH", json: { state: "paused" } });
      await api("/api/campaigns", { method: "POST", json: { name: plan.campaign_name, config, preferences: plan.preferences, state: "active" } });
      router.push("/");
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell title="What should I do for you?">
      <ErrorNote message={error} />
      {key.data && !keyReady && (
        <div className="card mt-4 max-w-2xl p-4">
          <p className="text-sm font-semibold">First, add your own AI key. The agent runs on it.</p>
          <ModelKey onChanged={key.reload} />
        </div>
      )}
      <form onSubmit={go} className="mt-4 max-w-2xl">
        <label className="sr-only" htmlFor="wish">Describe what you want</label>
        <textarea id="wish" className="field min-h-56 w-full text-base" value={text} onChange={(e) => setText(e.target.value)}
          placeholder="Describe what you want, like you would tell a person. For example: remote or relocation jobs in all of Europe, Canada and the US, Austria first, never Pakistan, 5 to 8 a day, nothing older than 20 days, same company again only after 15 days, read my replies." />
        {answer && <p className="mt-3 border-l-4 border-rule bg-sheet px-3 py-2 text-sm">{answer}</p>}
        <div className="mt-3 flex flex-wrap items-center gap-4">
          {!(cvs.data || []).some((c) => c.active) && (
            <label className="text-sm">Attach your CV (PDF, DOCX or TXT)
              <input className="field mt-1" type="file" accept=".pdf,.docx,.txt" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            </label>
          )}
          <button className="btn ml-auto" disabled={busy || !hasCv || !mailReady || !keyReady}>{busy ? "Working…" : "Start"}</button>
        </div>
      </form>
      {!mailReady && status.data && (
        <div className="mt-6 max-w-2xl border-t border-rule pt-4">
          <p className="text-sm text-slate">One thing first: connect the Gmail the agent applies from, and your main email for reports.</p>
          {!status.data.mail_connected && <JobGmail onConnected={status.reload} />}
          <div className="mt-4"><MainEmail onSaved={status.reload} /></div>
        </div>
      )}
    </Shell>
  );
}
