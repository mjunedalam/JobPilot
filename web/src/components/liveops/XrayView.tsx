import { useEffect, useRef, useState } from "react";
import { CAST, type NodeKey } from "./cast";
import type { HopEngine } from "./engine";
import { diamond, rrect, shape } from "./shapes";

const CX = 500, CY = 380;
const CYAN = "#5FE1FF", AMBER = "#F5A524", TEAL = "#3FD0B5", STEEL = "#8DA3B5";
const GATES = ["allowlist", "budget", "approval binding", "idempotency", "kill switch"];
const GATE_ANGLES = [-140, -55, 25, 140, 210]; // clear of the outside world's positions
const KEYS = Object.keys(CAST) as NodeKey[];
const WIRES: [NodeKey, NodeKey][] = [["portal", "portals"], ["application", "chrome"], ["application", "db"], ["email", "gmail"], ["reply", "gmail"], ["email", "db"], ["db", "analytics"], ["search", "portal"]];

export type XrayMode = "shell" | "xray" | "exploded";

/** Smoothly follow a target number (for the explode / x-ray transitions). */
function useEase(target: number): number {
  const [v, setV] = useState(target);
  const ref = useRef(target);
  useEffect(() => {
    let raf = 0;
    const loop = () => {
      ref.current += (target - ref.current) * 0.14;
      setV(ref.current);
      if (Math.abs(target - ref.current) > 0.002) raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [target]);
  return v;
}

export function XrayView({ engine, mode, selected }: { engine: HopEngine; mode: XrayMode; selected: NodeKey }) {
  const e = useEase(mode === "exploded" ? 1 : 0);
  const xr = useEase(mode === "shell" ? 0 : 1);
  const arm = useEase(engine.waiting ? 0 : 1);
  const now = performance.now();

  const pos = (k: NodeKey) => {
    const n = CAST[k];
    if (n.kind === "core") return { x: CX, y: CY };
    const r = n.kind === "agent" ? 182 + e * 48 : 328 + e * 34;
    const a = (n.ang * Math.PI) / 180;
    return { x: CX + r * Math.cos(a) * (n.kind === "agent" ? 1.2 : 1.32), y: CY + r * Math.sin(a) * 0.86 };
  };
  const busy = new Set<NodeKey>();
  engine.hops.forEach((h) => h.delay <= 0 && h.t < 1 && (busy.add(h.from), busy.add(h.to)));
  const you = pos("you");
  const gx = (CX + you.x) / 2, gy = (CY + you.y) / 2;
  const shellRx = 300 + e * 62, shellRy = 262 + e * 44;
  const ang = (-75 * arm * Math.PI) / 180, ax = gx - 26, ay = gy - 4;
  const live = engine.hops.filter((h) => h.delay <= 0 && h.t < 1);
  const lead = live[0];

  return (
    <svg viewBox="0 0 1000 760" className="xr-svg" role="img"
         aria-label={`JobPilot's agents inside the harness. ${lead ? `${CAST[lead.from].label} to ${CAST[lead.to].label}: ${lead.label}` : "Idle."}`}>
      <defs>
        <pattern id="xr-grid" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M40 0H0V40" fill="none" stroke="#0E1C27" strokeWidth="1" /></pattern>
        <radialGradient id="xr-core" cx="50%" cy="50%" r="50%"><stop offset="0%" stopColor={CYAN} stopOpacity="0.3" /><stop offset="100%" stopColor={CYAN} stopOpacity="0" /></radialGradient>
        <linearGradient id="xr-beam" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={CYAN} stopOpacity="0" /><stop offset="50%" stopColor={CYAN} stopOpacity="0.1" /><stop offset="100%" stopColor={CYAN} stopOpacity="0" /></linearGradient>
      </defs>
      <rect width="1000" height="760" fill="#070C12" />
      <rect width="1000" height="760" fill="url(#xr-grid)" />
      <ellipse cx={CX} cy={CY} rx={shellRx} ry={shellRy} fill="none" stroke="#1E4256" strokeWidth="2" strokeDasharray={xr > 0.5 ? "2 6" : undefined} />
      <ellipse cx={CX} cy={CY} rx={shellRx - 18} ry={shellRy - 16} fill="#0A1A24" fillOpacity={0.25 * xr} stroke="#2A5A73" strokeOpacity="0.6" />
      {/* Gate markers sit on the rim between the cast; their names are in the caption below. */}
      {GATE_ANGLES.map((deg, i) => {
        const a = (deg * Math.PI) / 180;
        const x = CX + shellRx * Math.cos(a), y = CY + shellRy * Math.sin(a);
        return <path key={GATES[i]} d={diamond(x, y, 6)} fill="#05080C" stroke={CYAN} strokeWidth="1.5" opacity={0.4 + 0.6 * xr} />;
      })}
      <text x={CX} y={748} textAnchor="middle" className="xr-mono" fill="#5C8AA3" fontSize="11" letterSpacing="1">
        AGENT HARNESS ◇ every tool call passes: {GATES.join(" · ")}
      </text>
      {(["profile", "search", "portal", "matching", "application", "analytics", "email", "reply"] as NodeKey[]).map((k) => {
        const p = pos(k);
        return <line key={`s-${k}`} x1={CX} y1={CY} x2={p.x} y2={p.y} stroke={CYAN} strokeWidth="1.4" strokeOpacity="0.3" />;
      })}
      {WIRES.map(([a, b]) => {
        const p = pos(a), q = pos(b);
        return <line key={`w-${a}-${b}`} x1={p.x} y1={p.y} x2={q.x} y2={q.y} stroke={STEEL} strokeWidth="1.2" strokeOpacity="0.4" />;
      })}
      {(["profile", "matching", "application", "email", "reply"] as NodeKey[]).map((k) => {
        const p = pos(k), q = pos("models");
        return <line key={`m-${k}`} x1={p.x} y1={p.y} x2={q.x} y2={q.y} stroke={TEAL} strokeOpacity={0.25 * xr} strokeDasharray="3 6" />;
      })}
      <line x1={CX} y1={CY} x2={you.x} y2={you.y} stroke={AMBER} strokeWidth="2" strokeOpacity="0.7" strokeDasharray="6 6" />

      <g>
        <path d={`${rrect(gx - 30, gy - 10, 8, 30, 2)} ${rrect(gx + 22, gy - 10, 8, 30, 2)}`} fill="#1A1206" stroke={AMBER} strokeWidth="2" />
        <line x1={ax} y1={ay} x2={ax + 46 * Math.cos(ang)} y2={ay + 46 * Math.sin(ang)} stroke={AMBER} strokeWidth="5" strokeLinecap="round" />
        <line x1={ax} y1={ay} x2={ax + 46 * Math.cos(ang)} y2={ay + 46 * Math.sin(ang)} stroke="#05080C" strokeWidth="5" strokeDasharray="6 6" />
        {engine.waiting && <rect className="xr-wait" x={gx - 32} y={gy - 30} width="64" height="58" rx="10" fill="none" stroke={AMBER} strokeWidth="1.5" />}
        <text x={gx} y={gy + 44} textAnchor="middle" className="xr-mono" fill={AMBER} fontSize="11" letterSpacing="1">YOUR APPROVAL</text>
      </g>
      <circle cx={CX} cy={CY} r="130" fill="url(#xr-core)" />

      {KEYS.map((k) => {
        const n = CAST[k], p = pos(k);
        const color = n.kind === "you" ? AMBER : n.kind === "ext" ? STEEL : CYAN;
        const size = n.kind === "core" ? 34 : n.kind === "agent" ? 26 : 22;
        const [d, d2] = shape(n.shape, p.x, p.y, size);
        const half = n.kind === "core" ? 54 : size * 1.15;
        const recent = (engine.pulses[k] ?? -1e9) > now - 700;
        const on = busy.has(k) || recent;
        return (
          <g key={k}>
            <path d={d} fill="none" stroke={color} strokeWidth="7" strokeOpacity={on ? 0.35 : 0} strokeLinejoin="round" />
            <path d={d} fill={k === selected ? "#0E2230" : "#08131B"} stroke={color} strokeWidth={on || k === selected ? 2.4 : 1.5} strokeLinejoin="round" />
            <path d={d2} fill="none" stroke={color} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" opacity="0.85" />
            <text x={p.x} y={p.y + half + 18} textAnchor="middle" className="xr-label" fontSize={n.label.length > 16 ? 12 : 13.5}>{n.label}</text>
            <text x={p.x} y={p.y + half + 32} textAnchor="middle" className="xr-mono" fill={busy.has(k) ? CYAN : "#7FA3B8"} fontSize="10.5">
              {n.kind === "ext" || n.kind === "you" ? n.role : busy.has(k) ? "working" : "idle"}
            </text>
            {n.organs && <text x={p.x} y={p.y + half + 45} textAnchor="middle" className="xr-mono" fill="#5C8AA3" fontSize="10" opacity={xr}>{n.organs}</text>}
          </g>
        );
      })}

      {live.map((h) => {
        const a = pos(h.from), b = pos(h.to), t = h.t, k = t * t * (3 - 2 * t);
        const x = a.x + (b.x - a.x) * k, y = a.y + (b.y - a.y) * k;
        const color = h.from === "you" || h.to === "you" ? AMBER : h.from === "models" || h.to === "models" ? TEAL : CYAN;
        return (
          <g key={h.id}>
            <path d={diamond(x, y, 12)} fill={color} fillOpacity="0.18" />
            <path d={diamond(x, y, 6)} fill={color} />
          </g>
        );
      })}
      {lead && (() => {
        const a = pos(lead.from), b = pos(lead.to), k = lead.t * lead.t * (3 - 2 * lead.t);
        const x = a.x + (b.x - a.x) * k, y = a.y + (b.y - a.y) * k;
        const text = engine.waiting && lead.gate ? "waiting at your approval gate" : lead.label;
        const w = 14 + text.length * 7;
        const cx = Math.min(1000 - w - 6, x + 14);
        return (
          <g>
            <rect x={cx} y={y - 32} width={w} height="24" rx="6" fill="#05080C" fillOpacity="0.9" stroke={engine.waiting && lead.gate ? AMBER : "#C9D6E0"} strokeOpacity="0.6" />
            <text x={cx + 8} y={y - 16} className="xr-mono" fill={engine.waiting && lead.gate ? AMBER : "#C9D6E0"} fontSize="11.5">{text}</text>
          </g>
        );
      })()}
      <rect className="xr-scan" width="1000" height="60" fill="url(#xr-beam)" opacity={0.9 * xr} />
    </svg>
  );
}
