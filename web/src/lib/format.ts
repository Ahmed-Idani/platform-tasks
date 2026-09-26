import { useEffect, useState } from "react";

/** Re-renders the caller every `ms`, for live timers ("running for 12s"). */
export function useNow(ms = 1000, enabled = true) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!enabled) return;
    const id = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(id);
  }, [ms, enabled]);
  return now;
}

const t = (iso: string | null | undefined) => (iso ? new Date(iso).getTime() : null);

/** 850ms · 4.2s · 3m 12s · 1h 04m */
export function duration(ms: number | null | undefined): string {
  if (ms == null || Number.isNaN(ms)) return "–";
  if (ms < 0) ms = 0;
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s < 10 ? s.toFixed(1) : Math.round(s)}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(Math.floor(s % 60)).padStart(2, "0")}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}

/** Gap between two timestamps; if `to` is missing and `live`, measures up to now. */
export function between(from: string | null | undefined, to: string | null | undefined, now?: number) {
  const a = t(from);
  if (a == null) return null;
  const b = t(to) ?? now ?? null;
  return b == null ? null : b - a;
}

export function relative(iso: string, now: number): string {
  const s = Math.round((now - new Date(iso).getTime()) / 1000);
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

const dateFmt = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

export function timestamp(iso: string | null | undefined): string {
  if (!iso) return "–";
  const d = new Date(iso);
  return `${dateFmt.format(d)}.${String(d.getMilliseconds()).padStart(3, "0")}`;
}

export const shortId = (id: string) => id.slice(0, 8) + "…" + id.slice(-4);

export const num = (n: number | null | undefined) => (n == null ? "–" : n.toLocaleString());
