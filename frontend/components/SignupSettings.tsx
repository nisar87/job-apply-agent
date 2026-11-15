"use client";

import { useState } from "react";
import { ErrorNote, errorText, useLoad } from "@/components/ui";
import { api } from "@/lib/api";

type Signup = { mode: "invite" | "open"; invites: string[]; fixed_invites: string[] };

export default function SignupSettings() {
  const s = useLoad<Signup>("/api/operator/signup");
  const [adding, setAdding] = useState("");
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const signupUrl = typeof window !== "undefined" ? `${window.location.origin}/signup` : "/signup";

  async function save(json: object) {
    setError("");
    try {
      await api("/api/operator/signup", { method: "PUT", json });
      s.reload();
      return true;
    } catch (e) {
      setError(errorText(e));
      return false;
    }
  }

  if (!s.data) return null;
  const d = s.data;
  return (
    <section className="mb-8">
      <h2 className="text-xl">Who can sign up</h2>
      <ErrorNote message={error} />
      <div className="mt-3 grid gap-2 md:grid-cols-2">
        {([["invite", "Only people I invite", "Others see “Sign-up is by invitation only”."],
          ["open", "Anyone", "Anyone with the link can create an account."]] as const).map(([value, label, detail]) => (
          <label key={value} className={`cursor-pointer border p-3 text-sm ${d.mode === value ? "border-ink bg-sheet" : "border-rule"}`}>
            <span className="flex items-center gap-2 font-semibold">
              <input type="radio" name="signup" checked={d.mode === value} onChange={() => save({ mode: value })} />{label}
            </span>
            <span className="mt-1 block text-slate">{detail}</span>
          </label>
        ))}
      </div>
      {d.mode === "invite" && (
        <div className="mt-4 text-sm">
          <form className="flex flex-wrap items-end gap-2" onSubmit={async (e) => {
            e.preventDefault();
            const add = adding.split(/[\s,;]+/).filter(Boolean);
            if (add.length && await save({ add })) setAdding("");
          }}>
            <label className="min-w-64 flex-1">Invite by email (several at once is fine)
              <input className="field mt-1" value={adding} onChange={(e) => setAdding(e.target.value)} placeholder="friend@example.com" />
            </label>
            <button className="btn">Invite</button>
          </form>
          <p className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate">
            Then send them the sign-up link: <code>{signupUrl}</code>
            <button type="button" className="underline" onClick={() => { navigator.clipboard?.writeText(signupUrl); setCopied(true); }}>{copied ? "Copied" : "Copy"}</button>
          </p>
          <ul className="mt-3 space-y-1">
            {d.fixed_invites.map((e) => <li key={e} className="border-b border-rule py-1">{e} <span className="text-xs text-slate">(always invited)</span></li>)}
            {d.invites.map((e) => (
              <li key={e} className="flex items-center justify-between border-b border-rule py-1">
                <span>{e}</span>
                <button className="underline" onClick={() => save({ remove: [e] })}>Remove</button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
