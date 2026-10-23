import { Play } from "lucide-react";
import { useAutopilot, useRunAutopilot, useSaveAutopilot, type AutopilotSettings } from "../../api/queries";
import { Button, ErrorNote } from "../ui";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const RECENCY: Record<string, string> = { "1h": "last hour", "24h": "last 24 hours", "7d": "last 7 days", "30d": "last 30 days" };

function when(iso: string | null | undefined, tz: string): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit", timeZone: tz });
}

export function AutopilotCard() {
  const ap = useAutopilot();
  const save = useSaveAutopilot();
  const run = useRunAutopilot();
  const data = ap.data;
  if (!data) return <section className="mc-card mc-autopilot" aria-busy="true" />;
  const s = { ...data.settings, days: data.settings.days ?? [] };
  const set = (patch: Partial<AutopilotSettings>) => save.mutate({ ...s, ...patch });
  const last = data.last_run as { finished_at?: string; new_jobs?: number; review_started?: boolean; notes?: string[] } | null;

  return (
    <section className="mc-card mc-autopilot" aria-labelledby="ap-title">
      <header className="mc-autopilot-head">
        <div>
          <h2 id="ap-title" className="mc-card-title">Autopilot</h2>
          <p className="muted">
            {s.enabled ? `Next run ${when(data.next_run, data.timezone)} · decisions come to you` : "Off · searches only when you start one"}
          </p>
        </div>
        <button type="button" className={`switch ${s.enabled ? "switch-on" : ""}`} aria-pressed={s.enabled}
                aria-label={s.enabled ? "Turn Autopilot off" : "Turn Autopilot on"}
                onClick={() => set({ enabled: !s.enabled })} disabled={save.isPending}>
          <span className="switch-knob" />
        </button>
      </header>

      <div className="mc-autopilot-grid">
        <label className="mc-field">
          <span>Time ({data.timezone})</span>
          <input className="input" type="time" value={s.time} onChange={(e) => e.target.value && set({ time: e.target.value })} />
        </label>
        <label className="mc-field">
          <span>Look back</span>
          <select className="input" value={s.recency} onChange={(e) => set({ recency: e.target.value })}>
            {Object.entries(RECENCY).map(([v, label]) => <option key={v} value={v}>{label}</option>)}
          </select>
        </label>
      </div>
      <div className="mc-days" role="group" aria-label="Days">
        {DAYS.map((d, i) => {
          const on = s.days.includes(i);
          return (
            <button key={d} type="button" aria-pressed={on} className={`mc-day ${on ? "mc-day-on" : ""}`}
                    onClick={() => set({ days: on ? s.days.filter((x) => x !== i) : [...s.days, i].sort() })}>
              {d}
            </button>
          );
        })}
      </div>
      <label className="check-inline">
        <input type="checkbox" checked={s.check_mail} onChange={(e) => set({ check_mail: e.target.checked })} />
        Also check my mailbox
      </label>

      <footer className="mc-autopilot-foot">
        <span className="muted">
          {last?.finished_at
            ? `Last run ${when(last.finished_at, data.timezone)} · ${last.new_jobs ?? 0} new${last.review_started ? " · shortlist sent to you" : ""}`
            : "Never run yet"}
        </span>
        <Button busy={run.isPending || data.running} onClick={() => run.mutate()}>
          <Play size={14} /> {data.running ? "Running…" : "Run now"}
        </Button>
      </footer>
      {last?.notes && last.notes.length > 0 && <p className="mc-note">{last.notes.join(" · ")}</p>}
      <ErrorNote error={save.error ?? run.error} />
    </section>
  );
}
