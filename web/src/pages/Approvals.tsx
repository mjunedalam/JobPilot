import { CirclePlay, Focus } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import { useApplications, useDecisions, useStartApply, useWorkflows, type Decision } from "../api/queries";
import { AppRow } from "../components/approvals/AppRow";
import { IfYouApprove, JourneyPanel, useDecisionJobId } from "../components/approvals/DecisionContext";
import { H3Review } from "../components/approvals/H3Review";
import { H4Questions } from "../components/approvals/H4Questions";
import { H5Preview } from "../components/approvals/H5Preview";
import { H6Email } from "../components/approvals/H6Email";
import { H7Reply } from "../components/approvals/H7Reply";
import { UndoToast } from "../components/approvals/UndoToast";
import { Button, Empty, ErrorNote } from "../components/ui";
import { decisionMeta, describeDecision } from "../lib/notify";
import { undoStore } from "../lib/undo";

const TITLES: Record<string, { eyebrow: string; title: string }> = {
  H3: { eyebrow: "H3 · job approval", title: "Which jobs should JobPilot apply to?" },
  H4: { eyebrow: "H4 · a question needs you", title: "Answer before it continues" },
  H5: { eyebrow: "H5 · pre-submit preview", title: "Review the filled application" },
  H6: { eyebrow: "H6 · an email needs you", title: "Reply, or set it aside?" },
  H7: { eyebrow: "H7 · reply approval", title: "Approve the reply before it is sent" },
};

function DecisionBody({ decision }: { decision: Decision }) {
  const k = decision.interrupt_id;
  switch (decision.checkpoint) {
    case "H3": return <H3Review key={k} decision={decision} />;
    case "H4": return <H4Questions key={k} decision={decision} />;
    case "H5": return <H5Preview key={k} decision={decision} />;
    case "H6": return <H6Email key={k} decision={decision} />;
    case "H7": return <H7Reply key={k} decision={decision} />;
    default: return <p className="muted">Open this decision in its own page.</p>;
  }
}

/** A, R, E press the current decision's buttons; J/K move through the queue; U undoes. */
function useHotkeys(move: (delta: number) => void) {
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (target && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))) return;
      const key = event.key.toLowerCase();
      const press = (hotkey: string) => {
        const button = document.querySelector<HTMLButtonElement>(`.dc-current [data-hotkey="${hotkey}"]:not(:disabled)`);
        if (button) {
          event.preventDefault();
          button.click();
        }
      };
      if (key === "j") move(1);
      else if (key === "k") move(-1);
      else if (key === "a") press("approve");
      else if (key === "r") press("reject");
      else if (key === "e") press("edit");
      else if (key === "u") undoStore.undo();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [move]);
}

export function Approvals() {
  const workflows = useWorkflows();
  const applyRunning = (workflows.data ?? []).some((r) => r.kind === "apply" && r.status === "running");
  const decisions = useDecisions(false);
  const pending = decisions.data ?? [];
  const applications = useApplications(applyRunning);
  const start = useStartApply();
  const [params, setParams] = useSearchParams();
  const [focusMode, setFocusMode] = useState(false);
  const focus = params.get("focus");
  const current = pending.find((d) => d.interrupt_id === focus) ?? pending[0];
  const jobId = useDecisionJobId(current);

  const move = useCallback(
    (delta: number) => {
      if (!pending.length) return;
      const i = Math.max(0, pending.findIndex((d) => d.interrupt_id === current?.interrupt_id));
      const next = pending[(i + delta + pending.length) % pending.length];
      if (next) setParams({ focus: next.interrupt_id }, { replace: true });
    },
    [pending, current, setParams],
  );
  useHotkeys(move);
  const meta = current ? (TITLES[current.checkpoint] ?? { eyebrow: current.checkpoint, title: "Decision" }) : null;

  return (
    <div className={`page dc ${focusMode ? "dc-focus" : ""}`}>
      <header className="dc-head">
        <div>
          <p className="eyebrow">Cockpit</p>
          <h1 className="page-title">Decision Center</h1>
          <p className="muted">
            {pending.length ? `${pending.length} waiting · also on your phone` : "Nothing waiting"} ·
            {" "}<kbd>J</kbd>/<kbd>K</kbd> move · <kbd>A</kbd> approve · <kbd>R</kbd> reject · <kbd>E</kbd> edit · <kbd>U</kbd> undo
          </p>
        </div>
        <div className="dc-head-actions">
          <Button aria-pressed={focusMode} onClick={() => setFocusMode((f) => !f)}><Focus size={15} /> Focus</Button>
          <Button variant="primary" busy={start.isPending} disabled={applyRunning || pending.some((d) => d.kind === "apply")}
                  onClick={() => start.mutate()}>
            <CirclePlay size={16} /> Review found jobs
          </Button>
        </div>
      </header>
      <ErrorNote error={start.error ?? decisions.error} />

      <div className="dc-layout">
        {!focusMode && (
          <nav aria-label="Decisions waiting" className="dc-queue">
            {pending.map((d) => {
              const on = d.interrupt_id === current?.interrupt_id;
              const { title } = describeDecision(d.checkpoint, d.payload as Record<string, unknown>);
              return (
                <button key={d.interrupt_id} type="button" aria-current={on || undefined}
                        className={`dc-q dc-q-${d.checkpoint} ${on ? "dc-q-on" : ""}`}
                        onClick={() => setParams({ focus: d.interrupt_id }, { replace: true })}>
                  <span className="mc-tag">{d.checkpoint}</span>
                  <span className="dc-q-text">
                    <span className="dc-q-title">{title}</span>
                    <span className="dc-q-meta">{decisionMeta(d as { checkpoint: string; payload: Record<string, unknown> })}</span>
                  </span>
                </button>
              );
            })}
            {pending.length === 0 && <Empty title="All clear">Decisions appear here the moment an agent needs you.</Empty>}
          </nav>
        )}

        <main className="dc-main">
          {current && meta ? (
            <article className="dc-current panel decision" aria-labelledby="dc-title">
              <p className="eyebrow">{meta.eyebrow}</p>
              <h2 id="dc-title" className="panel-title">{meta.title}</h2>
              <DecisionBody decision={current} />
              <IfYouApprove checkpoint={current.checkpoint} />
            </article>
          ) : (
            <Empty title="Nothing needs you">When Autopilot or a review finds something, it lands here and on your phone.</Empty>
          )}
        </main>

        {!focusMode && (
          <aside className="dc-side" aria-label="Context">
            <JourneyPanel jobId={jobId} />
          </aside>
        )}
      </div>

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
      <UndoToast />
    </div>
  );
}
