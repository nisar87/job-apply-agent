"use client";

import Shell from "@/components/Shell";
import { Empty, ErrorNote, useLoad } from "@/components/ui";
import { fmtDate } from "@/lib/api";

type Site = {
  provider: string; slug: string; kind: string; name: string; url: string; enabled: boolean;
  last_checked_at: string | null; last_ok_at: string | null; last_matches: number; failures: number;
  last_error: string; learned_from: string;
};

type Pool = { jobs: number; new_last_day: number; company_boards?: number; last_harvest: { finished_at?: string; new?: number; boards_checked?: number; boards_learned?: number } | null };

const BOARD_NAMES: Record<string, string> = {
  pool: "Job pool (free harvester)", websearch: "Google search (by the AI)", arbeitnow: "Arbeitnow", arbeitsagentur: "Bundesagentur für Arbeit", adzuna: "Adzuna", remotive: "Remotive", jobicy: "Jobicy", himalayas: "Himalayas",
  weworkremotely: "We Work Remotely", remoteok: "Remote OK", workingnomads: "Working Nomads",
  hackernews: "Hacker News: Who is hiring", workablesearch: "Workable job search",
  landingjobs: "Landing.jobs", berlinstartupjobs: "Berlin Startup Jobs",
  commoncrawl: "Common Crawl (finds company career pages)",
  wayback: "Internet Archive (finds company career pages)",
};

function status(s: Site): { text: string; tone: string } {
  if (!s.enabled) return { text: s.failures ? "Paused after errors" : "Paused", tone: "text-problem" };
  if (!s.last_checked_at) return { text: "Not checked yet", tone: "text-slate" };
  if (s.failures) return { text: `Failing (${s.failures})`, tone: "text-waiting" };
  return { text: "Working", tone: "text-sent" };
}

export default function Sources() {
  const { data, error } = useLoad<Site[]>("/api/job-sites");
  const { data: pool } = useLoad<Pool>("/api/job-pool");
  const harvest = pool?.last_harvest;
  const boards = (data || []).filter((s) => s.kind === "board");
  const companies = (data || []).filter((s) => s.kind === "company");
  return (
    <Shell title="Job sources" intro="The job boards and company sites the agent searches.">
      <p className="max-w-prose text-sm text-slate">
        Every place the agent searches. Job boards are checked for each search; company career pages are learned
        automatically from the jobs it reads and checked twice a day. Jobs are always applied to on the original site.
      </p>
      {pool && (
        <p className="mt-2 max-w-prose text-sm text-slate">
          Job pool: {pool.jobs} jobs collected from free boards, {pool.new_last_day} new in the last day.
          {harvest?.finished_at
            ? ` Last harvest ${fmtDate(harvest.finished_at)}: ${harvest.new ?? 0} new jobs, ${harvest.boards_checked ?? 0} company pages checked, ${harvest.boards_learned ?? 0} learned.`
            : " No harvest has run yet."}
        </p>
      )}
      <ErrorNote message={error} />
      {!data && !error && <p className="mt-4 text-sm text-slate">Loading…</p>}
      <h2 className="mt-6 text-xl">Job boards ({boards.length})</h2>
      {data && boards.length === 0 ? <Empty>No board has been checked yet. The first search fills this in.</Empty> : (
        <ul className="mt-3 grid gap-3 sm:grid-cols-2">
          {boards.map((s) => {
            const st = status(s);
            return (
              <li key={s.provider} className="card p-3 text-sm">
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
      <h2 className="mt-8 text-xl">Company career pages ({pool?.company_boards ?? companies.length})</h2>
      <p className="mt-1 max-w-prose text-sm text-slate">
        Found from links in job ads and from the Internet Archive&apos;s list of careers pages (Greenhouse, Lever, Ashby,
        Workable, SmartRecruiters, Recruitee, Personio, Teamtailor).
        {pool?.company_boards && pool.company_boards > companies.length ? ` Showing the ${companies.length} with the most matching jobs.` : ""}
      </p>
      {data && companies.length === 0 ? <Empty>None learned yet. They appear as the agent reads job ads that link to a company&apos;s own careers page.</Empty> : (
        <ul className="mt-3 divide-y divide-rule card text-sm">
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
