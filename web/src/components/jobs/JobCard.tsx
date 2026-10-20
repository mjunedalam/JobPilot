import { ChevronDown, ExternalLink, ShieldAlert } from "lucide-react";
import { useState } from "react";
import { useJob, type Job } from "../../api/queries";
import { experienceBand, parseFlag, relativeTime, salaryRange, scoreBand } from "../../lib/format";
import { Badge } from "../ui";

function ScoreDial({ score }: { score: number | null }) {
  const value = score ?? 0;
  const band = scoreBand(score);
  return (
    <div className={`dial dial-${band}`} style={{ ["--score" as string]: value }} aria-label={`Match ${value} out of 100`}>
      <span className="dial-num mono">{score ?? "–"}</span>
    </div>
  );
}

export function JobCard({ job, rank }: { job: Job; rank: number }) {
  const [open, setOpen] = useState(false);
  const detail = useJob(open ? job.id : null);
  const flags = job.red_flags.map(parseFlag);
  const high = flags.some((f) => f.severity === "high");
  const facts = [
    job.location || null,
    job.work_mode || null,
    experienceBand(job.experience_min, job.experience_max),
    salaryRange(job.salary_min, job.salary_max, job.salary_currency),
    relativeTime(job.posted_at),
  ].filter(Boolean);

  return (
    <article className={`job ${high ? "job-flagged" : ""}`}>
      <span className="job-rank mono">#{rank}</span>
      <ScoreDial score={job.match_score} />
      <div className="job-body">
        <header>
          <h3 className="job-title">{job.title}</h3>
          <p className="job-company">{job.company}</p>
        </header>
        <p className="job-facts">{facts.join(" · ")}</p>
        {job.match_reason && <p className="job-reason">{job.match_reason}</p>}
        {flags.length > 0 && (
          <ul className="job-flags">
            {flags.map((f) => (
              <li key={f.code}>
                <Badge tone={f.severity === "high" ? "stop" : "amber"}>
                  {f.severity === "high" && <ShieldAlert size={12} />} {f.message || f.code}
                </Badge>
              </li>
            ))}
          </ul>
        )}
        <footer className="job-foot">
          {job.portals.map((p) => <Badge key={p} tone="sky">{p}</Badge>)}
          {job.source_urls.slice(0, 3).map((url, i) => (
            <a key={url} href={url} target="_blank" rel="noopener noreferrer" className="link-quiet">
              listing {job.source_urls.length > 1 ? i + 1 : ""} <ExternalLink size={12} />
            </a>
          ))}
          <button className="link-quiet job-more" aria-expanded={open} onClick={() => setOpen(!open)}>
            Details <ChevronDown size={14} className={open ? "rot" : ""} />
          </button>
        </footer>
        {open && (
          <div className="job-detail">
            {detail.isLoading && <p className="muted">Loading…</p>}
            {detail.data && (
              <>
                {detail.data.skills.length > 0 && (
                  <div className="tags">{detail.data.skills.map((s) => <Badge key={s}>{s}</Badge>)}</div>
                )}
                <p className="job-desc">{detail.data.description || "No description on the listing."}</p>
              </>
            )}
          </div>
        )}
      </div>
    </article>
  );
}
