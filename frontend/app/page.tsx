"use client";

import Link from "next/link";
import Shell from "@/components/Shell";
import { ErrorNote, useLoad } from "@/components/ui";

type Dash = {
  today: { sent: number; quota: number; drafts_waiting: number; queued: number; needs_attention: number; manual_apply: number };
  week: { evaluated: number; matched: number; applied: number; replied: number; interviews: number; rejections: number };
  campaigns: { id: string; name: string; state: string }[];
};
type Setup = { missing: string[] };
type Day = { day: string; sent: number; replies: number };

const MISSING: Record<string, { text: string; href: string }> = {
  cv: { text: "Upload a CV", href: "/cvs" },
  mail: { text: "Connect your Gmail account", href: "/account" },
  escalation_email: { text: "Say where urgent replies should be sent", href: "/account" },
  fact_sheet: { text: "Fill in your fact sheet", href: "/cvs" },
};

function Figure({ n, label, href }: { n: number | string; label: string; href?: string }) {
  const inner = (
    <>
      <span className="letter block text-4xl">{n}</span>
      <span className="text-sm text-slate">{label}</span>
    </>
  );
  return href ? <Link href={href} className="block hover:underline">{inner}</Link> : <div>{inner}</div>;
}

export default function Today() {
  const dash = useLoad<Dash>("/api/dashboard");
  const setup = useLoad<Setup>("/api/setup/status");
  const stats = useLoad<Day[]>("/api/stats/daily?days=13");
  const replies = useLoad<{ urgency: string }[]>("/api/escalations");
  const d = dash.data;
  const urgent = (replies.data || []).filter((r) => r.urgency === "urgent").length;
  return (
    <Shell title="Today">
      <ErrorNote message={dash.error} />
      {setup.data && setup.data.missing.length > 0 && (
        <section className="mb-8 border-l-4 border-waiting bg-sheet px-4 py-3 text-sm">
          <p className="font-semibold">Finish setting up before the agent can apply</p>
          <ul className="mt-1 list-disc pl-5">
            {setup.data.missing.map((m) => (
              <li key={m}><Link className="underline" href={MISSING[m]?.href || "/setup"}>{MISSING[m]?.text || m}</Link></li>
            ))}
          </ul>
        </section>
      )}
      {d && (
        <>
          {urgent > 0 && (
            <Link href="/inbox" className="mb-6 block border-l-4 border-problem bg-sheet px-4 py-3 text-sm font-semibold text-problem">
              {urgent === 1 ? "An urgent reply needs you" : `${urgent} urgent replies need you`} (interview, offer or similar). Open the inbox
            </Link>
          )}
          <section className="grid grid-cols-2 gap-6 border-b border-rule pb-8 sm:grid-cols-5">
            <Figure n={(replies.data || []).length} label="replies waiting for you" href="/inbox" />
            <Figure n={d.today.drafts_waiting + d.today.manual_apply} label="drafts and jobs to apply to" href="/drafts" />
            <Figure n={`${d.today.sent} of ${d.today.quota}`} label="sent today" href="/applications?status=sent" />
            <Figure n={d.today.queued} label="approved, going out" href="/applications?status=queued" />
            <Figure n={d.today.needs_attention} label="need attention" href="/applications?status=failed" />
          </section>
          <h2 className="mt-8 text-xl">Last seven days</h2>
          <section className="mt-4 grid grid-cols-2 gap-6 sm:grid-cols-6">
            <Figure n={d.week.evaluated} label="postings checked" href="/applications?tab=skipped" />
            <Figure n={d.week.matched} label="good matches" />
            <Figure n={d.week.applied} label="applications sent" href="/applications" />
            <Figure n={d.week.replied} label="replies" href="/inbox" />
            <Figure n={d.week.interviews} label="interviews" href="/applications?status=interview" />
            <Figure n={d.week.rejections} label="rejections" href="/applications?status=rejected" />
          </section>
          {stats.data && stats.data.some((x) => x.sent || x.replies) && (
            <section className="mt-8" aria-label="Applications sent and replies received per day">
              <h2 className="text-xl">Sent and replies by day</h2>
              <div className="mt-3 flex h-32 items-end gap-1 border-b border-ink">
                {stats.data.map((x) => {
                  const max = Math.max(1, ...stats.data!.map((y) => Math.max(y.sent, y.replies)));
                  return (
                    <div key={x.day} className="flex h-full flex-1 items-end justify-center gap-px" title={`${x.day}: ${x.sent} sent, ${x.replies} replies`}>
                      <span className="w-1/2 bg-ink" style={{ height: `${(x.sent / max) * 100}%` }} />
                      <span className="w-1/2 bg-sent" style={{ height: `${(x.replies / max) * 100}%` }} />
                    </div>
                  );
                })}
              </div>
              <p className="mt-1 flex justify-between text-xs text-slate">
                <span>{stats.data[0].day}</span>
                <span><span className="mr-1 inline-block h-2 w-2 bg-ink" />sent <span className="ml-3 mr-1 inline-block h-2 w-2 bg-sent" />replies</span>
                <span>{stats.data[stats.data.length - 1].day}</span>
              </p>
            </section>
          )}
          {d.today.manual_apply > 0 && (
            <p className="mt-8 text-sm">
              {d.today.manual_apply} good {d.today.manual_apply === 1 ? "match takes" : "matches take"} applications on the employer&apos;s own site.{" "}
              <Link className="underline" href="/drafts">See them in Drafts</Link>
            </p>
          )}
          <h2 className="mt-8 text-xl">Searches</h2>
          {d.campaigns.length === 0 ? (
            <p className="mt-2 text-sm">No search yet. <Link className="underline" href="/setup">Describe the jobs you want</Link></p>
          ) : (
            <ul className="mt-2 text-sm">
              {d.campaigns.map((c) => (
                <li key={c.id} className="flex justify-between border-b border-rule py-2">
                  <Link className="underline" href="/campaigns">{c.name}</Link>
                  <span className="text-slate">{c.state === "active" ? "Running" : c.state === "dry_run" ? "Dry run" : "Paused"}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </Shell>
  );
}
