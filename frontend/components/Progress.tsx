"use client";

import { useEffect, useState } from "react";
import { pendingSince, watchPending } from "@/lib/api";

/** A bar that moves, for work that takes a while. Shows what is happening and that the page is not stuck. */
export function Progress({ label, hint }: { label: string; hint?: string }) {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, []);
  return (
    <div role="status" aria-live="polite" className="my-3 max-w-2xl">
      <div className="flex items-baseline justify-between gap-3 text-sm">
        <span className="font-semibold">{label}</span>
        <span className="text-xs text-slate">{seconds}s</span>
      </div>
      <div className="progress mt-1.5"><span /></div>
      {hint && <p className="mt-1 text-xs text-slate">{hint}</p>}
    </div>
  );
}

/** At the top of every page: a thin bar while any request is slow, and a note when one takes long. */
export function GlobalProgress() {
  const [age, setAge] = useState(-1);
  useEffect(() => {
    const redraw = () => {
      const since = pendingSince();
      setAge(since ? Date.now() - since : -1);
    };
    const off = watchPending(redraw);
    const t = setInterval(redraw, 500);
    return () => { off(); clearInterval(t); };
  }, []);
  if (age < 600) return null;
  return (
    <>
      <div className="progress progress-top" role="progressbar" aria-label="Working"><span /></div>
      {age > 6000 && (
        <div role="status" className="fixed bottom-4 left-1/2 z-50 -translate-x-1/2 card px-4 py-2 text-sm shadow">
          Still working… {Math.round(age / 1000)}s. Some steps take up to a minute.
        </div>
      )}
    </>
  );
}
