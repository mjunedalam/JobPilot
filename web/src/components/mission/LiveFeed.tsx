import { useEffect, useState } from "react";
import { useActivity, type ActivityItem } from "../../api/queries";
import { useLiveEvents, useLiveStatus } from "../live/LiveProvider";
import { clockTime } from "./liveState";

const SHOW = 14;
const QUIET = ["apply.fill", "apply.click", "apply.proof"];

/** The audit trail as it happens: the first page from /activity, then live audit events. */
export function LiveFeed() {
  const initial = useActivity().data ?? [];
  const live = useLiveEvents(["audit"])
    .map((e) => e.data as unknown as ActivityItem)
    .filter((i) => !QUIET.some((t) => i.action === `tool:${t}`));
  const status = useLiveStatus();
  const [, tick] = useState(0);
  useEffect(() => {
    const t = window.setInterval(() => tick((n) => n + 1), 30_000);
    return () => window.clearInterval(t);
  }, []);
  const seen = new Set<number>();
  const items = [...live.reverse(), ...initial].filter((i) => (seen.has(i.id) ? false : (seen.add(i.id), true))).slice(0, SHOW);

  return (
    <section aria-labelledby="feed-title" className="mc-feed">
      <div className="mc-section-head">
        <h2 id="feed-title" className="mc-section-title">Live</h2>
        <span className={`mc-live mc-live-${status}`}><span aria-hidden className="mc-live-dot" />{status === "live" ? "streaming" : status}</span>
      </div>
      <ol className="mc-feed-list" aria-live="polite">
        {items.map((i) => (
          <li key={i.id} className={`mc-feed-item ${i.actor === "user" || i.actor === "telegram" ? "mc-feed-you" : ""}`}>
            <span className="mc-mono mc-muted">{clockTime(i.ts)}</span>
            <span>{i.text}</span>
          </li>
        ))}
        {items.length === 0 && <li className="mc-feed-item muted">Nothing has happened yet.</li>}
      </ol>
    </section>
  );
}
