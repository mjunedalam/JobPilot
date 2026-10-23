import { useCallback, useState } from "react";
import { Link } from "react-router";
import { useDecisions } from "../api/queries";
import { useLiveStatus } from "../components/live/LiveProvider";
import { agencyDuration } from "../components/liveops/agencyArt";
import { AgencyView } from "../components/liveops/AgencyView";
import { AGENTS, CAST, type NodeKey } from "../components/liveops/cast";
import { GATE_TEXT, useHops } from "../components/liveops/engine";
import { XrayView, type XrayMode } from "../components/liveops/XrayView";
import { Button } from "../components/ui";
import { describeDecision } from "../lib/notify";

type View = "xray" | "agency";

function Segmented<T extends string>({ label, value, options, onChange }: {
  label: string; value: T; options: [T, string][]; onChange: (v: T) => void;
}) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map(([v, text]) => (
        <button key={v} type="button" aria-pressed={value === v} className={value === v ? "seg-on" : ""} onClick={() => onChange(v)}>{text}</button>
      ))}
    </div>
  );
}

export function LiveOps() {
  const [view, setView] = useState<View>("xray");
  const [mode, setMode] = useState<"live" | "demo">("demo");
  const [xmode, setXmode] = useState<XrayMode>("xray");
  const [selected, setSelected] = useState<NodeKey>("application");
  const durFor = useCallback((a: NodeKey, b: NodeKey) => (view === "agency" ? agencyDuration(a, b) : 1.15), [view]);
  const engine = useHops(mode, durFor);
  const decisions = useDecisions(false).data ?? [];
  const status = useLiveStatus();

  const busy = new Set<NodeKey>();
  engine.hops.forEach((h) => h.delay <= 0 && h.t < 1 && (busy.add(h.from), busy.add(h.to)));
  const gate = mode === "demo" && engine.waiting ? GATE_TEXT[engine.waiting] : null;
  const next = decisions[0];
  const sel = CAST[selected];

  return (
    <div className={`page lo lo-${view}`}>
      <header className="lo-head">
        <div>
          <p className="eyebrow">Cockpit</p>
          <h1 className="page-title">Live ops</h1>
          <p className="muted">
            {mode === "live"
              ? `Your agents' real work as it happens (${status}). Each moving piece is a tool call, a model call or a decision.`
              : "A demo day: how the agents work together, and where everything stops for you."}
          </p>
        </div>
        <div className="lo-controls">
          <Segmented label="View" value={view} options={[["xray", "X-ray"], ["agency", "The Agency"]]} onChange={setView} />
          <Segmented label="Source" value={mode} options={[["live", "Live"], ["demo", "Demo"]]} onChange={setMode} />
          {view === "xray" && <Segmented label="Depth" value={xmode} options={[["shell", "Shell"], ["xray", "X-ray"], ["exploded", "Exploded"]]} onChange={setXmode} />}
        </div>
      </header>

      <div className="lo-layout">
        <section className={`lo-stage lo-stage-${view}`} aria-label="Live view">
          {view === "xray" ? <XrayView engine={engine} mode={xmode} selected={selected} /> : <AgencyView engine={engine} />}
        </section>

        <aside className="lo-side" aria-label="Controls and telemetry">
          {mode === "demo" && (
            <div className="lo-row">
              <Button variant="primary" onClick={() => engine.setPlaying(!engine.playing)}>{engine.playing ? "Pause" : "Play"}</Button>
              <Button onClick={() => engine.setSpeed(engine.speed === 1 ? 2 : engine.speed === 2 ? 4 : 1)} aria-label="Speed">{engine.speed}×</Button>
              <Button onClick={() => engine.jumpTo("mail")}>Send a test email</Button>
            </div>
          )}
          {mode === "demo" && (
            <label className="check-inline">
              <input type="checkbox" checked={engine.autoTaps} onChange={(e) => engine.setAutoTaps(e.target.checked)} />
              Simulate my taps (JobPilot itself never approves)
            </label>
          )}

          <article className={`lo-gate ${gate || (mode === "live" && next) ? "lo-gate-on" : ""}`}>
            {mode === "demo" ? (
              gate ? (
                <>
                  <span className="mc-mono">{engine.waiting} · AT YOUR GATE</span>
                  <strong>{gate[0]}</strong>
                  <span className="muted">{gate[1]}</span>
                  <div className="lo-row">
                    <Button variant="primary" onClick={engine.approve}>Approve</Button>
                    <Button onClick={engine.reject}>Reject</Button>
                  </div>
                </>
              ) : (
                <><span className="mc-mono">NOTHING WAITING</span><span className="muted">Decisions stop at your gate before anything is submitted or sent.</span></>
              )
            ) : next ? (
              <>
                <span className="mc-mono">{next.checkpoint} · WAITING FOR YOU</span>
                <strong>{describeDecision(next.checkpoint, next.payload as Record<string, unknown>).title}</strong>
                <Link className="btn btn-primary" to={`/approvals?focus=${encodeURIComponent(next.interrupt_id)}`}>Decide</Link>
              </>
            ) : (
              <><span className="mc-mono">NOTHING WAITING</span><span className="muted">When an agent needs you, its work stops at the gate and shows here.</span></>
            )}
          </article>

          <div className="lo-team" role="group" aria-label="Inspect an agent">
            {AGENTS.map((k) => (
              <button key={k} type="button" aria-pressed={selected === k} className={`lo-agent ${selected === k ? "lo-agent-on" : ""} ${busy.has(k) ? "lo-agent-busy" : ""}`}
                      onClick={() => setSelected(k)}>
                <span className="lo-dot" style={{ background: CAST[k].person?.shirt }} aria-hidden />
                <span>{CAST[k].label}</span>
                <span className="mc-mono">{busy.has(k) ? "working" : "idle"}</span>
              </button>
            ))}
          </div>
          <article className="lo-inspect">
            <strong>{sel.label}{sel.person ? ` · ${sel.person.title}` : ""}</strong>
            <span className="muted">{sel.role}</span>
            {sel.organs && <span className="mc-mono">{sel.organs}</span>}
          </article>

          <div className="lo-log">
            <span className="mc-mono">EVENT LOG{mode === "live" ? " · LIVE" : " · DEMO"}</span>
            <ol aria-live="polite">
              {engine.log.map((l) => <li key={l.id} className={`lo-log-${l.tone}`}><span className="mc-mono">{l.at}</span><span>{l.text}</span></li>)}
              {engine.log.length === 0 && <li className="muted">{mode === "live" ? "Waiting for the agents to do something…" : "Press Play."}</li>}
            </ol>
          </div>
        </aside>
      </div>
    </div>
  );
}
