import { useApplications, useJourney, type Decision } from "../../api/queries";
import { relativeTime } from "../../lib/format";

/** What happens after you approve, per checkpoint: no surprises. */
export const IF_YOU_APPROVE: Record<string, string[]> = {
  H3: ["Opens each approved job in Chrome", "Fills the form from your profile and Answer Bank", "Asks you anything it is unsure of (H4)", "Shows you the filled form before submitting (H5)"],
  H4: ["Uses your answer in this form", "Saves it to your Answer Bank if you ticked it", "Carries on filling the form", "Shows you the filled form (H5)"],
  H5: ["Clicks Submit once, never twice", "Keeps a screenshot as proof", "Records the confirmation", "Watches your inbox for the reply"],
  H6: ["The Writer drafts a reply from your instruction", "Offers only times your calendar shows free", "Shows you the exact text (H7)", "Nothing is sent yet"],
  H7: ["Sends exactly this text, once", "Logs your approval with its fingerprint", "Marks the email replied", "Adds it to the application's journey"],
};

export function IfYouApprove({ checkpoint }: { checkpoint: string }) {
  const steps = IF_YOU_APPROVE[checkpoint];
  if (!steps) return null;
  return (
    <section aria-label="If you approve" className="dc-next">
      <h3 className="dc-next-title">If you approve</h3>
      <ol className="dc-next-steps">
        {steps.map((s, i) => (
          <li key={s} className={i >= 2 && checkpoint !== "H5" && checkpoint !== "H7" ? "dc-next-you" : ""}>
            <span className="mc-mono">{String(i + 1).padStart(2, "0")}</span>
            <span>{s}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}

/** The job behind a decision, when there is one. */
export function useDecisionJobId(decision: Decision | undefined): number | null {
  const apps = useApplications().data ?? [];
  if (!decision) return null;
  const p = decision.payload as Record<string, unknown>;
  if (typeof p.job_id === "number") return p.job_id;
  const email = p.email as { application?: { id: number } | null } | undefined;
  const appId = email?.application?.id;
  return appId ? (apps.find((a) => a.id === appId)?.job_id ?? null) : null;
}

export function JourneyPanel({ jobId }: { jobId: number | null }) {
  const journey = useJourney(jobId);
  if (jobId == null) return null;
  const j = journey.data;
  if (!j) return <p className="muted">Loading the journey…</p>;
  return (
    <section aria-labelledby="journey-title" className="dc-journey">
      <h2 id="journey-title" className="mc-card-title">{j.company} · journey</h2>
      <p className="muted">{j.title} · now <strong>{j.status}</strong></p>
      <ol className="dc-steps">
        {[...j.steps].reverse().map((s, i) => (
          <li key={`${s.at}-${i}`} className={`dc-step dc-step-${s.tone}`}>
            <span className="dc-step-dot" aria-hidden />
            <span className="dc-step-text">
              <span className="dc-step-title">{s.title}</span>
              <span className="dc-step-meta">{relativeTime(s.at)}{s.detail ? ` · ${s.detail}` : ""}</span>
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}
