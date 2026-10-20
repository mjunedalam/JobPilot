import { LogOut, OctagonX, Power } from "lucide-react";
import { setToken } from "../api/client";
import { useHealth, useKillSwitch } from "../api/queries";
import { Button, ErrorNote, Lamp, Panel } from "../components/ui";

export function System() {
  const health = useHealth();
  const kill = useKillSwitch();
  const h = health.data;
  const halted = Boolean(h?.kill_switch_engaged);

  return (
    <div className="page">
      <header className="page-head">
        <p className="eyebrow">Ops</p>
        <h1 className="page-title">System</h1>
      </header>
      <div className="system-grid">
        <Panel eyebrow="Kill switch" title={halted ? "Agents halted" : "Agents armed"} className={halted ? "is-halted" : ""}>
          <p className="muted">
            Same switch as <span className="mono">jobpilot stop</span>. Every agent checks it before
            each step and before any browser or email action.
          </p>
          <Button variant={halted ? "secondary" : "danger"} busy={kill.isPending} onClick={() => kill.mutate(!halted)}>
            {halted ? <Power size={16} /> : <OctagonX size={16} />}
            {halted ? "Resume agents" : "Stop everything"}
          </Button>
          <ErrorNote error={kill.error} />
        </Panel>

        <Panel eyebrow="Health" title="Instruments">
          <ErrorNote error={health.error} />
          {h && (
            <ul className="instrument-list">
              <li><Lamp tone="go" label="Database" /> <span className="mono">{h.database}</span></li>
              <li><Lamp tone={h.llm_privacy_mode === "local_only" ? "go" : "amber"} label="Privacy" /> <span className="mono">{h.llm_privacy_mode}</span></li>
              <li><Lamp tone={h.tracing === "langfuse" ? "go" : "neutral"} label="Tracing" /> <span className="mono">{h.tracing}</span></li>
            </ul>
          )}
        </Panel>

        <Panel eyebrow="Session" title="API token">
          <p className="muted">The token is stored in this browser only. Sign out to remove it.</p>
          <Button
            variant="ghost"
            onClick={() => {
              setToken(null);
              window.location.assign("/");
            }}
          >
            <LogOut size={16} /> Sign out
          </Button>
        </Panel>
      </div>
    </div>
  );
}
