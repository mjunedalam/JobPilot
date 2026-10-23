/** SVG path builders for the X-ray: each agent drawn as what it does. */

const f = (n: number) => n.toFixed(1);
const P = (x: number, y: number) => `${f(x)} ${f(y)}`;

export function rrect(x: number, y: number, w: number, h: number, r: number): string {
  return `M${P(x + r, y)} H${f(x + w - r)} Q${P(x + w, y)} ${P(x + w, y + r)} V${f(y + h - r)} Q${P(x + w, y + h)} ${P(x + w - r, y + h)} H${f(x + r)} Q${P(x, y + h)} ${P(x, y + h - r)} V${f(y + r)} Q${P(x, y)} ${P(x + r, y)} Z`;
}

export function ring(x: number, y: number, r: number): string {
  return `M${P(x - r, y)} a${r} ${r} 0 1 0 ${2 * r} 0 a${r} ${r} 0 1 0 ${-2 * r} 0 Z`;
}

export function diamond(x: number, y: number, r: number): string {
  return `M${P(x, y - r)} L${P(x + r, y)} L${P(x, y + r)} L${P(x - r, y)} Z`;
}

function hex(x: number, y: number, r: number): string {
  let d = "";
  for (let i = 0; i < 6; i++) {
    const a = ((60 * i - 90) * Math.PI) / 180;
    d += `${i ? " L" : "M"}${P(x + r * Math.cos(a), y + r * Math.sin(a))}`;
  }
  return `${d} Z`;
}

/** [outline, detail] centred on (x, y), roughly 2s wide. */
export function shape(kind: string, x: number, y: number, s: number): [string, string] {
  switch (kind) {
    case "hexagon":
      return [hex(x, y, s * 1.55), `${hex(x, y, s * 1.15)} M${P(x - 18, y - 10)} L${P(x, y - 22)} L${P(x + 18, y - 10)} M${P(x, y - 22)} V${f(y - 6)} ${ring(x - 18, y - 10, 3)} ${ring(x, y - 22, 3)} ${ring(x + 18, y - 10, 3)}`];
    case "idcard":
      return [rrect(x - s * 1.25, y - s * 0.8, s * 2.5, s * 1.6, 7), `${rrect(x - s, y - s * 0.5, s * 0.8, s * 0.9, 3)} M${P(x + s * 0.05, y - s * 0.35)} H${f(x + s * 0.95)} M${P(x + s * 0.05, y)} H${f(x + s * 0.8)} M${P(x + s * 0.05, y + s * 0.35)} H${f(x + s * 0.6)}`];
    case "magnifier":
      return [ring(x - s * 0.25, y - s * 0.25, s * 0.85), `M${P(x + s * 0.38, y + s * 0.38)} L${P(x + s * 1.05, y + s * 1.05)} ${ring(x - s * 0.25, y - s * 0.25, s * 0.55)}`];
    case "shield":
      return [`M${P(x, y - s * 1.1)} L${P(x + s * 0.95, y - s * 0.7)} V${f(y + s * 0.05)} Q${P(x + s * 0.9, y + s * 0.75)} ${P(x, y + s * 1.15)} Q${P(x - s * 0.9, y + s * 0.75)} ${P(x - s * 0.95, y + s * 0.05)} V${f(y - s * 0.7)} Z`,
        `${ring(x, y - s * 0.15, s * 0.22)} M${P(x, y + s * 0.07)} V${f(y + s * 0.5)}`];
    case "podium":
      return [`M${P(x - s * 1.3, y + s * 0.8)} V${f(y + s * 0.1)} H${f(x - s * 0.45)} V${f(y - s * 0.55)} H${f(x + s * 0.45)} V${f(y - s * 0.15)} H${f(x + s * 1.3)} V${f(y + s * 0.8)} Z`,
        `M${P(x - s * 0.45, y + s * 0.8)} V${f(y + s * 0.1)} M${P(x + s * 0.45, y + s * 0.8)} V${f(y - s * 0.15)} M${P(x, y - s * 0.95)} l${P(s * 0.12, s * 0.25)} h${f(-s * 0.24)} Z`];
    case "form":
      return [`M${P(x - s * 0.8, y - s * 1.05)} H${f(x + s * 0.35)} L${P(x + s * 0.8, y - s * 0.6)} V${f(y + s * 1.05)} H${f(x - s * 0.8)} Z`,
        `M${P(x + s * 0.35, y - s * 1.05)} V${f(y - s * 0.6)} H${f(x + s * 0.8)} M${P(x - s * 0.5, y - s * 0.4)} H${f(x + s * 0.4)} M${P(x - s * 0.5, y - s * 0.05)} H${f(x + s * 0.5)} ${rrect(x - s * 0.5, y + s * 0.3, s * 0.3, s * 0.3, 2)} M${P(x - s * 0.45, y + s * 0.45)} l${P(s * 0.08, s * 0.08)} l${P(s * 0.15, -s * 0.2)} M${P(x - s * 0.05, y + s * 0.45)} H${f(x + s * 0.5)}`];
    case "chart":
      return [rrect(x - s * 1.15, y - s * 0.85, s * 2.3, s * 1.7, 6), `M${P(x - s * 0.85, y + s * 0.55)} L${P(x - s * 0.3, y)} L${P(x + s * 0.1, y + s * 0.25)} L${P(x + s * 0.8, y - s * 0.5)} M${P(x + s * 0.55, y - s * 0.5)} H${f(x + s * 0.8)} V${f(y - s * 0.25)}`];
    case "envelope":
      return [rrect(x - s * 1.2, y - s * 0.8, s * 2.4, s * 1.6, 5), `M${P(x - s * 1.2, y - s * 0.72)} L${P(x, y + s * 0.15)} L${P(x + s * 1.2, y - s * 0.72)} M${P(x - s * 1.15, y + s * 0.75)} L${P(x - s * 0.35, y - s * 0.1)} M${P(x + s * 1.15, y + s * 0.75)} L${P(x + s * 0.35, y - s * 0.1)}`];
    case "bubble":
      return [`${rrect(x - s * 1.2, y - s * 0.85, s * 2.4, s * 1.3, 8)} M${P(x - s * 0.2, y + s * 0.45)} L${P(x - s * 0.75, y + s)} V${f(y + s * 0.45)}`,
        `M${P(x - s * 0.75, y - s * 0.4)} H${f(x + s * 0.75)} M${P(x - s * 0.75, y - s * 0.05)} H${f(x + s * 0.35)} M${P(x + s * 0.55, y + s * 0.1)} L${P(x + s * 0.95, y - s * 0.3)}`];
    case "chip":
      return [rrect(x - s * 0.8, y - s * 0.8, s * 1.6, s * 1.6, 5), `${rrect(x - s * 0.4, y - s * 0.4, s * 0.8, s * 0.8, 2)}${[-0.45, 0, 0.45].map((o) => ` M${P(x + s * o, y - s * 0.8)} v-8 M${P(x + s * o, y + s * 0.8)} v8 M${P(x - s * 0.8, y + s * o)} h-8 M${P(x + s * 0.8, y + s * o)} h8`).join("")}`];
    case "cards":
      return [rrect(x - s * 1.1, y - s * 0.55, s * 1.9, s * 1.35, 5), `M${P(x - s * 0.8, y - s * 0.55)} V${f(y - s * 0.85)} H${f(x + s * 1.1)} V${f(y + s * 0.5)} H${f(x + s * 0.8)} M${P(x - s * 0.8, y - s * 0.2)} H${f(x + s * 0.5)} M${P(x - s * 0.8, y + s * 0.15)} H${f(x + s * 0.2)}`];
    case "browser":
      return [rrect(x - s * 1.2, y - s * 0.85, s * 2.4, s * 1.7, 6), `M${P(x - s * 1.2, y - s * 0.4)} H${f(x + s * 1.2)} M${P(x - s, y - s * 0.62)} h4 M${P(x - s * 0.82, y - s * 0.62)} h4 ${rrect(x - s * 0.55, y - s * 0.72, s * 1.5, s * 0.2, 3)}`];
    case "cylinder":
      return [`M${P(x - s, y - s * 0.6)} A${P(s, s * 0.32)} 0 0 1 ${P(x + s, y - s * 0.6)} V${f(y + s * 0.6)} A${P(s, s * 0.32)} 0 0 1 ${P(x - s, y + s * 0.6)} Z`,
        `M${P(x - s, y - s * 0.6)} A${P(s, s * 0.32)} 0 0 0 ${P(x + s, y - s * 0.6)} M${P(x - s, y)} A${P(s, s * 0.32)} 0 0 0 ${P(x + s, y)}`];
    case "postbox":
      return [`M${P(x - s, y + s * 0.8)} V${f(y - s * 0.2)} A${P(s, s * 0.75)} 0 0 1 ${P(x + s, y - s * 0.2)} V${f(y + s * 0.8)} Z`,
        `M${P(x - s * 0.6, y - s * 0.1)} H${f(x + s * 0.6)} M${P(x + s, y - s * 0.25)} H${f(x + s * 1.45)} V${f(y - s * 0.75)} H${f(x + s * 1.15)}`];
    case "phone":
      return [rrect(x - s * 0.62, y - s * 1.1, s * 1.24, s * 2.2, 9), `M${P(x - s * 0.2, y + s * 0.88)} H${f(x + s * 0.2)} M${P(x - s * 0.15, y - s * 0.92)} H${f(x + s * 0.15)}`];
    default:
      return [ring(x, y, s), ""];
  }
}
