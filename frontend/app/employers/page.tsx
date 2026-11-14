"use client";

import { useState } from "react";
import { ErrorNote, errorText } from "@/components/ui";
import { api } from "@/lib/api";

export default function Employers() {
  const [email, setEmail] = useState("");
  const [company, setCompany] = useState("");
  const [message, setMessage] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await api("/api/public/block-request", { method: "POST", json: { contact_email: email, company, message } });
      setDone(true);
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <main className="mx-auto mt-16 w-full max-w-lg px-4">
      <h1 className="text-3xl">For employers</h1>
      <p className="mt-3 text-sm leading-6 text-slate">
        People use Job Apply Agent to apply to openings you have published, from their own email accounts. If you do not want
        applications sent this way, tell us the address they arrive at. Once we have checked the request, no user of this
        service will be able to write to that address or its domain again.
      </p>
      {done ? (
        <p className="mt-6 border-l-4 border-sent bg-sheet px-4 py-3 text-sm">We have your request and will act on it after a short check.</p>
      ) : (
        <form onSubmit={submit} className="mt-6 space-y-4">
          <label className="block text-sm">Address that received applications
            <input className="field mt-1" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </label>
          <label className="block text-sm">Company
            <input className="field mt-1" value={company} onChange={(e) => setCompany(e.target.value)} />
          </label>
          <label className="block text-sm">Anything we should know
            <textarea className="field mt-1 min-h-24" value={message} onChange={(e) => setMessage(e.target.value)} />
          </label>
          <ErrorNote message={error} />
          <button className="btn">Stop applications to this address</button>
        </form>
      )}
    </main>
  );
}
