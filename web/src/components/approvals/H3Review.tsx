import { ExternalLink, ShieldAlert } from "lucide-react";
import { useMemo, useState } from "react";
import { useAnswerDecision, type Decision } from "../../api/queries";
import { experienceBand, parseFlag, relativeTime, salaryRange } from "../../lib/format";
import { Badge, Button, ErrorNote } from "../ui";

interface Row {
  rank: number;
  job_id: number;
  title: string;
  company: string;
  location: string;
  experience_min: number | null;
  experience_max: number | null;
  salary_min: number | null;
  salary_max: number | null;
  salary_currency: string;
  posted_at: string | null;
  portals: string[];
  match_score: number | null;
  match_reason: string;
  red_flags: string[];
  url: string;
}

/** "1,3,5-8" from a set of ranks, compressing runs. */
export function toSelection(ranks: number[]): string {
  const sorted = [...ranks].sort((a, b) => a - b);
  const parts: string[] = [];
  for (let i = 0; i < sorted.length; i++) {
    const start = sorted[i] as number;
    let end = start;
    while (i + 1 < sorted.length && sorted[i + 1] === end + 1) end = sorted[++i] as number;
    parts.push(end - start >= 2 ? `${start}-${end}` : end > start ? `${start},${end}` : `${start}`);
  }
  return parts.join(",");
}

export function H3Review({ decision }: { decision: Decision }) {
  const jobs = (decision.payload.jobs ?? []) as Row[];
  const [picked, setPicked] = useState<number[]>([]);
  const answer = useAnswerDecision();
  const selection = toSelection(picked);
  const flagged = useMemo(
    () => jobs.filter((j) => picked.includes(j.rank) && j.red_flags.some((f) => parseFlag(f).severity === "high")),
    [jobs, picked],
  );
  const error = (decision.payload.error as string) || "";

  function toggle(rank: number) {
    setPicked(picked.includes(rank) ? picked.filter((r) => r !== rank) : [...picked, rank]);
  }

  return (
    <div className="decision-body">
      <p className="muted">Pick the jobs to apply to. JobPilot applies only to these, one at a time.</p>
      {error && <p className="callout callout-stop">{error}</p>}
      <div className="h3-table" role="table" aria-label="Jobs to approve">
        {jobs.map((j) => {
          const flags = j.red_flags.map(parseFlag);
          return (
            <label key={j.job_id} className={`h3-row ${picked.includes(j.rank) ? "on" : ""}`} role="row">
              <input type="checkbox" checked={picked.includes(j.rank)} onChange={() => toggle(j.rank)}
                     aria-label={`Select #${j.rank} ${j.title}`} />
              <span className="mono h3-rank">#{j.rank}</span>
              <span className="h3-main">
                <span className="h3-title">{j.title}</span>
                <span className="muted">{[j.company, j.location, experienceBand(j.experience_min, j.experience_max),
                  salaryRange(j.salary_min, j.salary_max, j.salary_currency), relativeTime(j.posted_at)]
                  .filter(Boolean).join(" · ")}</span>
                {j.match_reason && <span className="h3-reason">{j.match_reason}</span>}
                {flags.length > 0 && (
                  <span className="job-flags">
                    {flags.map((f) => (
                      <Badge key={f.code} tone={f.severity === "high" ? "stop" : "amber"}>{f.message || f.code}</Badge>
                    ))}
                  </span>
                )}
              </span>
              <span className="h3-score mono">{j.match_score ?? "–"}</span>
              {j.url && (
                <a href={j.url} target="_blank" rel="noopener noreferrer" className="link-quiet"
                   onClick={(e) => e.stopPropagation()} aria-label={`Open #${j.rank} listing`}>
                  <ExternalLink size={14} />
                </a>
              )}
            </label>
          );
        })}
      </div>
      {flagged.length > 0 && (
        <p className="callout callout-stop">
          <ShieldAlert size={16} /> You selected {flagged.length} job(s) with a high-severity red flag.
          Check the listing before approving.
        </p>
      )}
      <div className="decision-actions">
        <span className="mono muted">{picked.length ? `Selection: ${selection}` : "Nothing selected"}</span>
        <span className="spacer" />
        <Button data-hotkey="reject" variant="ghost" busy={answer.isPending} onClick={() => answer.mutate({ decision, answer: { action: "reject" } })}>
          Reject all
        </Button>
        <Button busy={answer.isPending} onClick={() => answer.mutate({ decision, answer: { action: "approve", selection: "all" } })}>
          Approve all {jobs.length}
        </Button>
        <Button data-hotkey="approve" variant="primary" disabled={!picked.length} busy={answer.isPending}
                onClick={() => answer.mutate({ decision, answer: { action: "approve", selection } })}>
          Approve {picked.length || ""}
        </Button>
      </div>
      <ErrorNote error={answer.error} />
    </div>
  );
}
