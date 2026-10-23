import { Paperclip, Pencil, RefreshCw, Send, Trash2 } from "lucide-react";
import { useState } from "react";
import { useAnswerDecision, useResumes, type Decision } from "../../api/queries";
import { Badge, Button, ErrorNote, Field } from "../ui";
import { EmailHeader, type EmailSummary } from "./H6Email";

interface DraftVersion {
  n: number;
  versions: number;
  to: string[];
  cc: string[];
  subject: string;
  body: string;
  attachments: { resume_id: number; name: string; sha256: string }[];
  source: string;
  warnings: string[];
}

interface EditState {
  to: string;
  cc: string;
  subject: string;
  body: string;
  attachments: number[];
}

const list = (value: string) => value.split(/[,;\s]+/).map((v) => v.trim()).filter(Boolean);

function fromDraft(d: DraftVersion | null): EditState {
  return {
    to: d?.to.join(", ") ?? "",
    cc: d?.cc.join(", ") ?? "",
    subject: d?.subject ?? "",
    body: d?.body ?? "",
    attachments: d?.attachments.map((a) => a.resume_id) ?? [],
  };
}

function Editor({ decision, draft, onCancel }: { decision: Decision; draft: DraftVersion | null; onCancel?: () => void }) {
  const [edit, setEdit] = useState<EditState>(fromDraft(draft));
  const resumes = useResumes();
  const answer = useAnswerDecision();
  const set = (patch: Partial<EditState>) => setEdit({ ...edit, ...patch });

  function save() {
    const changes = draft
      ? { to: list(edit.to), cc: list(edit.cc), subject: edit.subject, body: edit.body, attachments: edit.attachments }
      : { body: edit.body, attachments: edit.attachments };
    answer.mutate({ decision, answer: { action: "edit", ...changes } });
  }

  return (
    <div className="reply-editor">
      {draft && (
        <>
          <Field label="To"><input className="input" value={edit.to} onChange={(e) => set({ to: e.target.value })} /></Field>
          <Field label="Cc" hint="Comma-separated; leave empty for none.">
            <input className="input" value={edit.cc} onChange={(e) => set({ cc: e.target.value })} />
          </Field>
          <Field label="Subject"><input className="input" value={edit.subject} maxLength={300}
                                        onChange={(e) => set({ subject: e.target.value })} /></Field>
        </>
      )}
      <Field label="Message">
        <textarea className="input reply-body-input" rows={10} maxLength={10000} value={edit.body}
                  onChange={(e) => set({ body: e.target.value })} />
      </Field>
      {(resumes.data ?? []).length > 0 && (
        <fieldset className="reply-attach">
          <legend>Attach</legend>
          {(resumes.data ?? []).map((r) => (
            <label key={r.id} className="check-inline">
              <input type="checkbox" checked={edit.attachments.includes(r.id)}
                     onChange={(e) => set({ attachments: e.target.checked
                       ? [...edit.attachments, r.id] : edit.attachments.filter((id) => id !== r.id) })} />
              {r.label} <span className="muted">({r.file_name})</span>
            </label>
          ))}
        </fieldset>
      )}
      <div className="decision-actions">
        {onCancel && <Button variant="ghost" onClick={onCancel}>Cancel</Button>}
        <span className="spacer" />
        <Button variant="primary" busy={answer.isPending} disabled={!edit.body.trim()} onClick={save}>
          Save as new version
        </Button>
      </div>
      <p className="muted">You'll see the new version again before anything is sent.</p>
      <ErrorNote error={answer.error} />
    </div>
  );
}

export function H7Reply({ decision }: { decision: Decision }) {
  const email = decision.payload.email as EmailSummary;
  const draft = (decision.payload.draft ?? null) as DraftVersion | null;
  const error = String(decision.payload.error ?? "");
  const allowed = (decision.payload.allowed ?? []) as string[];
  const [mode, setMode] = useState<"view" | "edit" | "regenerate">(draft ? "view" : "edit");
  const [instruction, setInstruction] = useState(String(decision.payload.instruction ?? ""));
  const answer = useAnswerDecision();

  return (
    <div className="decision-body">
      <EmailHeader email={email} />
      {error && <p className="tone-stop" role="alert">{error}</p>}

      {draft && mode !== "edit" && (
        <article className="reply-preview" aria-label="Reply draft">
          <header className="reply-head">
            <span className="eyebrow">Version {draft.n} of {draft.versions}</span>
            <Badge tone={draft.source === "user" ? "sky" : "neutral"}>
              {draft.source === "user" ? "your edit" : "drafted"}
            </Badge>
          </header>
          <dl className="reply-fields">
            <dt>To</dt><dd>{draft.to.join(", ")}</dd>
            {draft.cc.length > 0 && (<><dt>Cc</dt><dd>{draft.cc.join(", ")}</dd></>)}
            <dt>Subject</dt><dd>{draft.subject}</dd>
            {draft.attachments.length > 0 && (
              <><dt>Attached</dt><dd>{draft.attachments.map((a) => (
                <span key={a.resume_id} className="reply-file"><Paperclip size={13} /> {a.name}</span>
              ))}</dd></>
            )}
          </dl>
          <pre className="reply-body">{draft.body}</pre>
          {draft.warnings.map((w) => <p key={w} className="tone-amber reply-warning">⚠ {w}</p>)}
        </article>
      )}

      {mode === "edit" && (
        <Editor key={draft?.n ?? 0} decision={decision} draft={draft}
                onCancel={draft ? () => setMode("view") : undefined} />
      )}

      {mode === "regenerate" && (
        <Field label="What should change?" hint="Leave empty to write it again with the same instruction.">
          <textarea className="input" rows={2} maxLength={1000} value={instruction}
                    onChange={(e) => setInstruction(e.target.value)} />
        </Field>
      )}

      {mode !== "edit" && (
        <div className="decision-actions">
          <Button data-hotkey="reject" variant="danger" busy={answer.isPending}
                  onClick={() => window.confirm("Discard this reply? Nothing will be sent.")
                    && answer.mutate({ decision, answer: { action: "discard" } })}>
            <Trash2 size={15} /> Discard
          </Button>
          <span className="spacer" />
          {mode === "regenerate" ? (
            <>
              <Button variant="ghost" onClick={() => setMode("view")}>Cancel</Button>
              <Button busy={answer.isPending}
                      onClick={() => answer.mutate({ decision, answer: { action: "regenerate", instruction: instruction.trim() } })}>
                <RefreshCw size={15} /> Write it again
              </Button>
            </>
          ) : (
            <>
              <Button onClick={() => setMode("regenerate")}><RefreshCw size={15} /> Regenerate</Button>
              <Button data-hotkey="edit" onClick={() => setMode("edit")}><Pencil size={15} /> Edit</Button>
              {allowed.includes("approve") && (
                <Button data-hotkey="approve" variant="primary" busy={answer.isPending}
                        onClick={() => answer.mutate({ decision, answer: { action: "approve" } })}>
                  <Send size={15} /> Approve &amp; send
                </Button>
              )}
            </>
          )}
        </div>
      )}
      <ErrorNote error={answer.error} />
    </div>
  );
}
