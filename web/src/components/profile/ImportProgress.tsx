import { Check, CircleAlert, Cpu, FileText, Sparkles, Upload, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import type { ImportJob } from "../../api/queries";
import { Badge, Button } from "../ui";

const STAGE_ICON: Record<string, ReactNode> = {
  upload: <Upload size={15} />,
  read: <FileText size={15} />,
  model: <Cpu size={15} />,
  save: <Sparkles size={15} />,
};

const FIELD_LABEL: Record<string, string> = {
  full_name: "Name",
  current_role: "Role",
  current_company: "Company",
  current_location: "Location",
  email: "Email",
  phone: "Phone",
};

/** Seconds since the job started, ticking smoothly between the twice-a-second polls. */
function useLiveElapsed(job: ImportJob): number {
  const [base, setBase] = useState({ elapsed: job.elapsed_s, at: Date.now() });
  const [now, setNow] = useState(Date.now());
  useEffect(() => setBase({ elapsed: job.elapsed_s, at: Date.now() }), [job.elapsed_s]);
  useEffect(() => {
    if (job.status !== "running") return;
    const timer = window.setInterval(() => setNow(Date.now()), 100);
    return () => window.clearInterval(timer);
  }, [job.status]);
  return job.status === "running" ? base.elapsed + (now - base.at) / 1000 : job.elapsed_s;
}

const clock = (s: number) => (s < 60 ? `${s.toFixed(0)}s` : `${Math.floor(s / 60)}m ${String(Math.floor(s % 60)).padStart(2, "0")}s`);

function Stage({ stage, last }: { stage: ImportJob["stages"][number]; last: boolean }) {
  const took = stage.started_at && stage.finished_at ? stage.finished_at - stage.started_at : null;
  return (
    <li className={`ip-stage ip-${stage.status}`} aria-current={stage.status === "active" ? "step" : undefined}>
      <span className="ip-node" aria-hidden>
        {stage.status === "done" ? <Check size={14} /> : stage.status === "failed" ? <X size={14} /> : STAGE_ICON[stage.key]}
      </span>
      {!last && <span className="ip-rail" aria-hidden />}
      <span className="ip-stage-text">
        <span className="ip-stage-label">{stage.label}</span>
        <span className="ip-stage-detail">
          {stage.detail}
          {took != null && took >= 0.5 && <span className="ip-took"> · {clock(took)}</span>}
        </span>
      </span>
    </li>
  );
}

function Meter({ job }: { job: ImportJob }) {
  const writing = job.streaming && job.status === "running";
  const pct = job.status === "done" ? 100 : (job.percent ?? 0);
  const indeterminate = job.status === "running" && job.percent == null;
  return (
    <div className="ip-meter-wrap">
      <div
        className={`ip-meter ${indeterminate ? "ip-meter-wait" : ""} ${job.status === "failed" ? "ip-meter-fail" : ""}`}
        role="progressbar"
        aria-label="Reading the resume"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={indeterminate ? undefined : Math.round(pct)}
      >
        <span className="ip-fill" style={{ transform: `scaleX(${indeterminate ? 1 : pct / 100})` }} />
      </div>
      <p className="ip-stats" aria-live="off">
        {indeterminate && <span>The model is reading your CV before it starts writing…</span>}
        {writing && (
          <>
            <span><strong>{Math.round(pct)}%</strong></span>
            <span>{job.tokens.toLocaleString()} tokens</span>
            {job.tokens_per_s != null && <span>{job.tokens_per_s} tok/s</span>}
            {job.eta_s != null && <span>about {clock(job.eta_s)} left</span>}
          </>
        )}
        {!job.streaming && job.status === "running" && job.percent != null && <span>Working…</span>}
      </p>
    </div>
  );
}

function Found({ job }: { job: ImportJob }) {
  const { fields, skills, education } = job.found;
  const running = job.status === "running";
  if (!running && !Object.keys(fields).length && !skills.length) return null;
  return (
    <section className="ip-found" aria-label="Details found so far">
      <header className="ip-found-head">
        <span className="eyebrow">{running ? "Found so far" : "Found in your CV"}</span>
        {running && job.streaming && <span className="ip-live"><span className="ip-live-dot" aria-hidden /> live</span>}
      </header>
      <dl className="ip-fields">
        {Object.entries(FIELD_LABEL).map(([key, label]) => (
          <div key={key} className={`ip-field ${fields[key] ? "ip-field-in" : ""}`}>
            <dt>{label}</dt>
            <dd>{fields[key] ?? (running ? <span className="ip-ghost" aria-label="not found yet" /> : <span className="muted">—</span>)}</dd>
          </div>
        ))}
      </dl>
      {(skills.length > 0 || running) && (
        <div className="ip-skills-block">
          <p className="ip-skills-head">Skills <Badge tone="sky">{skills.length}</Badge></p>
          <ul className="ip-skills" aria-label="Skills found">
            {skills.map((s, i) => (
              <li key={s} className="ip-chip" style={{ animationDelay: `${Math.min(i % 6, 5) * 40}ms` }}>{s}</li>
            ))}
            {running && <li className="ip-chip ip-chip-ghost" aria-hidden />}
          </ul>
        </div>
      )}
      {education.length > 0 && (
        <p className="ip-edu muted">Education: {education.join(" · ")}</p>
      )}
    </section>
  );
}

const label = (field: string) => field.replace(/_/g, " ");

function Outcome({ job, onAgain }: { job: ImportJob; onAgain: () => void }) {
  if (job.status === "failed") {
    return (
      <div className="ip-outcome ip-outcome-fail" role="alert">
        <CircleAlert size={18} />
        <div>
          <p><strong>The resume could not be read.</strong> {job.error}</p>
          <Button onClick={onAgain}>Try again</Button>
        </div>
      </div>
    );
  }
  const r = job.result;
  if (job.status !== "done" || !r) return null;
  return (
    <div className="ip-outcome" role="status">
      <Check size={18} />
      <div>
        <p>
          <strong>{r.version ? `Saved as profile version ${r.version}` : "Your profile already had all of this"}</strong>
          {" "}in {clock(job.elapsed_s)}
          {job.model && <> with <code>{job.model}</code></>}.
        </p>
        {r.updated.length > 0 && <p>Filled from the CV: {r.updated.map(label).join(", ")}.</p>}
        {r.kept.length > 0 && (
          <p>Kept your existing {r.kept.map(label).join(", ")}: the CV says something different. Edit below if the CV is right.</p>
        )}
        {r.missing.length > 0 && <p>{r.missing.length} details still needed: answer them below.</p>}
        <Button variant="ghost" onClick={onAgain}>Import another resume</Button>
      </div>
    </div>
  );
}

export function ImportProgress({ job, onAgain }: { job: ImportJob; onAgain: () => void }) {
  const elapsed = useLiveElapsed(job);
  return (
    <div className={`ip ip-${job.status}`}>
      <header className="ip-head">
        <div className="ip-file">
          <FileText size={18} />
          <span className="ip-filename">{job.filename}</span>
          <span className="muted">{(job.size_bytes / 1024).toFixed(0)} KB</span>
        </div>
        <div className="ip-head-side">
          {job.model && <Badge tone="neutral">{job.model}</Badge>}
          <span className="ip-clock" aria-label="Elapsed time">{clock(elapsed)}</span>
        </div>
      </header>
      <ol className="ip-stages">
        {job.stages.map((s, i) => <Stage key={s.key} stage={s} last={i === job.stages.length - 1} />)}
      </ol>
      <Meter job={job} />
      <Found job={job} />
      <Outcome job={job} onAgain={onAgain} />
    </div>
  );
}
