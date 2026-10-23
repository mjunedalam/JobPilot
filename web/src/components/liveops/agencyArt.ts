/** Drawings for the Agency: people, the things they carry, desks and rooms. */
import { CAST, LANE, type NodeKey } from "./cast";
import { ring, rrect } from "./shapes";

const f = (n: number) => n.toFixed(1);
const P = (x: number, y: number) => `${f(x)} ${f(y)}`;
export const WALK_V = 150; // px per second

export interface Figure { head: string; hair: string; body: string; legs: string; arms: string }

export function person(x: number, y: number, phase: number, walking: boolean): Figure {
  const sw = walking ? Math.sin(phase) * 6 : 0;
  return {
    head: ring(x, y - 30, 6.5),
    hair: `M${P(x - 6.5, y - 31)} A6.5 6.5 0 0 1 ${P(x + 6.5, y - 31)} Q${P(x, y - 35)} ${P(x - 6.5, y - 31)} Z`,
    body: rrect(x - 7, y - 22, 14, 19, 5),
    legs: `M${P(x - 3, y - 4)} L${P(x - 3 + sw, y + 10)} M${P(x + 3, y - 4)} L${P(x + 3 - sw, y + 10)}`,
    arms: walking
      ? `M${P(x - 7, y - 18)} L${P(x - 9 - sw * 0.6, y - 6)} M${P(x + 7, y - 18)} L${P(x + 11, y - 12)}`
      : `M${P(x - 7, y - 17)} L${P(x - 11, y - 9 + Math.sin(phase * 2) * 2)} M${P(x + 7, y - 17)} L${P(x + 11, y - 9 - Math.sin(phase * 2) * 2)}`,
  };
}

/** What someone carries, from what the hop is about. */
export function carried(label: string, to: NodeKey): [string, string] {
  const l = label.toLowerCase();
  if (to === "gmail" || l.includes("mail")) return ["envelope", "#FFFDF8"];
  if (l.includes("form") || l.includes("fill") || l.includes("submit") || to === "chrome") return ["form", "#DCEBF7"];
  if (l.includes("posting") || l.includes("dedup")) return ["stack", "#FFFDF8"];
  if (to === "portals" || l.includes("sign")) return ["key", "#E0B34A"];
  if (l.includes("today") || l.includes("record")) return ["chart", "#FFFDF8"];
  if (l.includes("draft") || l.includes("reply")) return ["letter", "#FFFDF8"];
  return ["folder", "#E9C46A"];
}

export function item(kind: string, x: number, y: number): string {
  switch (kind) {
    case "envelope": return `${rrect(x, y + 1, 15, 10, 1)} M${P(x, y + 1)} L${P(x + 7.5, y + 7)} L${P(x + 15, y + 1)}`;
    case "letter": return `${rrect(x + 1, y - 2, 12, 15, 1)} M${P(x + 3, y + 2)} h8 M${P(x + 3, y + 5)} h8 M${P(x + 3, y + 8)} h5`;
    case "form": return `${rrect(x + 1, y - 2, 12, 15, 1)} M${P(x + 3, y + 2)} h3 M${P(x + 7, y + 2)} h4 M${P(x + 3, y + 6)} h8`;
    case "stack": return `${rrect(x, y + 3, 15, 9, 1)} ${rrect(x + 2, y, 15, 9, 1)}`;
    case "key": return `${ring(x + 4, y + 6, 3.5)} M${P(x + 7.5, y + 6)} h8 v3 M${P(x + 13, y + 6)} v3`;
    case "chart": return `${rrect(x, y, 15, 12, 1)} M${P(x + 2, y + 10)} L${P(x + 6, y + 6)} L${P(x + 9, y + 8)} L${P(x + 13, y + 3)}`;
    default: return `M${P(x, y)} h6 l2 2 h8 v11 h-16 Z`;
  }
}

export function frontOf(k: NodeKey): [number, number] {
  return CAST[k].person?.front ?? CAST[k].place?.front ?? [500, LANE];
}

/** Seat → around the desk → hallway → the other person's desk (or a room). */
export function route(walker: NodeKey, to: NodeKey): [number, number][] {
  const who = CAST[walker].person;
  if (!who) return [];
  const p = frontOf(walker), q = frontOf(to);
  const side = who.row === "table" ? [] : [[who.seat[0] + (who.row === "office" ? 66 : 58), who.seat[1]], [who.seat[0] + (who.row === "office" ? 66 : 58), p[1]]] as [number, number][];
  const hall: [number, number][] = Math.abs(p[1] - LANE) < 1 && Math.abs(q[1] - LANE) < 1 ? [p, q] : [p, [p[0], LANE], [q[0], LANE], q];
  return [who.seat, ...side, ...hall];
}

export function length(pts: [number, number][]): number {
  let L = 0;
  for (let i = 1; i < pts.length; i++) L += Math.hypot(pts[i]![0] - pts[i - 1]![0], pts[i]![1] - pts[i - 1]![1]);
  return L;
}

export function along(pts: [number, number][], d: number): [number, number] {
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1]!, b = pts[i]!;
    const seg = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (d <= seg || i === pts.length - 1) {
      const k = seg ? Math.min(1, d / seg) : 1;
      return [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k];
    }
    d -= seg;
  }
  return pts[pts.length - 1] ?? [0, 0];
}

/** Who walks for a hop: the person at either end (the boss stays at their desk). */
export function walkerOf(from: NodeKey, to: NodeKey): { walker: NodeKey; dest: NodeKey } | null {
  if (from === "you") return null;
  if (CAST[from].person) return { walker: from, dest: to };
  if (CAST[to].person && to !== "you") return { walker: to, dest: from };
  return null;
}

export function agencyDuration(from: NodeKey, to: NodeKey): number {
  const w = walkerOf(from, to);
  if (!w) return 1.2;
  const L = length(route(w.walker, w.dest));
  const dwell = CAST[w.dest].place?.dwell ?? 0.7;
  return Math.max(2, (2 * L) / WALK_V + dwell);
}

export function desk(k: NodeKey): { d: string; props: string; y: number } | null {
  const p = CAST[k].person;
  if (!p || !p.prop) return null;
  const x = p.seat[0], top = p.row === "top";
  const y = top ? p.seat[1] + 2 : p.seat[1] - 44;
  let props = "";
  if (p.prop === "monitor") props = `${rrect(x - 22, y + 4, 44, 26, 3)} M${P(x - 14, y + 18)} q8 -10 16 0 t12 -4`;
  if (p.prop === "cabinet") props = `${rrect(x + 22, y - 26, 22, 64, 2)} M${P(x + 22, y - 5)} h22 M${P(x + 22, y + 16)} h22 ${rrect(x - 30, y + 10, 26, 18, 2)}`;
  if (p.prop === "scales") props = `M${P(x, y + 6)} V${f(y + 34)} M${P(x - 20, y + 12)} H${f(x + 20)} M${P(x - 26, y + 24)} l6 -12 l6 12 Z M${P(x + 14, y + 24)} l6 -12 l6 12 Z`;
  if (p.prop === "keys") props = `${rrect(x + 26, y - 24, 18, 58, 2)} ${[0, 1, 2].map((i) => ring(x + 35, y - 14 + i * 16, 3)).join(" ")} ${rrect(x - 28, y + 10, 24, 18, 2)}`;
  if (p.prop === "pigeonholes") props = `${rrect(x - 44, y + 46, 88, 30, 2)} M${P(x - 22, y + 46)} v30 M${P(x, y + 46)} v30 M${P(x + 22, y + 46)} v30 M${P(x - 44, y + 61)} h88 ${rrect(x - 20, y + 10, 26, 16, 1)}`;
  if (p.prop === "typewriter") props = `${rrect(x - 22, y + 12, 44, 20, 4)} ${rrect(x - 14, y + 4, 28, 10, 1)} M${P(x - 16, y + 22)} h32`;
  if (p.prop === "chart") props = `${rrect(x - 40, y + 50, 80, 26, 2)} M${P(x - 34, y + 70)} L${P(x - 14, y + 60)} L${P(x + 4, y + 64)} L${P(x + 32, y + 54)} ${rrect(x - 18, y + 10, 36, 20, 2)}`;
  if (p.prop === "laptop") props = `${rrect(x - 20, y + 6, 40, 22, 2)} M${P(x - 26, y + 32)} h52 ${rrect(x + 26, y + 6, 14, 18, 1)}`;
  return { d: rrect(x - 46, y, 92, 42, 6), props, y };
}

export const ROOMS: { key: NodeKey; d: string; d2: string; fill: string; detail: string; tx: number; ty: number }[] = [
  { key: "portals", d: rrect(900, 210, 80, 90, 4), d2: "M912 300 V222 H968 V300 M958 262 h4", fill: "#D5C7AE", detail: "#5C5346", tx: 940, ty: 196 },
  { key: "models", d: rrect(930, 392, 62, 76, 4), d2: "M940 404 h42 M940 418 h42 M940 432 h42 M940 446 h42", fill: "#3B3F46", detail: "#5FE1FF", tx: 950, ty: 380 },
  { key: "db", d: rrect(8, 392, 62, 76, 4), d2: `${ring(39, 430, 16)} M39 414 V446 M23 430 H55`, fill: "#BFB29B", detail: "#5C5346", tx: 52, ty: 380 },
  { key: "gmail", d: rrect(20, 560, 56, 90, 4), d2: "M30 574 h36 M30 588 h36 M48 600 V640", fill: "#C9B28A", detail: "#5C5346", tx: 48, ty: 676 },
  { key: "chrome", d: rrect(812, 560, 84, 90, 6), d2: `${rrect(824, 572, 60, 40, 3)} M836 624 h36`, fill: "#DDE6EE", detail: "#2B4BEE", tx: 854, ty: 676 },
];
