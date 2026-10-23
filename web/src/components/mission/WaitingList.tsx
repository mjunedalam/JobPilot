import { Link } from "react-router";
import { useDecisions } from "../../api/queries";
import { decisionMeta as meta, describeDecision } from "../../lib/notify";
import { Empty } from "../ui";

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
