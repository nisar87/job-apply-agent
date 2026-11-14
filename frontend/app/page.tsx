"use client";

import { useEffect, useState } from "react";
import Shell from "@/components/Shell";
import { api } from "@/lib/api";

type Health = { ok: boolean; api: string; database: string; redis: string };

export default function Home() {
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    api<Health>("/api/health").then(setHealth).catch(() => setHealth(null));
  }, []);

  return (
    <Shell title="Today">
      <p className="text-sm text-slate">
        {health ? (health.ok ? "All services are running." : "A service is not responding. Check the server.") : "Checking services…"}
      </p>
    </Shell>
  );
}
