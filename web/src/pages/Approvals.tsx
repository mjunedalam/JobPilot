import { CirclePlay, Image as ImageIcon } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import {
  fetchProof,
  flowKeys,
  useApplications,
  useDecisions,
  useStartApply,
  useWorkflows,
  type ApplicationRow,
  type Decision,
} from "../api/queries";
import { H3Review } from "../components/approvals/H3Review";
import { H4Questions } from "../components/approvals/H4Questions";
import { H5Preview } from "../components/approvals/H5Preview";
import { Badge, Button, Empty, ErrorNote, Lamp, Panel } from "../components/ui";
import { relativeTime } from "../lib/format";

const TITLES: Record<string, { eyebrow: string; title: string }> = {
  H3: { eyebrow: "H3 · job approval", title: "Which jobs should JobPilot apply to?" },
  H4: { eyebrow: "H4 · a question needs you", title: "Answer before it continues" },
  H5: { eyebrow: "H5 · pre-submit preview", title: "Review the filled application" },
};

const STATUS_TONE: Record<string, "go" | "amber" | "stop" | "sky" | "neutral"> = {
  applied: "go", applying: "sky", failed: "stop", skipped: "neutral",
};

function DecisionCard({ decision }: { decision: Decision }) {
  const meta = TITLES[decision.checkpoint] ?? { eyebrow: decision.checkpoint, title: "Decision" };
  return (
    <Panel eyebrow={meta.eyebrow} title={meta.title} className="decision">
      {decision.checkpoint === "H3" && <H3Review key={decision.interrupt_id} decision={decision} />}
      {decision.checkpoint === "H4" && <H4Questions key={decision.interrupt_id} decision={decision} />}
      {decision.checkpoint === "H5" && <H5Preview key={decision.interrupt_id} decision={decision} />}
    </Panel>
  );
}

function AppRow({ app }: { app: ApplicationRow }) {
  const [src, setSrc] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  return (
    <article className="app-row">
      <div>
        <h3 className="app-title">{app.title}</h3>
        <p className="muted">{app.company} · {app.portal} · attempt {app.attempts}
          {app.submitted_at ? ` · ${relativeTime(app.submitted_at)}` : ""}</p>
        {app.confirmation_text && <p className="app-confirm">“{app.confirmation_text}”</p>}
        {app.error && <p className="tone-stop app-error">{app.error}</p>}
      </div>
      <div className="app-side">
        <Badge tone={STATUS_TONE[app.status] ?? "neutral"}>{app.status}</Badge>
        {app.has_proof && (
          <Button variant="ghost" aria-expanded={Boolean(src)}
                  onClick={() => (src ? setSrc(null) : fetchProof(app.id).then(setSrc, setError))}>
            <ImageIcon size={14} /> {src ? "Hide proof" : "Proof"}
          </Button>
        )}
      </div>
      <ErrorNote error={error} />
      {src && <img className="proof" src={src} alt={`Screenshot of the submitted application for ${app.title}`} />}
    </article>
  );
}

export function Approvals() {
  const workflows = useWorkflows();
  const running = (workflows.data ?? []).some((r) => r.status === "running");
  const decisions = useDecisions(running);
  const applications = useApplications(running);
  const client = useQueryClient();
  const wasRunning = useRef(running);
  useEffect(() => {
    // A run can finish between two polls: fetch the final state once when it stops.
    if (wasRunning.current && !running) {
      void client.invalidateQueries({ queryKey: flowKeys.applications });
      void client.invalidateQueries({ queryKey: flowKeys.decisions });
    }
    wasRunning.current = running;
  }, [running, client]);
  const start = useStartApply();
  const latest = workflows.data?.[0];
  const pending = decisions.data ?? [];

  return (
    <div className="page">
      <header className="page-head">
        <p className="eyebrow">Cockpit</p>
        <h1 className="page-title">Approvals</h1>
        <p className="page-lede">
          Every application stops here for you: which jobs (H3), anything it can't answer
          confidently (H4), and the exact form before it is sent (H5). Decisions survive restarts.
        </p>
      </header>

      <Panel eyebrow="Run" title={running ? "Working…" : pending.length ? "Waiting for you" : "Ready"}
             actions={<Button variant="primary" busy={start.isPending} disabled={running || pending.length > 0}
                              onClick={() => start.mutate()}><CirclePlay size={16} /> Review found jobs</Button>}>
        {latest ? (
          <p className="run-line">
            <Lamp tone={latest.status === "failed" ? "stop" : latest.status === "done" ? "go" : "amber"}
                  label={latest.status} />
            <span className="muted">started {relativeTime(latest.created_at)}</span>
            {latest.summary?.applied_count != null && <span>· {String(latest.summary.applied_count)} applied</span>}
            {latest.error && <span className="tone-stop">· {latest.error}</span>}
          </p>
        ) : (
          <p className="muted">No runs yet. Search for jobs, then start a review.</p>
        )}
        <ErrorNote error={start.error ?? workflows.error} />
      </Panel>

      <ErrorNote error={decisions.error} />
      {pending.map((d) => <DecisionCard key={d.interrupt_id} decision={d} />)}

      <section className="section" aria-labelledby="apps-title">
        <div className="section-head">
          <h2 id="apps-title" className="section-title">Applications</h2>
          <span className="muted">{applications.data?.length ?? 0}</span>
        </div>
        {applications.data?.length === 0 && <Empty title="Nothing applied yet" />}
        <div className="app-list">
          {(applications.data ?? []).map((a) => <AppRow key={a.id} app={a} />)}
        </div>
      </section>
    </div>
  );
}
