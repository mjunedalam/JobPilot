import { useState, type FormEvent } from "react";
import { useAddAnswer, useAnswers } from "../api/queries";
import { Badge, Button, Empty, ErrorNote, Field, Panel } from "../components/ui";

const STARTERS = [
  "Why are you looking for a change?",
  "Are you willing to relocate?",
  "Are you comfortable with a hybrid schedule?",
  "When can you join?",
];

export function Answers() {
  const answers = useAnswers();
  const add = useAddAnswer();
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [tags, setTags] = useState("");

  function submit(event: FormEvent) {
    event.preventDefault();
    add.mutate(
      {
        question: question.trim(),
        answer: answer.trim(),
        tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
      },
      {
        onSuccess: () => {
          setQuestion("");
          setAnswer("");
          setTags("");
        },
      },
    );
  }

  const existing = new Set((answers.data ?? []).map((a) => a.question.toLowerCase()));

  return (
    <div className="page">
      <header className="page-head">
        <p className="eyebrow">Pilot</p>
        <h1 className="page-title">Answer bank</h1>
        <p className="page-lede">
          Reusable answers to screening questions. The Application agent uses only answers you
          confirmed here, and asks you about anything new.
        </p>
      </header>

      <div className="answers-grid">
        <Panel eyebrow="New answer" title="Teach it once">
          <form onSubmit={submit} className="stack">
            <Field label="Question">
              <input className="input" value={question} onChange={(e) => setQuestion(e.target.value)} required minLength={3} />
            </Field>
            <div className="starters">
              {STARTERS.filter((s) => !existing.has(s.toLowerCase())).map((s) => (
                <button type="button" key={s} className="chip" onClick={() => setQuestion(s)}>
                  {s}
                </button>
              ))}
            </div>
            <Field label="Your answer">
              <textarea className="input" rows={4} value={answer} onChange={(e) => setAnswer(e.target.value)} required />
            </Field>
            <Field label="Tags" hint="Optional, comma separated">
              <input className="input" value={tags} onChange={(e) => setTags(e.target.value)} />
            </Field>
            <ErrorNote error={add.error} />
            <Button variant="primary" type="submit" busy={add.isPending}>
              Save answer
            </Button>
          </form>
        </Panel>

        <section className="answer-list" aria-label="Saved answers">
          <ErrorNote error={answers.error} />
          {answers.data?.length === 0 && (
            <Empty title="No answers yet">Start with the suggestions on the left.</Empty>
          )}
          {(answers.data ?? []).map((a) => (
            <article key={a.id} className="answer">
              <h3 className="answer-q">{a.question}</h3>
              <p className="answer-a">{a.answer}</p>
              <footer className="answer-meta">
                {a.tags.map((t) => (
                  <Badge key={t}>{t}</Badge>
                ))}
                <span className="muted mono">used {a.times_used}×</span>
              </footer>
            </article>
          ))}
        </section>
      </div>
    </div>
  );
}
