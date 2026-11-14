"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, getToken, setToken } from "@/lib/api";

type Me = { id: string; email: string; name: string };

const NAV = [
  { href: "/", label: "Today" },
  { href: "/applications", label: "Applications" },
  { href: "/drafts", label: "Drafts" },
  { href: "/inbox", label: "Inbox" },
  { href: "/campaigns", label: "Searches" },
  { href: "/cvs", label: "CVs and facts" },
  { href: "/setup", label: "Setup chat" },
  { href: "/account", label: "Account" },
];

export default function Shell({ title, children, actions }: { title: string; children: React.ReactNode; actions?: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    api<Me>("/api/auth/me").then(setMe).catch(() => undefined);
  }, [router]);

  function signOut() {
    setToken(null);
    router.replace("/login");
  }

  return (
    <div className="mx-auto flex min-h-screen max-w-6xl flex-col md:flex-row">
      <nav className="border-b border-rule px-4 py-4 md:w-52 md:shrink-0 md:border-b-0 md:border-r md:py-8" aria-label="Main">
        <p className="letter text-lg">Job Apply Agent</p>
        <ul className="mt-4 flex flex-wrap gap-x-4 gap-y-1 text-sm md:block md:space-y-1">
          {NAV.map((item) => {
            const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
            return (
              <li key={item.href}>
                <Link
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  className={`block rounded px-2 py-1 ${active ? "bg-ink text-white" : "text-ink hover:bg-rule"}`}
                >
                  {item.label}
                </Link>
              </li>
            );
          })}
        </ul>
        <div className="mt-6 hidden text-xs text-slate md:block">
          {me && <p className="truncate">{me.email}</p>}
          <button className="mt-1 underline" onClick={signOut}>Sign out</button>
        </div>
      </nav>
      <main className="min-w-0 flex-1 px-4 py-6 md:px-8 md:py-8">
        <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
          <h1 className="text-3xl">{title}</h1>
          {actions}
        </div>
        {children}
      </main>
    </div>
  );
}
