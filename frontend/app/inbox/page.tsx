"use client";

import Shell from "@/components/Shell";
import { Empty, ErrorNote, useLoad } from "@/components/ui";
import { api, fmtDate } from "@/lib/api";

type Note = { id: string; kind: string; title: string; body: string; link: string | null; read: boolean; created_at: string };

export default function Inbox() {
  const { data, error, reload } = useLoad<Note[]>("/api/notifications");
  async function read(id: string) {
    await api(`/api/notifications/${id}/read`, { method: "POST" }).catch(() => undefined);
    reload();
  }
  return (
    <Shell title="Inbox">
      <ErrorNote message={error} />
      {data && data.length === 0 && <Empty>Nothing needs you right now. Replies that need a decision appear here.</Empty>}
      <ul className="space-y-3">
        {(data || []).map((n) => (
          <li key={n.id} className={`border-l-4 bg-sheet p-4 text-sm ${n.read ? "border-rule" : "border-waiting"}`}>
            <p className="font-semibold">{n.title}</p>
            <p className="mt-1 whitespace-pre-wrap">{n.body}</p>
            <p className="mt-2 flex gap-4 text-xs text-slate">
              <span>{fmtDate(n.created_at)}</span>
              {n.link && <a className="underline" href={n.link}>Open</a>}
              {!n.read && <button className="underline" onClick={() => read(n.id)}>Mark as read</button>}
            </p>
          </li>
        ))}
      </ul>
    </Shell>
  );
}
