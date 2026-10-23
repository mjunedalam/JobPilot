import { CAST, type NodeKey } from "./cast";
import { HOLD_AT, type HopEngine } from "./engine";
import { ROOMS, along, carried, desk, item, length, person, route, walkerOf } from "./agencyArt";
import { ring, rrect } from "./shapes";

const PEOPLE = (Object.keys(CAST) as NodeKey[]).filter((k) => CAST[k].person);

export function AgencyView({ engine }: { engine: HopEngine }) {
  const phase = performance.now() / 110;
  const walking = new Map<NodeKey, { x: number; y: number; moving: boolean; carry: [string, string] | null; label: string; gated: boolean }>();
  const active = new Set<NodeKey>();
  for (const h of engine.hops) {
    if (h.delay > 0) continue;
    const w = walkerOf(h.from, h.to);
    if (!w || walking.has(w.walker)) continue;
    const pts = route(w.walker, w.dest);
    const L = length(pts);
    let d: number, moving = true, carry: [string, string] | null = carried(h.label, w.dest);
    if (h.t < HOLD_AT) d = (h.t / HOLD_AT) * L;
    else if (h.t < 1 - HOLD_AT) { d = L; moving = false; }
    else { d = L * (1 - (h.t - (1 - HOLD_AT)) / HOLD_AT); carry = null; }
    const [x, y] = along(pts, d);
    walking.set(w.walker, { x, y, moving, carry, label: h.label, gated: Boolean(h.gate && !h.released) });
    if (h.t < 1 - HOLD_AT) { active.add(w.walker); active.add(w.dest); }
  }
  const speaker = [...walking.entries()].find(([, v]) => v.carry !== null) ?? [...walking.entries()][0];
  const you = CAST.you.person!;

  return (
    <svg viewBox="0 0 1000 760" className="ag-svg" role="img"
         aria-label={`The office: JobPilot's agents as people. ${speaker ? `${CAST[speaker[0]].label}: ${speaker[1].label}` : "Everyone is at their desk."}`}>
      <defs>
        <pattern id="ag-floor" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M40 0H0V40" fill="none" stroke="#EAE3D6" strokeWidth="1" /></pattern>
      </defs>
      <rect width="1000" height="760" fill="#F4EFE6" />
      <rect width="1000" height="760" fill="url(#ag-floor)" />
      <rect x="0" y="392" width="1000" height="76" fill="#ECE5D8" />
      <text x="500" y="458" textAnchor="middle" className="ag-mono" fill="#B3A895" fontSize="11" letterSpacing="4">HALLWAY</text>
      <rect x="24" y="70" width="206" height="250" rx="10" fill="#E6F0F3" fillOpacity="0.7" stroke="#8FB8C9" strokeWidth="2" />
      <text x="38" y="92" className="ag-mono" fill="#4F7C8F" fontSize="11" letterSpacing="2">YOUR OFFICE</text>

      {ROOMS.map((r) => (
        <g key={r.key}>
          <path d={r.d} fill={r.fill} stroke="#9C9180" strokeWidth="1.5" />
          <path d={r.d2} fill="none" stroke={r.detail} strokeWidth="2" strokeLinecap="round" className={active.has(r.key) ? "ag-blink" : undefined} />
          <text x={r.tx} y={r.ty} textAnchor="middle" className="ag-label" fontSize="12">{CAST[r.key].place?.label}</text>
        </g>
      ))}

      {PEOPLE.filter((k) => !walking.has(k)).map((k) => {
        const p = CAST[k].person!, fig = person(p.seat[0], p.seat[1], phase + p.seat[0], false);
        return (
          <g key={`seat-${k}`}>
            <path d={fig.body} fill={p.shirt} />
            <path d={fig.head} fill={p.skin} />
            <path d={fig.hair} fill={p.hair} />
            <path d={fig.arms} fill="none" stroke={p.shirt} strokeWidth="3.5" strokeLinecap="round" />
          </g>
        );
      })}

      {PEOPLE.map((k) => {
        const p = CAST[k].person!, dk = desk(k);
        if (k === "core") {
          return (
            <g key={`desk-${k}`}>
              <path d={ring(p.seat[0], p.seat[1] + 26, 34)} fill="#D9CCB4" stroke={active.has(k) ? p.shirt : "#A8987C"} strokeWidth={active.has(k) ? 3 : 1.5} />
              <text x={p.seat[0]} y={p.seat[1] + 76} textAnchor="middle" className="ag-label" fontSize="12.5">Orchestrator</text>
              <text x={p.seat[0]} y={p.seat[1] + 90} textAnchor="middle" className="ag-mono" fill="#7D7367" fontSize="10">{p.title}</text>
            </g>
          );
        }
        if (k === "you") {
          return (
            <g key="desk-you">
              <path d={rrect(p.seat[0] - 52, p.seat[1] + 2, 104, 44, 6)} fill="#D9CCB4" stroke={engine.waiting ? "#C47A12" : "#A8987C"} strokeWidth={engine.waiting ? 3 : 1.5} />
              <path d={rrect(p.seat[0] + 26, p.seat[1] + 8, 22, 14, 2)} fill={engine.waiting ? "#F3D28B" : "#E8DFCE"} stroke="#5C5346" className={engine.waiting ? "ag-blink" : undefined} />
              <text x={p.seat[0]} y={p.seat[1] + 64} textAnchor="middle" className="ag-label" fontSize="12.5">You</text>
              <text x={p.seat[0]} y={p.seat[1] + 78} textAnchor="middle" className="ag-mono" fill="#7D7367" fontSize="10">{engine.waiting ? "something is on your desk" : "decide here or on your phone"}</text>
            </g>
          );
        }
        if (!dk) return null;
        const top = p.row === "top";
        return (
          <g key={`desk-${k}`}>
            <path d={dk.d} fill="#D9CCB4" stroke={active.has(k) ? p.shirt : "#A8987C"} strokeWidth={active.has(k) ? 3 : 1.5} />
            <path d={dk.props} fill="#F4EFE6" stroke="#5C5346" strokeWidth="1.4" strokeLinejoin="round" />
            <text x={p.seat[0]} y={top ? dk.y + 62 : dk.y + 104} textAnchor="middle" className="ag-label" fontSize="12.5">{CAST[k].label}</text>
            <text x={p.seat[0]} y={top ? dk.y + 76 : dk.y + 118} textAnchor="middle" className="ag-mono" fill="#7D7367" fontSize="10">{p.title}</text>
          </g>
        );
      })}

      {[...walking.entries()].map(([k, w]) => {
        const p = CAST[k].person!, fig = person(w.x, w.y, phase, w.moving);
        return (
          <g key={`walk-${k}`}>
            <ellipse cx={w.x} cy={w.y + 11} rx="9" ry="3" fill="#000" fillOpacity="0.12" />
            <path d={fig.legs} fill="none" stroke="#3B342C" strokeWidth="3.5" strokeLinecap="round" />
            <path d={fig.body} fill={p.shirt} />
            <path d={fig.arms} fill="none" stroke={p.shirt} strokeWidth="3.5" strokeLinecap="round" />
            <path d={fig.head} fill={p.skin} />
            <path d={fig.hair} fill={p.hair} />
            {w.carry && <path d={item(w.carry[0], w.x + 8, w.y - 22)} fill={w.carry[1]} stroke="#5C5346" strokeWidth="1.2" strokeLinejoin="round" />}
          </g>
        );
      })}

      {speaker && (() => {
        const [, w] = speaker;
        const text = w.gated ? `Waiting for you: ${w.label}` : w.label;
        const bw = 18 + text.length * 6.4, bx = Math.min(1000 - bw - 8, Math.max(8, w.x - bw / 2));
        const by = w.y - 74 < 8 ? w.y + 18 : w.y - 74;
        return (
          <g>
            <path d={`${rrect(bx, by, bw, 28, 8)} M${w.x - 6} ${by + 28} L${w.x} ${by + 38} L${w.x + 6} ${by + 28}`} fill="#FFFDF8" stroke={w.gated ? "#C47A12" : "#B9AD99"} strokeWidth="1.5" />
            <text x={bx + 10} y={by + 18} className="ag-label" fontSize="12" fontWeight="500" fill={w.gated ? "#9A5A0A" : "#2A251F"}>{text}</text>
          </g>
        );
      })()}
      {engine.waiting && <text x={you.seat[0] + 30} y={you.seat[1] - 30} textAnchor="middle" className="ag-mono ag-blink" fill="#9A5A0A" fontSize="12">IN-TRAY</text>}
    </svg>
  );
}
