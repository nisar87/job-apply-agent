import Link from "next/link";

const STEPS = [
  { title: "Add your CV", text: "The agent reads it and works out what you do, where you may work and what suits you." },
  { title: "Connect Gmail", text: "Applications go out from this address, and employers' replies come back to it." },
  { title: "Start", text: "It looks for new jobs every few hours and applies to the ones that fit, up to a daily limit." },
];

/** What a signed-out visitor sees at the address: one sentence, three steps, two ways in. */
export default function FrontDoor() {
  return (
    <main className="mx-auto max-w-3xl px-4 py-16">
      <h1 className="text-4xl leading-tight">An agent that finds jobs that fit your CV and applies for you.</h1>
      <p className="mt-4 max-w-prose text-lg text-slate">You give it your CV once. It does the searching, the forms and the emails, and tells you only what needs you.</p>
      <ol className="mt-10 space-y-5">
        {STEPS.map((s, i) => (
          <li key={s.title} className="flex gap-4">
            <span className="letter text-3xl text-slate" aria-hidden="true">{i + 1}</span>
            <div><h2 className="text-xl">{s.title}</h2><p className="text-sm text-slate">{s.text}</p></div>
          </li>
        ))}
      </ol>
      <div className="mt-10 flex flex-wrap gap-3">
        <Link className="btn" href="/signup">Create your account</Link>
        <Link className="btn btn-quiet" href="/login">Sign in</Link>
      </div>
      <p className="mt-6 text-sm text-slate">Accounts are currently by invitation. If you do not have one yet, create an account and you will see how to ask for one.</p>
    </main>
  );
}
