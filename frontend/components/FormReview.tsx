"use client";

import { useEffect, useState } from "react";
import { ErrorNote, errorText, useLoad } from "@/components/ui";
import { API_BASE, api, getToken } from "@/lib/api";

type Review = {
  hiring_system: string | null; consents: string[]; has_screenshot: boolean; submitted_at: string | null;
  answers: { label: string; value: string | boolean }[]; files: { label: string; file: string }[];
};

const BASE = API_BASE;

export default function FormReview({ id, status, onChange }: { id: string; status: string; onChange: () => void }) {
  const review = useLoad<Review>(status === "form_ready" || status === "sent" ? `/api/applications/${id}/form` : null);
  const [shot, setShot] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!review.data?.has_screenshot) return;
    let url = "";
    fetch(`${BASE}/api/applications/${id}/form/screenshot`, { headers: { Authorization: `Bearer ${getToken()}` } })
      .then((r) => (r.ok ? r.blob() : Promise.reject()))
      .then((b) => { url = URL.createObjectURL(b); setShot(url); })
      .catch(() => undefined);
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [id, review.data?.has_screenshot]);

  async function post(path: string) {
    setBusy(true);
    setError("");
    try {
      await api(`/api/applications/${id}/form/${path}`, { method: "POST" });
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
      onChange();
      review.reload();
    }
  }

  if (status === "manual_apply") {
    return (
      <section className="mt-6 border border-rule bg-sheet p-4 text-sm">
        <h2 className="text-xl">This employer uses a web form</h2>
        <p className="mt-1 max-w-prose text-slate">The agent can fill it in from your CV and fact sheet, then show you the result. Nothing is submitted until you approve.</p>
        <ErrorNote message={error} />
        <button className="btn mt-3" disabled={busy} onClick={() => post("prepare")}>{busy ? "Filling in the form…" : "Fill in the form for me"}</button>
      </section>
    );
  }
  if (!review.data) return null;
  const r = review.data;
  return (
    <section className="mt-6 border border-rule bg-sheet p-4 text-sm">
      <h2 className="text-xl">{status === "sent" ? "Form as submitted" : "Check the filled form"}</h2>
      <ErrorNote message={error} />
      <dl className="mt-3 grid gap-x-6 gap-y-1 sm:grid-cols-2">
        {r.answers.map((a) => (
          <div key={a.label} className="flex justify-between gap-3 border-b border-rule py-1">
            <dt className="text-slate">{a.label}</dt>
            <dd className="max-w-[60%] truncate text-right">{a.value === true ? "Ticked" : String(a.value)}</dd>
          </div>
        ))}
        {r.files.map((f) => (
          <div key={f.label} className="flex justify-between gap-3 border-b border-rule py-1"><dt className="text-slate">{f.label}</dt><dd>{f.file}</dd></div>
        ))}
      </dl>
      {r.consents.length > 0 && (
        <p className="mt-3 border-l-4 border-waiting pl-3">By approving you accept, in your own name: {r.consents.join("; ")}</p>
      )}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      {shot && <img src={shot} alt="Screenshot of the filled application form" className="mt-4 max-h-[32rem] w-full border border-rule object-contain object-top" />}
      {status === "form_ready" && (
        <button className="btn mt-4" disabled={busy} onClick={() => post("approve")}>{busy ? "Submitting…" : "Approve and submit"}</button>
      )}
    </section>
  );
}
