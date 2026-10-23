import { useApplications, useDecisions, useEmails, useJobs } from "../../api/queries";
import { useLiveEvents } from "../live/LiveProvider";
import { busyAgents } from "./liveState";

type State = "running" | "you" | "done" | "idle";

interface Stage {
  step: string;
  name: string;
  value: string;
  detail: string;
  state: State;
}

const STATE_LABEL: Record<State, string> = { running: "Running", you: "Needs you", done: "Done", idle: "Idle" };

function today(iso: string | null | undefined): boolean {
  if (!iso) return false;
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  return d.toDateString() === new Date().toDateString();
}

export function Pipeline() {
  const busy = busyAgents(useLiveEvents(["agent"]));
  const jobs = useJobs().data ?? [];
  const decisions = useDecisions(false).data ?? [];
  const apps = useApplications().data ?? [];
  const emails = useEmails("all").data ?? [];

  const flagged = jobs.filter((j) => j.red_flags.length > 0).length;
  const ranked = jobs.filter((j) => j.match_score != null).length;
  const appliedToday = apps.filter((a) => today(a.submitted_at)).length;
  const applying = apps.filter((a) => a.status === "applying").length;
  const needReply = emails.filter((e) => e.action_status === "needs_action" || e.action_status === "drafting").length;
  const replied = emails.filter((e) => e.action_status === "replied").length;
  const byCp = decisions.reduce<Record<string, number>>((acc, d) => ({ ...acc, [d.checkpoint]: (acc[d.checkpoint] ?? 0) + 1 }), {});

  const stages: Stage[] = [
    { step: "01", name: "Search", value: String(jobs.length), detail: busy.has("search_agent") ? "Reading job sources now" : `${jobs.length} jobs waiting for review`,
      state: busy.has("search_agent") ? "running" : jobs.length ? "done" : "idle" },
    { step: "02", name: "Rank", value: String(ranked), detail: flagged ? `${flagged} flagged as possible scams` : "Scored against your profile",
      state: busy.has("matching_agent") ? "running" : ranked ? "done" : "idle" },
    { step: "03", name: "Approve", value: String(decisions.length),
      detail: decisions.length ? Object.entries(byCp).map(([cp, n]) => `${cp} ×${n}`).join(" · ") : "Nothing waiting for you",
      state: decisions.length ? "you" : "idle" },
    { step: "04", name: "Apply", value: String(appliedToday), detail: applying ? `${applying} in progress in Chrome` : `${appliedToday} submitted today`,
      state: busy.has("application_agent") || applying ? "running" : appliedToday ? "done" : "idle" },
    { step: "05", name: "Replies", value: String(needReply), detail: needReply ? `${needReply} emails need you · ${replied} replied` : `${replied} replied`,
      state: busy.has("reply_drafting") || busy.has("email_monitor") ? "running" : needReply ? "you" : replied ? "done" : "idle" },
  ];

  return (
    <section aria-label="Pipeline" className="mc-pipeline">
      {stages.map((s) => (
        <article key={s.step} className={`mc-stage mc-stage-${s.state}`}>
          <div className="mc-stage-top">
            <span className="mc-mono mc-muted">{s.step}</span>
            <span className={`mc-mono mc-state mc-state-${s.state}`}>{STATE_LABEL[s.state]}</span>
          </div>
          <h3 className="mc-stage-name">{s.name}</h3>
          <p className="mc-stage-value">{s.value}</p>
          <div className={`mc-bar mc-bar-${s.state}`} role="progressbar" aria-label={`${s.name}: ${STATE_LABEL[s.state]}`}
               aria-valuemin={0} aria-valuemax={100} aria-valuenow={s.state === "running" ? undefined : s.state === "idle" ? 0 : 100}>
            <span />
          </div>
          <p className="mc-stage-detail">{s.detail}</p>
        </article>
      ))}
    </section>
  );
}
