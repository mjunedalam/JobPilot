import { TriangleAlert } from "lucide-react";
import { Link } from "react-router";
import type { SearchReport } from "../../api/queries";
import { Badge } from "../ui";

const SKIP_LABEL: Record<string, string> = {
  excluded_company: "excluded company",
  expired: "expired",
  outside_window: "outside window",
  undated: "no posted date",
  already_handled: "already handled",
};

export function ReportStrip({ report }: { report: SearchReport }) {
  const steps = [
    ["fetched", report.fetched],
    ["unique", report.unique],
    ["new", report.new_jobs],
    ["updated", report.updated_jobs],
  ] as const;
  return (
    <div className="report" role="status">
      <ol className="report-flow">
        {steps.map(([label, value]) => (
          <li key={label}><span className="report-num mono">{value}</span><span className="eyebrow">{label}</span></li>
        ))}
      </ol>
      {Object.keys(report.skipped).length > 0 && (
        <p className="report-skips">
          Skipped:{" "}
          {Object.entries(report.skipped).map(([k, v]) => <Badge key={k}>{v} {SKIP_LABEL[k] ?? k}</Badge>)}
        </p>
      )}
      {Object.entries(report.blocked).map(([portal, reason]) => (
        <p key={portal} className="callout callout-amber">
          <TriangleAlert size={16} /> <strong>{portal}</strong> needs you (H1): {reason}.{" "}
          <Link to="/portals">Open Portals</Link>
        </p>
      ))}
      {Object.entries(report.errors).map(([portal, error]) => (
        <p key={portal} className="callout callout-stop"><strong>{portal}</strong> failed: {error}</p>
      ))}
    </div>
  );
}
