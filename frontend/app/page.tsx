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
};

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
    <Shell title="Home">
      <ErrorNote message={home.error} />
      {h && (
        <>
          <section className="flex flex-wrap items-center justify-between gap-3 border-b border-rule pb-5" aria-label="Status">
            <div>
              <p className="letter text-2xl">{h.status.text}</p>
              {h.status.state === "working" && <p className="text-sm text-slate">Last looked for jobs {ago(h.status.last_checked_at)}.</p>}
              {h.status.fix && <p className="mt-1 text-sm"><Link className="underline" href={h.status.fix.href}>{h.status.fix.text}</Link></p>}
            </div>
            {h.status.state === "working" && <button className="btn btn-quiet" disabled={busy} onClick={() => togglePause(true)}>Pause</button>}
            {h.status.state === "paused" && !h.status.fix?.href.startsWith("/setup") && (
              <button className="btn" disabled={busy} onClick={() => togglePause(false)}>Resume</button>
            )}
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
            <h2 className="text-xl">This week</h2>
            <p className="mt-2 text-sm">
              <strong>{h.week.applied}</strong> applied · <strong>{h.week.replies}</strong> {h.week.replies === 1 ? "reply" : "replies"} ·{" "}
              <strong>{h.week.interviews}</strong> {h.week.interviews === 1 ? "interview" : "interviews"} · <strong>{h.week.checked}</strong> jobs checked
            </p>
            {h.try_it_first && <p className="mt-1 text-sm text-slate">Try-it-first is on, so nothing has been sent. Applications show what would have gone out.</p>}
          </section>

          <section className="mt-8" aria-label="Latest">
            <h2 className="text-xl">Latest</h2>
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
  useEffect(() => setMode(getToken() ? "in" : "out"), []);
  if (mode === "loading") return null;
  return mode === "in" ? <HomeScreen /> : <FrontDoor />;
}
