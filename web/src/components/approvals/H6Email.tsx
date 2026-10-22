import { Check, EyeOff, PenLine } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useAnswerDecision, type Decision } from "../../api/queries";
import { relativeTime } from "../../lib/format";
import { Badge, Button, ErrorNote, Field } from "../ui";
import { CATEGORY_LABEL, CATEGORY_TONE } from "../inbox/categories";

export interface EmailSummary {
  email_id: number;
  sender: string;
  subject: string;
  summary: string;
  category: string;
  suggested_action: string;
  received_at: string;
  snippet: string;
  application: { id: number; title: string; company: string; status: string } | null;
}

export function EmailHeader({ email }: { email: EmailSummary }) {
  return (
    <div className="mail-card">
      <div className="mail-card-top">
        <Badge tone={CATEGORY_TONE[email.category] ?? "neutral"}>
          {CATEGORY_LABEL[email.category] ?? email.category}
        </Badge>
        <span className="muted">{relativeTime(email.received_at)}</span>
      </div>
      <p className="mail-from">{email.sender}</p>
      <h3 className="mail-subject">{email.subject}</h3>
      <p className="mail-summary">{email.summary}</p>
      {email.application && (
        <p className="muted mail-link">
          About your application: <strong>{email.application.title}</strong> at{" "}
          {email.application.company} · now <Badge tone="sky">{email.application.status}</Badge>
        </p>
      )}
    </div>
  );
}

export function H6Email({ decision }: { decision: Decision }) {
  const email = decision.payload.email as EmailSummary;
  const [instruction, setInstruction] = useState("");
  const answer = useAnswerDecision();

  function draft(event: FormEvent) {
    event.preventDefault();
    answer.mutate({ decision, answer: { action: "draft", instruction: instruction.trim() } });
  }

  return (
    <form className="decision-body" onSubmit={draft}>
      <EmailHeader email={email} />
      <p className="muted">Suggested: {email.suggested_action}</p>
      <Field
        label="How should the reply go?"
        hint="For example: “accept, propose Thursday 3–5 PM IST”. Nothing is sent until you approve the final text."
      >
        <textarea className="input" rows={2} maxLength={1000} value={instruction}
                  onChange={(e) => setInstruction(e.target.value)} />
      </Field>
      <div className="decision-actions">
        <Button variant="ghost" busy={answer.isPending}
                onClick={() => answer.mutate({ decision, answer: { action: "ignore" } })}>
          <EyeOff size={15} /> Ignore
        </Button>
        <Button variant="ghost" busy={answer.isPending}
                onClick={() => answer.mutate({ decision, answer: { action: "done" } })}>
          <Check size={15} /> Mark done
        </Button>
        <span className="spacer" />
        <Button type="submit" variant="primary" busy={answer.isPending}>
          <PenLine size={15} /> Draft a reply
        </Button>
      </div>
      <ErrorNote error={answer.error} />
    </form>
  );
}
