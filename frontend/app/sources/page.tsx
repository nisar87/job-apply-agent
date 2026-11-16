"use client";

import Shell from "@/components/Shell";
import { Empty, ErrorNote, useLoad } from "@/components/ui";
import { fmtDate } from "@/lib/api";

type Site = {
  provider: string; slug: string; kind: string; name: string; url: string; enabled: boolean;
  last_checked_at: string | null; last_ok_at: string | null; last_matches: number; failures: number;
  last_error: string; learned_from: string;
};

const BOARD_NAMES: Record<string, string> = {
  arbeitnow: "Arbeitnow", arbeitsagentur: "Bundesagentur für Arbeit", adzuna: "Adzuna", remotive: "Remotive", jobicy: "Jobicy", himalayas: "Himalayas",
  weworkremotely: "We Work Remotely", remoteok: "Remote OK", workingnomads: "Working Nomads",
};

function status(s: Site): { text: string; tone: string } {
  if (!s.enabled) return { text: s.failures ? "Paused after errors" : "Paused", tone: "text-problem" };
  if (!s.last_checked_at) return { text: "Not checked yet", tone: "text-slate" };
  if (s.failures) return { text: `Failing (${s.failures})`, tone: "text-waiting" };
  return { text: "Working", tone: "text-sent" };
}

export default function Sources() {
  const { data, error } = useLoad<Site[]>("/api/job-sites");
  const boards = (data || []).filter((s) => s.kind === "board");
  const companies = (data || []).filter((s) => s.kind === "company");
  return (
    <Shell title="Where we look">
      <p className="max-w-prose text-sm text-slate">
        Every place the agent searches. Job boards are checked for each search; company career pages are learned
        automatically from the jobs it reads and checked twice a day. Jobs are always applied to on the original site.
      </p>
      <ErrorNote message={error} />
      {!data && !error && <p className="mt-4 text-sm text-slate">Loading…</p>}
      <h2 className="mt-6 text-xl">Job boards ({boards.length})</h2>
      {data && boards.length === 0 ? <Empty>No board has been checked yet. The first search fills this in.</Empty> : (
        <ul className="mt-3 grid gap-3 sm:grid-cols-2">
          {boards.map((s) => {
            const st = status(s);
            return (
              <li key={s.provider} className="border border-rule bg-sheet p-3 text-sm">
                <div className="flex items-center justify-between gap-2">
                  <a className="font-semibold underline" href={s.url} target="_blank" rel="noreferrer">{BOARD_NAMES[s.provider] || s.provider}</a>
                  <span className={`text-xs ${st.tone}`}>{st.text}</span>
                </div>
                <p className="mt-1 text-xs text-slate">
                  {s.last_ok_at ? `Last good check ${fmtDate(s.last_ok_at)}, ${s.last_matches} matching job${s.last_matches === 1 ? "" : "s"}` : "No successful check yet"}
                </p>
                {s.last_error && <p className="mt-1 text-xs text-problem">{s.last_error}</p>}
              </li>
            );
          })}
        </ul>
      )}
      <h2 className="mt-8 text-xl">Company career pages ({companies.length})</h2>
      <p className="mt-1 max-w-prose text-sm text-slate">Found from links in job ads (Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee, Personio, Teamtailor).</p>
      {data && companies.length === 0 ? <Empty>None learned yet. They appear as the agent reads job ads that link to a company&apos;s own careers page.</Empty> : (
        <ul className="mt-3 divide-y divide-rule border border-rule bg-sheet text-sm">
          {companies.map((s) => {
            const st = status(s);
            return (
              <li key={`${s.provider}:${s.slug}`} className="flex flex-wrap items-center justify-between gap-2 p-3">
                <span>
                  <a className="font-semibold underline" href={s.url} target="_blank" rel="noreferrer">{s.name}</a>
                  <span className="ml-2 text-xs text-slate">{s.provider}</span>
                </span>
                <span className="text-xs text-slate">
                  <span className={st.tone}>{st.text}</span>
                  {s.last_checked_at && ` · checked ${fmtDate(s.last_checked_at)} · ${s.last_matches} matching`}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </Shell>
  );
}
