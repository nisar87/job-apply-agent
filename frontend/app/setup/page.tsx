"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useCountries } from "@/components/CountryPicker";
import Shell from "@/components/Shell";
import { ErrorNote, errorText } from "@/components/ui";
import { api } from "@/lib/api";
import type { CampaignConfig } from "@/lib/types";

type Turn = { role: "user" | "agent"; text: string };
type Reply = {
  reply: string; config: CampaignConfig; campaign_name: string; preferences: string[]; ready: boolean;
  status: { missing: string[] };
};

const MISSING: Record<string, { text: string; href: string }> = {
  cv: { text: "Upload a CV", href: "/cvs" },
  mail: { text: "Connect your job Gmail", href: "/account" },
  escalation_email: { text: "Add an address for urgent replies", href: "/account" },
  fact_sheet: { text: "Fill in the fact sheet", href: "/cvs" },
};

export default function Setup() {
  const router = useRouter();
  const [history, setHistory] = useState<Turn[]>([]);
  const [text, setText] = useState("");
  const [last, setLast] = useState<Reply | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function send(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    const next: Turn[] = [...history, { role: "user", text }];
    setHistory(next);
    setText("");
    setBusy(true);
    setError("");
    try {
      const out = await api<Reply>("/api/setup/turn", { method: "POST", json: { history: next, draft: last?.config } });
      setLast(out);
      setHistory([...next, { role: "agent", text: out.reply }]);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function start(state: "active" | "dry_run") {
    if (!last) return;
    setError("");
    try {
      await api("/api/campaigns", { method: "POST", json: { name: last.campaign_name, config: last.config, preferences: last.preferences, state } });
      router.push("/campaigns");
    } catch (err) {
      setError(errorText(err));
    }
  }

  const { name: countryName } = useCountries();
  const c = last?.config;
  return (
    <Shell title="Setup chat">
      <div className="grid gap-8 lg:grid-cols-[1fr_20rem]">
        <section className="min-w-0">
          <p className="max-w-prose text-sm text-slate">
            Describe your job search the way you would to a person. For example: “Apply to 10 remote backend jobs a day at Austrian companies, posted in the last week, and never the same company twice in 15 days.”
          </p>
          <ol className="mt-4 space-y-3" aria-live="polite">
            {history.map((t, i) => (
              <li key={i} className={`max-w-prose whitespace-pre-wrap p-3 text-sm ${t.role === "user" ? "ml-auto bg-ink text-white" : "letter border border-rule bg-sheet leading-7"}`}>{t.text}</li>
            ))}
            {busy && <li className="text-sm text-slate">Reading your instructions…</li>}
          </ol>
          <ErrorNote message={error} />
          <form onSubmit={send} className="mt-4 flex gap-2">
            <textarea className="field min-h-24" value={text} onChange={(e) => setText(e.target.value)} placeholder="What jobs should I apply to?" aria-label="Your instruction" />
            <button className="btn self-end" disabled={busy}>Send</button>
          </form>
        </section>
        <aside className="text-sm">
          <h2 className="text-xl">What I understood</h2>
          {!c ? <p className="mt-2 text-slate">Your settings appear here as you describe them.</p> : (
            <dl className="mt-2 space-y-2">
              {[
                ["Looking for", c.keywords.join(", ") || "not said yet"],
                ["Countries", c.countries.length ? c.countries.map(countryName).join(", ") : "anywhere"],
                ...(c.countries.length && !c.remote_outside_countries ? [["Remote elsewhere", "no"]] : []),
                ["Work mode", c.work_modes.join(", ") || "any"],
                ["Per day", String(c.daily_quota)],
                ["Send window", `${c.send_window.start} to ${c.send_window.end}`],
                ["Posted within", c.max_posting_age_days ? `${c.max_posting_age_days} days` : "any age"],
                ["Match threshold", String(c.match_threshold)],
                ["Same company again", `after ${c.company_cooldown_days} days`],
              ].map(([k, v]) => (
                <div key={k} className="flex justify-between gap-3 border-b border-rule pb-1"><dt className="text-slate">{k}</dt><dd className="text-right">{v}</dd></div>
              ))}
            </dl>
          )}
          {last && last.preferences.length > 0 && (
            <>
              <h3 className="mt-5 font-semibold">Standing instructions</h3>
              <ul className="mt-1 list-disc pl-5">{last.preferences.map((p) => <li key={p}>{p}</li>)}</ul>
            </>
          )}
          {last && last.status.missing.length > 0 && (
            <>
              <h3 className="mt-5 font-semibold">Still needed</h3>
              <ul className="mt-1 list-disc pl-5">{last.status.missing.map((m) => <li key={m}><Link className="underline" href={MISSING[m]?.href || "/"}>{MISSING[m]?.text || m}</Link></li>)}</ul>
            </>
          )}
          {c && c.keywords.length > 0 && (
            <div className="mt-6 space-y-2">
              <button className="btn w-full justify-center" disabled={!last?.ready} onClick={() => start("active")}>Start this search</button>
              <button className="btn btn-quiet w-full justify-center" onClick={() => start("dry_run")}>Start as a dry run</button>
              <p className="text-xs text-slate">Every draft waits for your approval until you change that in the search settings.</p>
            </div>
          )}
        </aside>
      </div>
    </Shell>
  );
}
