"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import ChangePassword from "@/components/ChangePassword";
import JobGmail from "@/components/JobGmail";
import MainEmail from "@/components/MainEmail";
import ModelKey from "@/components/ModelKey";
import Shell from "@/components/Shell";
import { useCountries } from "@/components/CountryPicker";
import { ErrorNote, errorText, useLoad } from "@/components/ui";
import { api } from "@/lib/api";
import type { Campaign } from "@/lib/types";

const STATES = [
  { value: "active", label: "Running", detail: "Applies to jobs that fit." },
  { value: "dry_run", label: "Try it first", detail: "Prepares everything and sends nothing." },
  { value: "paused", label: "Paused", detail: "Does nothing until you resume." },
];

function Section({ id, title, summary, children, open }: { id: string; title: string; summary: string; children: React.ReactNode; open?: boolean }) {
  const [isOpen, setOpen] = useState(!!open);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (typeof window !== "undefined" && window.location.hash === `#${id}`) setOpen(true);
  }, [id]);
  return (
    <details id={id} className="border-b border-rule py-3" open={isOpen} onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
      <summary className="cursor-pointer"><span className="letter text-xl">{title}</span> <span className="ml-2 text-sm text-slate">{summary}</span></summary>
      <div className="mt-4 max-w-2xl">{children}</div>
    </details>
  );
}

function WhatYouWant() {
  const searches = useLoad<Campaign[]>("/api/campaigns");
  const [error, setError] = useState("");
  async function patch(id: string, json: unknown) {
    setError("");
    try {
      await api(`/api/campaigns/${id}`, { method: "PATCH", json });
      searches.reload();
    } catch (e) {
      setError(errorText(e));
    }
  }
  const { name: countryName } = useCountries();
  const list = searches.data || [];
  const running = list.filter((c) => c.state !== "paused").length;
  return (
    <>
      {running > 1 && <p className="mb-3 border-l-4 border-problem bg-sheet px-3 py-2 text-sm">{running} searches are running at the same time. Pause the ones you no longer want.</p>}
      <ErrorNote message={error || searches.error} />
      {list.length === 0 && <p className="text-sm">No job search yet. <Link className="underline" href="/setup">Set up your agent</Link></p>}
      {list.map((c) => (
        <div key={c.id} className="mb-4 border border-rule bg-sheet p-4 text-sm">
          <p className="font-semibold">{c.name}</p>
          <p className="text-slate">Looking for {c.config.keywords.join(", ") || "anything that fits"}</p>
          <p className="text-slate">Where: {c.config.countries.length ? c.config.countries.map(countryName).join(", ") : "anywhere"}
            {c.config.priority_countries?.length ? `; first: ${c.config.priority_countries.map(countryName).join(", ")}` : ""}</p>
          <fieldset className="mt-3">
            <legend className="font-semibold">State</legend>
            {STATES.map((s) => (
              <label key={s.value} className="mr-5 inline-block"><input type="radio" name={`state-${c.id}`} checked={c.state === s.value} onChange={() => patch(c.id, { state: s.value })} /> {s.label}
                <span className="block text-xs text-slate">{s.detail}</span></label>
            ))}
          </fieldset>
          <label className="mt-3 block">Applications a day (default 10)
            <input className="field mt-1 w-24" type="number" min={1} max={25} defaultValue={c.config.daily_quota}
              onBlur={(e) => { const v = Number(e.target.value); if (v && v !== c.config.daily_quota) patch(c.id, { config: { ...c.config, daily_quota: v } }); }} />
          </label>
          <label className="mt-3 block">How picky should I be? (0 to 100, default {c.config.match_threshold})
            <input className="field mt-1 w-24" type="number" min={0} max={100} defaultValue={c.config.match_threshold}
              onBlur={(e) => { const v = Number(e.target.value); if (v !== c.config.match_threshold) patch(c.id, { config: { ...c.config, match_threshold: v } }); }} />
            <span className="block text-xs text-slate">Higher means fewer, better-fitting applications.</span>
          </label>
          <p className="mt-3"><Link className="underline" href="/campaigns">All details of this search</Link> · <Link className="underline" href="/setup">Change it by describing it</Link></p>
        </div>
      ))}
    </>
  );
}

export default function Settings() {
  return (
    <Shell title="Settings">
      <p className="mb-2 max-w-prose text-sm text-slate">Everything has a working default. You only need this page to change something.</p>
      <Section id="search" title="What you want" summary="Your job search, how picky, how many a day" open><WhatYouWant /></Section>
      <Section id="cv" title="Your CV and facts" summary="What the agent knows about you">
        <p className="text-sm">The agent answers employers&apos; questions only from your CV and these facts.</p>
        <p className="mt-2"><Link className="btn btn-quiet" href="/cvs">Open your CV and facts</Link></p>
      </Section>
      <Section id="where" title="Where and how much" summary="Countries, sending hours, places the agent looks">
        <p className="text-sm">Countries, send hours and cooldowns are set in the details of your search.</p>
        <p className="mt-2"><Link className="btn btn-quiet" href="/campaigns">Open the search details</Link></p>
      </Section>
      <Section id="mail" title="Mail" summary="The Gmail applications go out from"><JobGmail /></Section>
      <Section id="reports" title="Reports" summary="Where reports go and how often"><MainEmail /></Section>
      <Section id="keys" title="Model keys" summary="Your own AI keys, if you want to use them"><ModelKey /></Section>
      <Section id="never" title="Never contact" summary="Companies and addresses to leave alone">
        <p className="mt-1"><Link className="btn btn-quiet" href="/account">Open the block list</Link></p>
      </Section>
      <Section id="data" title="Your data and password" summary="Download or delete your data, change your password">
        <ChangePassword />
        <p className="mt-4"><Link className="btn btn-quiet" href="/account">Download or delete your data</Link></p>
      </Section>
    </Shell>
  );
}
