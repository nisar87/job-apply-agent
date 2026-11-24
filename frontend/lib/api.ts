// Thin client for the backend API. The login token is kept in localStorage.

// "same-origin": the site and the API share one address (Firebase Hosting sends /api to the API), so the same
// build works on every domain the site is served from. Otherwise the API's full address, or local development.
const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const BASE = API_URL === "same-origin" ? "" : API_URL;
export const API_BASE = BASE;
const TOKEN_KEY = "jaa_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null) {
  try {
    if (token) window.localStorage.setItem(TOKEN_KEY, token);
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: the session lasts until the page reloads */
  }
}

/** The sign-in page, remembering where the user was going (for example the Drafts link in a notice email). */
export function loginPath(): string {
  if (typeof window === "undefined") return "/login";
  const here = window.location.pathname + window.location.search;
  return here === "/" || here.startsWith("/login") ? "/login" : `/login?next=${encodeURIComponent(here)}`;
}

/** Where to go after signing in: the remembered page if it is one of ours, otherwise the given default. */
export function afterLogin(fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const next = new URLSearchParams(window.location.search).get("next") || "";
  return next.startsWith("/") && !next.startsWith("//") ? next : fallback;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// Requests in flight, so the page can show that something is happening while a slow one runs.
type Pending = { id: number; since: number };
let pending: Pending[] = [];
let nextId = 1;
const watchers = new Set<() => void>();
export function watchPending(fn: () => void): () => void {
  watchers.add(fn);
  return () => { watchers.delete(fn); };
}
export const pendingSince = (): number | null => (pending.length ? Math.min(...pending.map((p) => p.since)) : null);
const track = (on: Pending | null, off?: Pending) => {
  if (on) pending = [...pending, on];
  if (off) pending = pending.filter((p) => p.id !== off.id);
  watchers.forEach((fn) => fn());
};

export async function api<T = unknown>(path: string, options: RequestInit & { json?: unknown } = {}): Promise<T> {
  const mark: Pending = { id: nextId++, since: Date.now() };
  track(mark);
  try {
    return await call<T>(path, options);
  } finally {
    track(null, mark);
  }
}

async function call<T>(path: string, options: RequestInit & { json?: unknown }): Promise<T> {
  const headers: Record<string, string> = { ...(options.headers as Record<string, string>) };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  let body = options.body;
  if (options.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.json);
  }
  const res = await fetch(`${BASE}${path}`, { ...options, headers, body });
  if (res.status === 401 && typeof window !== "undefined" && !path.startsWith("/api/auth/")) {
    setToken(null);
    window.location.replace(loginPath());
  }
  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    try {
      const data = await res.json();
      if (typeof data.detail === "string") message = data.detail;
      else if (Array.isArray(data.detail)) message = data.detail.map((d: { msg: string }) => d.msg).join("; ");
    } catch {
      /* keep the generic message */
    }
    throw new ApiError(res.status, message);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export function fmtDate(value?: string | null): string {
  if (!value) return "";
  return new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
