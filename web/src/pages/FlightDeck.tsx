import { ArrowUpRight, CheckCircle2, CircleDashed, OctagonX, Power } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";
import {
  useAnswers,
  useHealth,
  useKillSwitch,
  useMissing,
  useProfile,
  useProviders,
  useRoutes,
  useVersions,
} from "../api/queries";
import { Badge, Button, ErrorNote, Panel } from "../components/ui";
import { ROLE_INFO, ROLE_ORDER } from "../lib/roles";

function greeting(): string {
  const hour = new Date().getHours();
  return hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
}

function CheckItem({
  done,
  pending,
  title,
  detail,
  to,
}: {
  done: boolean;
  pending?: boolean;
  title: string;
  detail: ReactNode;
  to?: string;
}) {
  const icon = done ? (
    <CheckCircle2 size={18} className="tone-go" />
  ) : (
    <CircleDashed size={18} className={pending ? "tone-muted" : "tone-amber"} />
  );
  const body = (
    <>
      {icon}
      <span className="check-text">
        <span className="check-title">{title}</span>
        <span className="check-detail">{detail}</span>
      </span>
      {to && <ArrowUpRight size={16} className="check-go" />}
    </>
  );
  return (
    <li className={`check ${done ? "check-done" : ""} ${pending ? "check-pending" : ""}`}>
      {to ? (
        <Link to={to} className="check-link">
          {body}
        </Link>
      ) : (
        <div className="check-link">{body}</div>
      )}
    </li>
  );
}

export function FlightDeck() {
  const profile = useProfile();
  const missing = useMissing();
  const versions = useVersions();
  const answers = useAnswers();
  const routes = useRoutes();
  const providers = useProviders();
  const health = useHealth();
  const kill = useKillSwitch();

  const firstName = profile.data?.full_name?.split(" ")[0];
  const missingCount = missing.data?.length ?? 0;
  const localNames = new Set((providers.data ?? []).filter((p) => p.is_local).map((p) => p.name));
  const halted = Boolean(health.data?.kill_switch_engaged);
  const routesByRole = new Map((routes.data ?? []).map((r) => [r.role, r]));

  return (
    <div className="page deck">
      <header className="hero">
        <p className="eyebrow">Phase 1 · pre-flight</p>
        <h1 className="hero-title">
          {greeting()}
          {firstName ? (
            <>
              , <em>{firstName}</em>.
            </>
          ) : (
            "."
          )}
        </h1>
        <p className="hero-lede">
          Every agent runs on the engines you pick and stops for your approval before anything
          leaves this machine. Finish the checklist, then job search unlocks in Phase 2.
        </p>
      </header>

      <div className="deck-grid">
        <Panel eyebrow="Checklist" title="Ready for take-off?" className="deck-checklist">
          <ul className="checklist">
            <CheckItem
              done={(versions.data?.length ?? 0) > 0}
              title="Profile created"
              detail={
                versions.data?.length
                  ? `Version ${versions.data[0]?.version}, ${versions.data[0]?.source}`
                  : "Import your resume to start"
              }
              to="/profile"
            />
            <CheckItem
              done={missingCount === 0 && (versions.data?.length ?? 0) > 0}
              title="Required details"
              detail={missingCount ? `${missingCount} fields still empty` : "All required fields set"}
              to="/profile"
            />
            <CheckItem
              done={(answers.data?.length ?? 0) >= 3}
              title="Screening answers"
              detail={`${answers.data?.length ?? 0} saved (3+ recommended)`}
              to="/answers"
            />
            <CheckItem
              done={(routes.data ?? []).length > 0}
              title="Engines assigned"
              detail="A model for every agent role"
              to="/models"
            />
            <CheckItem pending done={false} title="Job portals" detail="Arrives in Phase 2" />
          </ul>
        </Panel>

        <Panel
          eyebrow="Engines"
          title="Who is flying each role"
          className="deck-engines"
          actions={
            <Link to="/models" className="link-quiet">
              Change <ArrowUpRight size={14} />
            </Link>
          }
        >
          <ErrorNote error={routes.error} />
          <ul className="engine-list">
            {ROLE_ORDER.map((role) => {
              const route = routesByRole.get(role);
              const local = route ? localNames.has(route.provider) : false;
              return (
                <li key={role} className="engine">
                  <span className="engine-role">{ROLE_INFO[role].title}</span>
                  <span className="engine-model mono">{route?.model ?? "…"}</span>
                  {route && (
                    <Badge tone={local ? "go" : "sky"}>{local ? "local" : route.provider}</Badge>
                  )}
                </li>
              );
            })}
          </ul>
        </Panel>

        <Panel eyebrow="Ops" title="Kill switch" className={`deck-kill ${halted ? "is-halted" : ""}`}>
          <p className="muted">
            {halted
              ? "All agents are halted. Nothing will be searched, submitted or sent."
              : "Stops every agent before its next step: searches, applications and emails."}
          </p>
          <Button
            variant={halted ? "secondary" : "danger"}
            busy={kill.isPending}
            onClick={() => kill.mutate(!halted)}
          >
            {halted ? <Power size={16} /> : <OctagonX size={16} />}
            {halted ? "Resume agents" : "Stop everything"}
          </Button>
          <ErrorNote error={kill.error} />
        </Panel>
      </div>
    </div>
  );
}
