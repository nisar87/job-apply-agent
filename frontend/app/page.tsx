"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import FrontDoor from "@/components/FrontDoor";
import Shell from "@/components/Shell";
import { ErrorNote, useLoad } from "@/components/ui";
import { api, fmtDate, getToken } from "@/lib/api";

type Home = {
  status: { state: "working" | "paused" | "stopped" | "cannot_work"; text: string; fix: { text: string; href: string } | null; last_checked_at: string | null };
  try_it_first: boolean;
  needs_you: { kind: string; urgent: boolean; title: string; href: string }[];
  week: { applied: number; replies: number; interviews: number; checked: number };
  latest: { at: string; kind: string; text: string; note: string; href: string }[];
  skipped: { total: number; reasons: { reason: string; count: number }[] };
  funnel: { checked: number; shortlisted: number; pending: number; submitted: number; needs_help: number; failed: number;
    replies: number; interviews: number; today_checked: number; today_submitted: number };
  mail_ok: boolean;
  searches: { name: string; state: string }[];
};

function Stat({ label, value, note, tone }: { label: string; value: number; note?: string; tone?: "good" | "warn" | "bad" }) {
  const color = tone === "good" ? "text-sent" : tone === "warn" ? "text-waiting" : tone === "bad" ? "text-problem" : "text-ink";
  return (
    <div className="rounded-lg border border-rule bg-sheet p-4">
      <p className={`text-3xl font-semibold ${color}`}>{value}</p>
      <p className="mt-1 text-sm">{label}</p>
      {note && <p className="text-xs text-slate">{note}</p>}
    </div>
  );
}

function Dot({ state }: { state: string }) {
  const c = state === "working" ? "bg-sent" : state === "paused" ? "bg-waiting" : "bg-problem";
  return <span aria-hidden className={`mr-2 inline-block h-3 w-3 rounded-full ${c}`} />;
}

function ago(iso: string | null): string {
  if (!iso) return "not yet";
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (minutes < 2) return "just now";
  if (minutes < 90) return `${minutes} minutes ago`;
  const hours = Math.round(minutes / 60);
  return hours < 36 ? `${hours} hours ago` : fmtDate(iso);
}

function HomeScreen() {
  const home = useLoad<Home>("/api/home");
  const [busy, setBusy] = useState(false);
  const h = home.data;

  async function togglePause(pause: boolean) {
    setBusy(true);
    try {
      const searches = await api<{ id: string; state: string }[]>("/api/campaigns");
      for (const c of searches) {
        if (pause && c.state !== "paused") await api(`/api/campaigns/${c.id}`, { method: "PATCH", json: { state: "paused" } });
        if (!pause && c.state === "paused") await api(`/api/campaigns/${c.id}`, { method: "PATCH", json: { state: "active" } });
      }
      home.reload();
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell title="Overview">
      <ErrorNote message={home.error} />
      {h && (
        <>
          <section className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-rule bg-sheet p-5" aria-label="Status">
            <div>
              <p className="letter text-2xl"><Dot state={h.status.state} />{h.status.text}</p>
              <p className="mt-1 text-sm text-slate">
                Gmail: {h.mail_ok ? "connected" : "needs attention"}
                {h.searches.length > 0 && <> · Search: {h.searches.filter((x) => x.state !== "paused").map((x) => x.name).join(", ") || "paused"}</>}
              </p>
              {h.status.state === "working" && <p className="text-sm text-slate">Last looked for jobs {ago(h.status.last_checked_at)}.</p>}
              {h.status.fix && <p className="mt-1 text-sm"><Link className="underline" href={h.status.fix.href}>{h.status.fix.text}</Link></p>}
            </div>
            {h.status.state === "working" && <button className="btn btn-quiet" disabled={busy} onClick={() => togglePause(true)}>Pause</button>}
            {h.status.state === "paused" && !h.status.fix?.href.startsWith("/setup") && (
              <button className="btn" disabled={busy} onClick={() => togglePause(false)}>Resume</button>
            )}
          </section>

          <section className="mt-6" aria-label="Numbers">
            <h2 className="text-xl">What the agent did</h2>
            <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
              <Stat label="Jobs checked" value={h.funnel.checked} note={`${h.funnel.today_checked} in the last 24 hours`} />
              <Stat label="Worth applying to" value={h.funnel.shortlisted} />
              <Stat label="Submitted" value={h.funnel.submitted} note={`${h.funnel.today_submitted} in the last 24 hours`} tone="good" />
              <Stat label="Being prepared" value={h.funnel.pending} />
              <Stat label="Employers replied" value={h.funnel.replies} tone="good" />
              <Stat label="Interviews" value={h.funnel.interviews} tone="good" />
              <Stat label="Need your help" value={h.funnel.needs_help} tone={h.funnel.needs_help ? "warn" : undefined} />
              <Stat label="Failed" value={h.funnel.failed} tone={h.funnel.failed ? "bad" : undefined} />
            </div>
          </section>

          <section className="mt-8" aria-label="Needs you">
            <h2 className="text-xl">Needs you{h.needs_you.length > 0 ? ` (${h.needs_you.length})` : ""}</h2>
            {h.needs_you.length === 0 ? <p className="mt-2 text-sm text-slate">Nothing needs you.</p> : (
              <ul className="mt-2 divide-y divide-rule border-y border-rule">
                {h.needs_you.map((n, i) => (
                  <li key={i} className="flex items-center justify-between gap-3 py-3 text-sm">
                    <span>{n.urgent && <strong className="mr-2 text-problem">Urgent</strong>}{n.title}</span>
                    <Link className="btn btn-quiet shrink-0" href={n.href}>Open</Link>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="mt-8" aria-label="This week">
            {h.try_it_first && <p className="mt-1 text-sm text-slate">Try-it-first is on, so nothing has been sent. Applications show what would have gone out.</p>}
          </section>

          <section className="mt-8" aria-label="Latest">
            <h2 className="text-xl">Activity</h2>
            {h.latest.length === 0 && h.skipped.total === 0 ? <p className="mt-2 text-sm text-slate">Nothing yet. The first results appear after the first search.</p> : (
              <ul className="mt-2 divide-y divide-rule border-y border-rule text-sm">
                {h.latest.map((l, i) => (
                  <li key={i} className="flex flex-wrap gap-x-3 py-2">
                    <span className="w-28 shrink-0 text-slate">{fmtDate(l.at)}</span>
                    <span className="w-16 shrink-0 font-semibold">{l.kind === "applied" ? "Applied" : "Reply"}</span>
                    <Link className="underline" href={l.href}>{l.text}</Link>
                    {l.note && <span className="text-slate">{l.note}</span>}
                  </li>
                ))}
                {h.skipped.total > 0 && (
                  <li className="py-2">
                    <details>
                      <summary className="cursor-pointer"><span className="mr-3 text-slate">Last 24 hours</span><span className="mr-3 font-semibold">Skipped</span>{h.skipped.total} jobs</summary>
                      <ul className="mt-2 list-disc pl-6 text-slate">{h.skipped.reasons.map((r) => <li key={r.reason}>{r.count}: {r.reason}</li>)}</ul>
                    </details>
                  </li>
                )}
              </ul>
            )}
          </section>
        </>
      )}
    </Shell>
  );
}

export default function Page() {
  const [mode, setMode] = useState<"loading" | "out" | "in">("loading");
  // The sign-in token exists only in the browser, so the choice is made after the first render.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => setMode(getToken() ? "in" : "out"), []);
  if (mode === "loading") return null;
  return mode === "in" ? <HomeScreen /> : <FrontDoor />;
}
