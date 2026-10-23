import { Image as ImageIcon } from "lucide-react";
import { useState } from "react";
import { fetchProof, type ApplicationRow } from "../../api/queries";
import { relativeTime } from "../../lib/format";
import { Badge, Button, ErrorNote } from "../ui";

const STATUS_TONE: Record<string, "go" | "amber" | "stop" | "sky" | "neutral"> = {
  applied: "go", applying: "sky", failed: "stop", skipped: "neutral", viewed: "sky",
  shortlisted: "sky", interview: "amber", offer: "go", rejected: "stop",
};

export function AppRow({ app }: { app: ApplicationRow }) {
  const [src, setSrc] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  return (
    <article className="app-row">
      <div>
        <h3 className="app-title">{app.title}</h3>
        <p className="muted">{app.company} · {app.portal} · attempt {app.attempts}
          {app.submitted_at ? ` · ${relativeTime(app.submitted_at)}` : ""}</p>
        {app.confirmation_text && <p className="app-confirm">“{app.confirmation_text}”</p>}
        {app.error && <p className="tone-stop app-error">{app.error}</p>}
      </div>
      <div className="app-side">
        <Badge tone={STATUS_TONE[app.status] ?? "neutral"}>{app.status}</Badge>
        {app.has_proof && (
          <Button variant="ghost" aria-expanded={Boolean(src)}
                  onClick={() => (src ? setSrc(null) : fetchProof(app.id).then(setSrc, setError))}>
            <ImageIcon size={14} /> {src ? "Hide proof" : "Proof"}
          </Button>
        )}
      </div>
      <ErrorNote error={error} />
      {src && <img className="proof" src={src} alt={`Screenshot of the submitted application for ${app.title}`} />}
    </article>
  );
}
