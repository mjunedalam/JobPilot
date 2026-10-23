import type { LiveEvent } from "../../lib/live";

/** Agent spec names as published by the harness → the pipeline step they belong to. */
export const AGENT_STEP: Record<string, string> = {
  search_agent: "search",
  matching_agent: "rank",
  application_agent: "apply",
  email_monitor: "replies",
  reply_drafting: "replies",
  profile_agent: "profile",
};

/** Which agents are mid-run right now, from the latest start/done/error events. */
export function busyAgents(events: LiveEvent[]): Set<string> {
  const last = new Map<string, string>();
  for (const e of events) {
    if (e.kind === "agent" && typeof e.data.agent === "string") last.set(e.data.agent, String(e.data.phase));
  }
  return new Set([...last].filter(([, phase]) => phase === "start").map(([agent]) => agent));
}

export function relativeSeconds(ts: number | null | undefined, now = Date.now()): string {
  if (!ts) return "";
  const s = Math.max(0, Math.round((now - ts * 1000) / 1000));
  if (s < 10) return "now";
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  if (s < 86_400) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86_400)}d`;
}

export function clockTime(ts: number | null | undefined): string {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
