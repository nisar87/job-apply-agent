"use client";

import Link from "next/link";
import { useState } from "react";
import CountryPicker from "@/components/CountryPicker";
import { Progress } from "@/components/Progress";
import Shell from "@/components/Shell";
import { Empty, ErrorNote, errorText, useLoad } from "@/components/ui";
import { api, fmtDate } from "@/lib/api";
import type { Campaign, CampaignConfig } from "@/lib/types";

const list = (v: string) => v.split(",").map((s) => s.trim()).filter(Boolean);

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block text-sm">
      <span className="font-semibold">{label}</span>
      {hint && <span className="block text-xs text-slate">{hint}</span>}
      <span className="mt-1 block">{children}</span>
    </label>
  );
}

function Editor({ campaign, onSaved }: { campaign: Campaign; onSaved: () => void }) {
  const [c, setC] = useState<CampaignConfig>(campaign.config);
  const [name, setName] = useState(campaign.name);
  const [mode, setMode] = useState(campaign.approval_mode);
  const [autoAbove, setAutoAbove] = useState(campaign.auto_above_score);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [running, setRunning] = useState("");
  const [note, setNote] = useState("");
  const set = <K extends keyof CampaignConfig>(key: K, value: CampaignConfig[K]) => setC((old) => ({ ...old, [key]: value }));

  async function save(extra: Record<string, unknown> = {}) {
    setError("");
    setSaved(false);
    try {
      await api(`/api/campaigns/${campaign.id}`, { method: "PATCH", json: { name, config: c, approval_mode: mode, auto_above_score: autoAbove, ...extra } });
      setSaved(true);
      onSaved();
    } catch (e) {
      setError(errorText(e));
    }
  }

  async function fillFromCv() {
    setError("");
    try {
      const res = await api<{ keywords: string[] }>("/api/cvs/search-keywords");
      if (res.keywords.length === 0) setError("Upload a CV first: its skills become the search keywords.");
      else set("keywords", res.keywords);
    } catch (e) {
      setError(errorText(e));
    }
  }

  async function run(kind: string) {
    setRunning(kind);
    setError("");
    setNote("");
    try {
      const out = await api<{ started?: boolean }>(`/api/campaigns/${campaign.id}/run?kind=${kind}`, { method: "POST" });
      if (out?.started) {
        // Each wait call stays open on the server while the run works, then reports whether it is still going.
        let first = true;
        for (let i = 0; i < 80; i++) {
          const w = await api<{ running: boolean }>(`/api/campaigns/${campaign.id}/run/wait?kind=${kind}${first ? "&first=true" : ""}`);
          first = false;
          if (!w.running) break;
        }
        setNote(kind === "discovery" ? "Done looking. New postings are in My search results and Applications." : "Done writing. The drafts are under Drafts.");
      }
      onSaved();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setRunning("");
    }
  }

  return (
    <section className="border-t border-rule p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <input className="field letter max-w-md text-xl" value={name} onChange={(e) => setName(e.target.value)} aria-label="Search name" />
        <div className="flex flex-wrap gap-2 text-sm">
          {(["active", "dry_run", "paused"] as const).map((state) => (
            <button key={state} className={`btn ${campaign.state === state ? "" : "btn-quiet"}`} onClick={() => save({ state })}>
              {state === "active" ? "Running" : state === "dry_run" ? "Dry run" : "Paused"}
            </button>
          ))}
        </div>
      </div>
      <p className="mt-1 text-xs text-slate">A dry run finds jobs and writes drafts but sends nothing.</p>
      <ErrorNote message={error} />
      <div className="mt-5 grid gap-5 md:grid-cols-2">
        <Field label="Keywords" hint="Comma separated. Empty means the skills on your CVs">
          <input className="field" value={c.keywords.join(", ")} onChange={(e) => set("keywords", list(e.target.value))} />
          <button type="button" className="btn btn-quiet mt-2 text-xs" onClick={fillFromCv}>Use the skills on my CV</button>
        </Field>
        <div className="block text-sm">
          <span className="font-semibold">Countries to apply within</span>
          <span className="block text-xs text-slate">Jobs outside these countries are skipped. None chosen means anywhere</span>
          <div className="mt-1"><CountryPicker value={c.countries} onChange={(codes) => set("countries", codes)} /></div>
          {c.countries.length > 0 && (
            <label className="mt-2 flex items-center gap-2">
              <input type="checkbox" checked={c.remote_outside_countries} onChange={(e) => set("remote_outside_countries", e.target.checked)} />
              Also remote jobs based in other countries
            </label>
          )}
        </div>
        <Field label="Work mode" hint="remote, hybrid, onsite. Empty means any"><input className="field" value={c.work_modes.join(", ")} onChange={(e) => set("work_modes", list(e.target.value))} /></Field>
        <Field label="Contract type" hint="full_time, part_time, contract, internship"><input className="field" value={c.contract_types.join(", ")} onChange={(e) => set("contract_types", list(e.target.value))} /></Field>
        <Field label="Minimum salary per year" hint="Leave empty for no minimum">
          <input className="field" type="number" value={c.salary_min ?? ""} onChange={(e) => set("salary_min", e.target.value ? Number(e.target.value) : null)} />
        </Field>
        <Field label="Oldest posting, in days"><input className="field" type="number" min={1} max={60} value={c.max_posting_age_days ?? ""} onChange={(e) => set("max_posting_age_days", e.target.value ? Number(e.target.value) : null)} /></Field>
        <Field label="Applications per day" hint="Each needs about 8 minutes of send window"><input className="field" type="number" min={1} max={30} value={c.daily_quota} onChange={(e) => set("daily_quota", Number(e.target.value))} /></Field>
        <Field label="Send window" hint="In your time zone, Monday to Friday">
          <span className="flex items-center gap-2">
            <input className="field" type="time" value={c.send_window.start} onChange={(e) => set("send_window", { ...c.send_window, start: e.target.value })} aria-label="Window start" />
            to
            <input className="field" type="time" value={c.send_window.end} onChange={(e) => set("send_window", { ...c.send_window, end: e.target.value })} aria-label="Window end" />
          </span>
        </Field>
        <Field label="Match threshold" hint="Only jobs scoring at least this are applied to"><input className="field" type="number" min={50} max={100} value={c.match_threshold} onChange={(e) => set("match_threshold", Number(e.target.value))} /></Field>
        <Field label="Same company again after, in days"><input className="field" type="number" min={0} max={365} value={c.company_cooldown_days} onChange={(e) => set("company_cooldown_days", Number(e.target.value))} /></Field>
        <Field label="Same contact address again after, in days"><input className="field" type="number" min={0} max={365} value={c.contact_cooldown_days} onChange={(e) => set("contact_cooldown_days", Number(e.target.value))} /></Field>
        <Field label="Drafts expire after, in days"><input className="field" type="number" min={1} max={7} value={c.draft_expiry_days} onChange={(e) => set("draft_expiry_days", Number(e.target.value))} /></Field>
        <Field label="When a posting does not state the salary">
          <select className="field" value={c.unknown_policy.salary} onChange={(e) => set("unknown_policy", { ...c.unknown_policy, salary: e.target.value })}>
            <option value="apply">Apply anyway</option><option value="skip">Skip it</option>
          </select>
        </Field>
        <Field label="Approval">
          <select className="field" value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="review">I approve every draft</option>
            <option value="auto_above">Send without asking above a score</option>
            <option value="full_auto">Send everything that passes the checks</option>
          </select>
          {mode === "auto_above" && (
            <input className="field mt-2" type="number" min={50} max={100} value={autoAbove} onChange={(e) => setAutoAbove(Number(e.target.value))} aria-label="Score for automatic sending" />
          )}
        </Field>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={c.exclude_agencies} onChange={(e) => set("exclude_agencies", e.target.checked)} /> Skip recruitment agencies</label>
        <label className="flex items-start gap-2 text-sm">
          <input className="mt-1" type="checkbox" checked={!!c.auto_fill_forms} onChange={(e) => set("auto_fill_forms", e.target.checked)} />
          <span>Fill in employers&apos; web forms for me<span className="block text-xs text-slate">Two tries per job. Nothing is submitted until you check the filled form. Forms with a CAPTCHA or a login are left for you.</span></span>
        </label>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={c.follow_up_enabled} onChange={(e) => set("follow_up_enabled", e.target.checked)} /> Send one follow-up after {c.follow_up_after_days} days without a reply</label>
      </div>
      <div className="mt-6 flex flex-wrap items-center gap-3">
        <button className="btn" onClick={() => save()}>Save changes</button>
        {saved && <span className="text-sm text-sent">Saved</span>}
        {note && !running && <span className="text-sm text-slate">{note}</span>}
        <span className="ml-auto flex flex-wrap gap-2">
          <button className="btn btn-quiet" disabled={!!running} onClick={() => run("discovery")}>{running === "discovery" ? "Looking…" : "Look for jobs now"}</button>
          <button className="btn btn-quiet" disabled={!!running} onClick={() => run("prepare")}>{running === "prepare" ? "Writing…" : "Write drafts now"}</button>
        </span>
      </div>
      {running && (
        <Progress label={running === "discovery" ? "Looking for jobs that fit…" : "Writing your drafts…"}
          hint="This takes a few minutes. Keep this page open until it finishes." />
      )}
    </section>
  );
}

const stateLabel = (state: string) => (state === "active" ? "Running" : state === "dry_run" ? "Dry run" : "Paused");

function Row({ c, checked, onCheck, open, onOpen, onSaved }: {
  c: Campaign; checked: boolean; onCheck: (v: boolean) => void; open: boolean; onOpen: () => void; onSaved: () => void;
}) {
  const cfg = c.config;
  const where = cfg.countries.length ? `${cfg.countries.length} ${cfg.countries.length === 1 ? "country" : "countries"}` : "Anywhere";
  const what = cfg.keywords.length ? cfg.keywords.slice(0, 4).join(", ") + (cfg.keywords.length > 4 ? ` +${cfg.keywords.length - 4}` : "") : "Skills from your CV";
  return (
    <div className={`card mb-3 overflow-hidden ${checked ? "ring-2 ring-ink" : ""}`}>
      <div className="flex flex-wrap items-center gap-3 p-4">
        <input type="checkbox" className="h-4 w-4" checked={checked} onChange={(e) => onCheck(e.target.checked)} aria-label={`Select ${c.name}`} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-semibold">{c.name}</span>
            <span className={`postmark ${c.state === "active" ? "text-sent" : ""}`}>{stateLabel(c.state)}</span>
          </div>
          <p className="mt-1 truncate text-sm text-slate">{what}</p>
          <p className="mt-0.5 text-xs text-slate">{where} · up to {cfg.daily_quota} a day · score {cfg.match_threshold}+{c.created_at ? ` · started ${fmtDate(c.created_at)}` : ""}</p>
        </div>
        <button className="btn btn-quiet text-sm" onClick={onOpen}>{open ? "Close" : "Edit"}</button>
      </div>
      {open && <Editor campaign={c} onSaved={onSaved} />}
    </div>
  );
}

export default function Campaigns() {
  const { data, error, reload } = useLoad<Campaign[]>("/api/campaigns");
  const [picked, setPicked] = useState<string[]>([]);
  const [open, setOpen] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [fail, setFail] = useState("");
  const all = data || [];
  const some = picked.length > 0;

  async function remove() {
    setFail("");
    try {
      await api("/api/campaigns/remove", { method: "POST", json: { ids: picked } });
      setPicked([]);
      setConfirm(false);
      reload();
    } catch (e) {
      setFail(errorText(e));
    }
  }

  return (
    <Shell title="My search" intro="What the agent looks for. Describe a new search, or select old ones to delete." actions={<Link className="btn" href="/setup">Describe a new search</Link>}>
      <ErrorNote message={error || fail} />
      {all.length === 0 && data && <Empty>No search yet. Describe the jobs you want and the agent builds the search from it.</Empty>}
      {all.length > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-3 text-sm">
          <label className="flex items-center gap-2">
            <input type="checkbox" className="h-4 w-4" checked={picked.length === all.length} onChange={(e) => setPicked(e.target.checked ? all.map((c) => c.id) : [])} />
            {some ? `${picked.length} selected` : `Select all (${all.length})`}
          </label>
          {some && !confirm && <button className="btn btn-quiet" onClick={() => setConfirm(true)}>Delete selected</button>}
          {some && confirm && (
            <span className="flex flex-wrap items-center gap-2">
              Delete {picked.length} {picked.length === 1 ? "search" : "searches"}? They stop at once. Applications already sent stay in your history.
              <button className="btn" onClick={remove}>Yes, delete</button>
              <button className="btn btn-quiet" onClick={() => setConfirm(false)}>Keep</button>
            </span>
          )}
        </div>
      )}
      {all.map((c) => (
        <Row key={c.id} c={c} checked={picked.includes(c.id)} onCheck={(v) => setPicked((p) => (v ? [...p, c.id] : p.filter((x) => x !== c.id)))}
          open={open === c.id} onOpen={() => setOpen(open === c.id ? "" : c.id)} onSaved={reload} />
      ))}
    </Shell>
  );
}
