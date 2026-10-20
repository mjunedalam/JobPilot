import { useMissing, useProfile, useVersions } from "../api/queries";
import { MissingForm } from "../components/profile/MissingForm";
import { ProfileSheet } from "../components/profile/ProfileSheet";
import { ResumeImport } from "../components/profile/ResumeImport";
import { Badge, ErrorNote, Panel } from "../components/ui";

export function Profile() {
  const profile = useProfile();
  const missing = useMissing();
  const versions = useVersions();

  return (
    <div className="page">
      <header className="page-head">
        <p className="eyebrow">Pilot</p>
        <h1 className="page-title">Profile</h1>
        <p className="page-lede">
          Agents fill applications only from what is here. Every edit is saved as a new version, so
          nothing you wrote is ever lost.
        </p>
      </header>

      <div className="profile-grid">
        <div className="profile-main">
          <ErrorNote error={profile.error} />
          {profile.data && <ProfileSheet profile={profile.data} />}
        </div>

        <div className="profile-side">
          <Panel eyebrow="Import" title="Read a resume">
            <ResumeImport />
          </Panel>

          {(missing.data?.length ?? 0) > 0 && (
            <Panel
              eyebrow="Still needed"
              title="A few details resumes rarely include"
              actions={<Badge tone="amber">{missing.data?.length} open</Badge>}
            >
              <MissingForm missing={missing.data ?? []} />
            </Panel>
          )}

          <Panel eyebrow="History" title="Versions">
            <ol className="timeline">
              {(versions.data ?? []).map((v) => (
                <li key={v.version} className={v.is_current ? "timeline-current" : ""}>
                  <span className="timeline-dot" aria-hidden />
                  <span className="mono">v{v.version}</span>
                  <Badge tone={v.source === "resume" ? "sky" : "neutral"}>{v.source}</Badge>
                  <span className="timeline-meta muted">
                    {new Date(v.created_at).toLocaleString()} · {v.change_summary}
                  </span>
                </li>
              ))}
              {versions.data?.length === 0 && <li className="muted">No versions yet.</li>}
            </ol>
          </Panel>
        </div>
      </div>
    </div>
  );
}
