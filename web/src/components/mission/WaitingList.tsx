import { Link } from "react-router";
import { useDecisions, type Decision } from "../../api/queries";
import { describeDecision } from "../../lib/notify";
import { Empty } from "../ui";

function meta(d: Decision): string {
  const p = d.payload as Record<string, unknown>;
  const job = p.job as { title?: string; company?: string } | undefined;
  const jobs = p.jobs as { title: string; company: string; match_score: number | null }[] | undefined;
  const email = p.email as { sender?: string; subject?: string } | undefined;
  const draft = p.draft as { to?: string[] } | null | undefined;
  if (d.checkpoint === "H3" && jobs?.length) {
    const best = [...jobs].sort((a, b) => (b.match_score ?? 0) - (a.match_score ?? 0))[0];
    return `${jobs.length} jobs · best: ${best?.title} at ${best?.company} (${best?.match_score ?? "–"}%)`;
  }
  if (d.checkpoint === "H7" && draft?.to) return `To ${draft.to.join(", ")}`;
  if (email?.subject) return `${email.sender ?? ""} · ${email.subject}`;
  if (job?.title) return `${job.title}${job.company ? ` at ${job.company}` : ""}`;
  return "";
}

export function WaitingList() {
  const decisions = useDecisions(false).data ?? [];
  return (
    <section aria-labelledby="wait-title" className="mc-waiting">
      <div className="mc-section-head">
        <h2 id="wait-title" className="mc-section-title">Waiting for you</h2>
        <Link to="/approvals" className="mc-link">Open Decision Center →</Link>
      </div>
      {decisions.length === 0 && <Empty title="Nothing needs you">New decisions appear here and on your phone.</Empty>}
      <ul className="mc-wait-list">
        {decisions.map((d) => {
          const { title } = describeDecision(d.checkpoint, d.payload as Record<string, unknown>);
          return (
            <li key={d.interrupt_id} className={`mc-wait mc-wait-${d.checkpoint}`}>
              <span className="mc-tag">{d.checkpoint}</span>
              <span className="mc-wait-text">
                <span className="mc-wait-title">{title}</span>
                <span className="mc-wait-meta">{meta(d)}</span>
              </span>
              <Link className="btn btn-primary" to={`/approvals?focus=${encodeURIComponent(d.interrupt_id)}`}>Decide</Link>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
