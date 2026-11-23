"use client";

import { useState } from "react";
import { ErrorNote, errorText, useLoad } from "@/components/ui";
import { api } from "@/lib/api";

type Me = { email: string; escalation_email: string | null; report_frequency: "daily" | "weekly" | "off"; report_hour: number };
type Preview = { to: string; subject: string; body: string };

const FREQUENCIES: { value: Me["report_frequency"]; label: string; detail: string }[] = [
  { value: "daily", label: "Every day", detail: "One email at the end of each day." },
  { value: "weekly", label: "Every Monday", detail: "One email covering the past week." },
  { value: "off", label: "No report", detail: "Urgent replies still reach you." },
];

/** Where reports and important messages go: the user's main address, kept apart from the job Gmail. */
export default function MainEmail({ onSaved }: { onSaved?: () => void } = {}) {
  const me = useLoad<Me>("/api/auth/me");
  const [address, setAddress] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);

  async function save(json: Partial<Me>, done = "Saved") {
    setError("");
    setNote("");
    try {
      await api("/api/auth/me", { method: "PATCH", json });
      setNote(done);
      me.reload();
      onSaved?.();
      return true;
    } catch (e) {
      setError(errorText(e));
      return false;
    }
  }

  async function run(fn: () => Promise<void>) {
    setError("");
    setNote("");
    try {
      await fn();
    } catch (e) {
      setError(errorText(e));
    }
  }

  if (!me.data) return null;
  const d = me.data;
  const main = address ?? (d.escalation_email || d.email);
  return (
    <section className="mt-10">
      <h2 className="text-xl">Your main email and reports</h2>
      <p className="mt-1 max-w-prose text-sm text-slate">
        Your everyday address, not the job Gmail. Reports of what the agent applied to, urgent replies from employers
        (interviews, offers) and account notices go here.
      </p>
      <ErrorNote message={error} />
      <form className="mt-3 flex flex-wrap items-end gap-2 text-sm" onSubmit={async (e) => {
        e.preventDefault();
        if (await save({ escalation_email: main.trim() })) setAddress(null);
      }}>
        <label className="min-w-64 flex-1">Main email
          <input className="field mt-1" type="email" required value={main} onChange={(e) => setAddress(e.target.value)} />
        </label>
        <button className="btn btn-quiet">Save</button>
      </form>

      <h3 className="mt-6 font-semibold">Report of what the agent applied to</h3>
      <p className="mt-1 max-w-prose text-sm text-slate">
        For each application: the job, the link to the ad, the address it went to, where that address came from, and
        which site found the job. Also the drafts waiting for you and jobs to apply to by hand.
      </p>
      <div className="mt-3 grid gap-2 md:grid-cols-3">
        {FREQUENCIES.map((f) => (
          <label key={f.value} className={`cursor-pointer border p-3 text-sm ${d.report_frequency === f.value ? "border-ink bg-sheet" : "border-rule"}`}>
            <span className="flex items-center gap-2 font-semibold">
              <input type="radio" name="report" checked={d.report_frequency === f.value} onChange={() => save({ report_frequency: f.value })} />
              {f.label}
            </span>
            <span className="mt-1 block text-slate">{f.detail}</span>
          </label>
        ))}
      </div>
      {d.report_frequency !== "off" && (
        <label className="mt-3 block text-sm">Send it at
          <select className="field mt-1 max-w-32" value={d.report_hour} onChange={(e) => save({ report_hour: Number(e.target.value) })}>
            {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>)}
          </select>
          <span className="ml-2 text-xs text-slate">your time</span>
        </label>
      )}
      <p className="mt-3 flex flex-wrap items-center gap-3 text-sm">
        <button className="btn btn-quiet" onClick={() => run(async () => setPreview(await api<Preview>("/api/report/preview")))}>Show today&apos;s report</button>
        <button className="btn btn-quiet" onClick={() => run(async () => {
          const r = await api<{ sent_to: string }>("/api/report/send-now", { method: "POST" });
          setNote(`Sent to ${r.sent_to}`);
        })}>Email it to me now</button>
        {note && <span className="text-sent">{note}</span>}
      </p>
      {preview && (
        <div className="mt-3 card p-3 text-sm">
          <p className="text-xs text-slate">To {preview.to} · {preview.subject}</p>
          <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap font-sans">{preview.body}</pre>
          <button className="btn btn-quiet mt-2" onClick={() => setPreview(null)}>Close</button>
        </div>
      )}
    </section>
  );
}
