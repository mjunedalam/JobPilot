import { useState, type FormEvent } from "react";
import { useAnswerDecision, type Decision } from "../../api/queries";
import { Badge, Button, ErrorNote, Field } from "../ui";

interface Question {
  key: string;
  label: string;
  kind: string;
  options: string[];
  suggestion: string;
  confidence: number;
  source: string;
}

export function H4Questions({ decision }: { decision: Decision }) {
  const questions = (decision.payload.questions ?? []) as Question[];
  const job = (decision.payload.job ?? {}) as { title?: string; company?: string };
  const [values, setValues] = useState<Record<string, string>>(
    Object.fromEntries(questions.map((q) => [q.key, q.suggestion ?? ""])),
  );
  const [save, setSave] = useState<string[]>(questions.map((q) => q.key));
  const answer = useAnswerDecision();
  const complete = questions.every((q) => (values[q.key] ?? "").trim());

  function submit(event: FormEvent) {
    event.preventDefault();
    answer.mutate({ decision, answer: { action: "answer", values, save } });
  }

  return (
    <form className="decision-body" onSubmit={submit}>
      <p className="muted">
        {job.title} at {job.company} asks for something JobPilot can't answer from your data
        with confidence. It never guesses; you decide.
      </p>
      {questions.map((q) => (
        <div key={q.key} className="h4-question">
          <Field
            label={q.label}
            hint={q.suggestion ? (
              <>Suggested from <Badge tone="sky">{q.source}</Badge> at {Math.round(q.confidence * 100)}% confidence</>
            ) : "No suggestion: nothing in your profile, resume or answers covers this."}
          >
            {q.options.length > 0 ? (
              <select className="input" value={values[q.key] ?? ""} onChange={(e) => setValues({ ...values, [q.key]: e.target.value })}>
                <option value="">Choose…</option>
                {q.options.map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
            ) : q.kind === "textarea" ? (
              <textarea className="input" rows={4} value={values[q.key] ?? ""}
                        onChange={(e) => setValues({ ...values, [q.key]: e.target.value })} />
            ) : (
              <input className="input" value={values[q.key] ?? ""} onChange={(e) => setValues({ ...values, [q.key]: e.target.value })} />
            )}
          </Field>
          <label className="check-inline">
            <input type="checkbox" checked={save.includes(q.key)}
                   onChange={(e) => setSave(e.target.checked ? [...save, q.key] : save.filter((k) => k !== q.key))} />
            Save to my Answer Bank for future applications
          </label>
        </div>
      ))}
      <div className="decision-actions">
        <Button data-hotkey="reject" variant="ghost" busy={answer.isPending} onClick={() => answer.mutate({ decision, answer: { action: "skip" } })}>
          Skip this job
        </Button>
        <span className="spacer" />
        <Button data-hotkey="approve" variant="primary" type="submit" disabled={!complete} busy={answer.isPending}>Continue</Button>
      </div>
      <ErrorNote error={answer.error} />
    </form>
  );
}
