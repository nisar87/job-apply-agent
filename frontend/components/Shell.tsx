"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, getToken, loginPath, setToken } from "@/lib/api";

type Me = { id: string; email: string; name: string; is_admin?: boolean; environment?: string; sends_for_real?: boolean };

const NAV = [
  { href: "/", label: "Home" },
  { href: "/applications", label: "Applications" },
  { href: "/settings", label: "Settings" },
];

export default function Shell({ title, children, actions }: { title: string; children: React.ReactNode; actions?: React.ReactNode }) {
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
    setSignedIn(true);
    api<Me>("/api/auth/me").then(setMe).catch(() => undefined);
    api<{ status: string }[]>("/api/mail/account")
      .then((accounts) => setMailBroken(accounts.some((a) => a.status === "error")))
      .catch(() => undefined);
    api<{ source: string; message: string }>("/api/account/model-key")
      .then((k) => setNoKey(k.source === "none" ? k.message || "No Gemini API key is set." : ""))
      .catch(() => undefined);
  }, [router]);

  const nav = me?.is_admin ? [...NAV, { href: "/operator", label: "Admin" }] : NAV;

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
      <nav className="border-b border-rule px-4 py-4 md:w-52 md:shrink-0 md:border-b-0 md:border-r md:py-8" aria-label="Main">
        <p className="letter text-lg">Job Apply Agent</p>
        <ul className="mt-4 flex flex-wrap gap-x-4 gap-y-1 text-sm md:block md:space-y-1">
          {nav.map((item) => {
            const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
            return (
              <li key={item.href}>
                <Link
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  className={`block rounded px-2 py-2 md:py-1 ${active ? "bg-ink text-white" : "text-ink hover:bg-rule"}`}
                >
                  {item.label}
                </Link>
              </li>
            );
          })}
        </ul>
        <div className="mt-3 flex items-center gap-3 text-xs text-slate md:mt-6 md:block">
          {me && <p className="truncate">{me.email}</p>}
          <button className="py-2 underline md:mt-1 md:py-0" onClick={signOut}>Sign out</button>
        </div>
      </nav>
      <main className="min-w-0 flex-1 px-4 py-6 md:px-8 md:py-8">
        {me && me.environment !== "production" && (
          <p className={`mb-4 border-l-4 bg-sheet px-3 py-2 text-sm ${me.sends_for_real ? "border-problem" : "border-rule"}`} role="status">
            {me.sends_for_real
              ? "Test site, but this account is switched to real sending: its applications reach employers."
              : "Test site: nothing you send here reaches an employer. Applications go to a test inbox."}
          </p>
        )}
        {mailBroken && (
          <p className="mb-4 border-l-4 border-problem bg-sheet px-3 py-2 text-sm" role="alert">
            Gmail stopped accepting the agent&apos;s sign-in, so nothing is being sent or read.{" "}
            <Link className="font-semibold underline" href="/account">Reconnect Gmail</Link> and your searches resume by themselves.
          </p>
        )}
        {noKey && (
          <p className="mb-4 border-l-4 border-problem bg-sheet px-3 py-2 text-sm" role="alert">
            The agent is stopped: {noKey.replace(/^no Gemini API key: /, "there is no Gemini API key to run on. ")}{" "}
            <Link className="font-semibold underline" href="/account">Add your key</Link>
          </p>
        )}
        <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
          <h1 className="text-3xl">{title}</h1>
          {actions}
        </div>
        {children}
      </main>
    </div>
  );
}
