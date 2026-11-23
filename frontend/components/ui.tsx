"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";

const STATUS_STYLE: Record<string, { label: string; tone: string }> = {
  awaiting_approval: { label: "Needs your approval", tone: "text-waiting" },
  queued: { label: "Approved, waiting to send", tone: "text-waiting" },
  sending: { label: "Sending", tone: "text-waiting" },
  sent: { label: "Sent", tone: "text-sent" },
  acknowledged: { label: "Received by employer", tone: "text-sent" },
  in_conversation: { label: "In conversation", tone: "text-sent" },
  interview: { label: "Interview", tone: "text-sent" },
  offer: { label: "Offer", tone: "text-sent" },
  rejected: { label: "Rejected", tone: "text-slate" },
  closed: { label: "Position closed", tone: "text-slate" },
  no_response: { label: "No response", tone: "text-slate" },
  undeliverable: { label: "Undeliverable", tone: "text-problem" },
  failed: { label: "Needs attention", tone: "text-problem" },
  skipped: { label: "Skipped", tone: "text-slate" },
  manual_apply: { label: "Apply by hand", tone: "text-waiting" },
  dry_run: { label: "Dry run, not sent", tone: "text-slate" },
  unconfirmed: { label: "Sent, not confirmed: check it", tone: "text-waiting" },
  form_ready: { label: "Form filled, check it", tone: "text-waiting" },
  draft: { label: "Draft", tone: "text-slate" },
};

export function Postmark({ status }: { status: string }) {
  const s = STATUS_STYLE[status] || { label: status, tone: "text-slate" };
  return <span className={`postmark ${s.tone}`}>{s.label}</span>;
}

export function ErrorNote({ message }: { message: string }) {
  if (!message) return null;
  return <p className="my-3 border-l-4 border-problem bg-sheet px-3 py-2 text-sm text-problem" role="alert">{message}</p>;
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <p className="rounded border border-dashed border-rule bg-sheet px-4 py-8 text-center text-sm text-slate">{children}</p>;
}

/** Load data from the API, with reload and error state. */
export function useLoad<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  useEffect(() => {
    if (!path) return;
    let live = true;
    api<T>(path)
      .then((d) => live && (setData(d), setError("")))
      .catch((e) => live && setError(e instanceof Error ? e.message : "Could not load"));
    return () => {
      live = false;
    };
  }, [path, tick]);
  return { data, error, reload };
}

export function errorText(e: unknown): string {
  return e instanceof Error ? e.message : "Something went wrong";
}
