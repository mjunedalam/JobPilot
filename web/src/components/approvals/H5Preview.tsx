import { Pencil, Send } from "lucide-react";
import { useState } from "react";
import { useAnswerDecision, type Decision } from "../../api/queries";
import { Button, ErrorNote } from "../ui";

export function H5Preview({ decision }: { decision: Decision }) {
  const answers = (decision.payload.answers ?? {}) as Record<string, string>;
  const keys = (decision.payload.keys ?? {}) as Record<string, string>;
  const job = (decision.payload.job ?? {}) as { title?: string; company?: string; url?: string };
  const [editing, setEditing] = useState<Record<string, string> | null>(null);
  const answer = useAnswerDecision();
  const changed = editing ? Object.entries(editing).filter(([label, v]) => v !== answers[label]) : [];

  function sendEdits() {
    const values = Object.fromEntries(changed.map(([label, v]) => [keys[label] ?? label, v]));
    answer.mutate({ decision, answer: { action: "edit", values } });
  }

  return (
    <div className="decision-body">
      <p className="muted">
        This is exactly what will be submitted to <strong>{job.title}</strong> at {job.company}.
        Nothing is sent until you press Submit; if you edit, you'll see the preview again.
      </p>
      <dl className="h5-list">
        {Object.entries(answers).map(([label, value]) => (
          <div key={label} className="h5-item">
            <dt>{label}</dt>
            <dd>
              {editing && keys[label] && value !== "(resume file)" ? (
                <input className="input" aria-label={`Edit ${label}`} value={editing[label] ?? value}
                       onChange={(e) => setEditing({ ...editing, [label]: e.target.value })} />
              ) : (
                <span className={value === "(resume file)" ? "muted" : ""}>{value}</span>
              )}
            </dd>
          </div>
        ))}
      </dl>
      <div className="decision-actions">
        <Button variant="ghost" busy={answer.isPending} onClick={() => answer.mutate({ decision, answer: { action: "skip" } })}>
          Skip this job
        </Button>
        <span className="spacer" />
        {editing ? (
          <>
            <Button variant="ghost" onClick={() => setEditing(null)}>Cancel edits</Button>
            <Button disabled={!changed.length} busy={answer.isPending} onClick={sendEdits}>
              Apply {changed.length} edit(s)
            </Button>
          </>
        ) : (
          <>
            <Button onClick={() => setEditing({ ...answers })}><Pencil size={15} /> Edit</Button>
            <Button variant="primary" busy={answer.isPending}
                    onClick={() => answer.mutate({ decision, answer: { action: "submit" } })}>
              <Send size={15} /> Submit application
            </Button>
          </>
        )}
      </div>
      <ErrorNote error={answer.error} />
    </div>
  );
}
