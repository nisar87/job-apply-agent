"use client";

import { GlobalProgress } from "@/components/Progress";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, getToken, loginPath, setToken } from "@/lib/api";

type Me = { id: string; email: string; name: string; is_admin?: boolean; environment?: string; sends_for_real?: boolean };

const NAV: { group: string; items: { href: string; label: string }[] }[] = [
  { group: "Main", items: [
    { href: "/", label: "Overview" }, { href: "/applications", label: "Applications" }, { href: "/inbox", label: "Replies" },
  ] },
  { group: "Your agent", items: [
    { href: "/campaigns", label: "My search" }, { href: "/cvs", label: "My CV" }, { href: "/sources", label: "Job sources" },
  ] },
  { group: "Account", items: [
    { href: "/settings", label: "Settings" }, { href: "/account", label: "Account and keys" },
  ] },
];

export default function Shell({ title, intro, children, actions }: { title: string; intro?: string; children: React.ReactNode; actions?: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [mailBroken, setMailBroken] = useState(false);
  const [noKey, setNoKey] = useState("");
  const [signedIn, setSignedIn] = useState(false);

  useEffect(() => {
    if (!getToken()) {
      router.replace(loginPath());
      return;
    }
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSignedIn(true);
    api<Me>("/api/auth/me").then(setMe).catch(() => undefined);
    api<{ status: string }[]>("/api/mail/account")
      .then((accounts) => setMailBroken(accounts.some((a) => a.status === "error")))
      .catch(() => undefined);
    api<{ source: string; message: string }>("/api/account/model-key")
      .then((k) => setNoKey(k.source === "none" ? k.message || "No Gemini API key is set." : ""))
      .catch(() => undefined);
  }, [router]);

  const nav = me?.is_admin ? [...NAV, { group: "Admin", items: [{ href: "/operator", label: "Operator" }] }] : NAV;

  function signOut() {
    setToken(null);
    router.replace("/login");
  }

  // A signed-out visitor never sees an internal page, not even its empty frame: the page below is not
  // mounted until a sign-in is present, so none of its requests start either.
  if (!signedIn) {
    return <p className="p-8 text-sm text-slate" role="status">Taking you to sign in…</p>;
  }

  return (
    <div className="mx-auto flex min-h-screen max-w-6xl flex-col md:flex-row">
      <nav className="border-b border-rule px-4 py-4 md:w-56 md:shrink-0 md:border-b-0 md:border-r md:py-8" aria-label="Main">
        <p className="letter text-lg">Job Apply Agent</p>
        <div className="mt-4 flex flex-wrap gap-x-6 gap-y-3 md:block md:space-y-5">
          {nav.map((g) => (
            <div key={g.group}>
              <p className="hidden px-2 text-xs font-semibold text-quiet md:block">{g.group}</p>
              <ul className="flex flex-wrap gap-x-2 text-sm md:mt-1 md:block md:space-y-0.5">
                {g.items.map((item) => {
                  const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
                  return (
                    <li key={item.href}>
                      <Link href={item.href} aria-current={active ? "page" : undefined}
                        className={`block rounded-md px-2 py-2 md:py-1.5 ${active ? "bg-ink font-semibold text-white" : "text-ink hover:bg-rule"}`}>
                        {item.label}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </div>
        <div className="mt-3 flex items-center gap-3 text-xs text-slate md:mt-6 md:block">
          {me && <p className="truncate">{me.email}</p>}
          <button className="py-2 underline md:mt-1 md:py-0" onClick={signOut}>Sign out</button>
        </div>
      </nav>
      <GlobalProgress />
      <main className="min-w-0 flex-1 px-4 py-6 md:px-8 md:py-8">
        {me && me.environment !== "production" && (
          <p className={`mb-4 rounded-lg border bg-sheet px-3 py-2 text-sm ${me.sends_for_real ? "border-problem" : "border-rule"}`} role="status">
            {me.sends_for_real
              ? "Test site, but this account is switched to real sending: its applications reach employers."
              : "Test site: nothing you send here reaches an employer. Applications go to a test inbox."}
          </p>
        )}
        {mailBroken && (
          <p className="mb-4 rounded-lg border border-problem bg-sheet px-3 py-2 text-sm" role="alert">
            Gmail stopped accepting the agent&apos;s sign-in, so nothing is being sent or read.{" "}
            <Link className="font-semibold underline" href="/account">Reconnect Gmail</Link> and your searches resume by themselves.
          </p>
        )}
        {noKey && (
          <p className="mb-4 rounded-lg border border-problem bg-sheet px-3 py-2 text-sm" role="alert">
            The agent is stopped: {noKey.replace(/^no key: /, "")}{" "}
            <Link className="font-semibold underline" href="/account">Add your key</Link>
          </p>
        )}
        <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1>{title}</h1>
            {intro && <p className="mt-1 max-w-xl text-sm text-slate">{intro}</p>}
          </div>
          {actions}
        </div>
        {children}
      </main>
    </div>
  );
}
