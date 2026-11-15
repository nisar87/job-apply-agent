"use client";

import { useState } from "react";
import { ErrorNote, errorText, useLoad } from "@/components/ui";
import { api } from "@/lib/api";

type Account = { id: string; address: string; provider: string; status: string };

/** Connect a Gmail used only for job hunting, with a Google app password. */
export default function JobGmail() {
  const mail = useLoad<Account[]>("/api/mail/account");
  const [address, setAddress] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const accounts = mail.data || [];
  const needsFix = accounts.some((a) => a.status !== "connected");
  const showForm = accounts.length === 0 || needsFix;

  async function connect(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setNote("");
    try {
      await api("/api/mail/app-password", { method: "POST", json: { address, app_password: code } });
      setCode("");
      setNote("Connected. The agent will send your applications from this address.");
      mail.reload();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function disconnect(id: string) {
    setError("");
    try {
      await api(`/api/mail/account/${id}`, { method: "DELETE" });
      mail.reload();
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <section>
      <h2 className="text-xl">Your job Gmail</h2>
      <p className="mt-1 max-w-prose text-sm text-slate">
        Applications are sent from a Gmail address you use only for job hunting, and employers&apos; replies come back there.
        Keep your personal email out of it.
      </p>
      <ErrorNote message={error} />
      <ul className="mt-3 space-y-2 text-sm">
        {accounts.map((a) => (
          <li key={a.id} className="flex flex-wrap items-center justify-between gap-2 border border-rule bg-sheet p-3">
            <span>{a.address} <span className={`postmark ml-2 ${a.status === "connected" ? "text-sent" : "text-problem"}`}>
              {a.status === "connected" ? "Connected" : "Needs a new app password"}</span></span>
            <button className="btn btn-quiet" onClick={() => disconnect(a.id)}>Disconnect</button>
          </li>
        ))}
      </ul>
      {mail.data && showForm && (
        <div className="mt-4 grid max-w-3xl gap-6 md:grid-cols-2">
          <ol className="list-decimal space-y-2 pl-5 text-sm">
            <li>Create a new Gmail just for job applications (for example <em>yourname.jobs@gmail.com</em>) and sign in to it.</li>
            <li>Turn on <a className="underline" href="https://myaccount.google.com/signinoptions/twosv" target="_blank" rel="noreferrer">2-Step Verification</a>.
              Google asks for a phone number; this is Google&apos;s rule.</li>
            <li>Open <a className="underline" href="https://myaccount.google.com/apppasswords" target="_blank" rel="noreferrer">App passwords</a>,
              type a name like <em>Job agent</em> and press Create. Copy the 16-letter code Google shows.</li>
            <li>Paste the address and the code here.</li>
          </ol>
          <form onSubmit={connect} className="space-y-3 text-sm">
            <label className="block">Job Gmail address
              <input className="field mt-1" type="email" autoComplete="off" value={address} onChange={(e) => setAddress(e.target.value)}
                placeholder="yourname.jobs@gmail.com" required />
            </label>
            <label className="block">App password (16 letters)
              <input className="field mt-1 font-mono" type="password" autoComplete="off" value={code} onChange={(e) => setCode(e.target.value)}
                placeholder="abcd efgh ijkl mnop" required />
            </label>
            <button className="btn" disabled={busy}>{busy ? "Checking with Gmail…" : "Connect"}</button>
            <p className="text-xs text-slate">
              This is not your Gmail password. The code only lets this app send and read mail, is stored encrypted, and you can
              cancel it any time on the App passwords page.
            </p>
          </form>
        </div>
      )}
      {note && <p className="mt-2 text-sm text-sent">{note}</p>}
    </section>
  );
}
