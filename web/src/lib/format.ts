/** Display formatting for jobs. Pure functions, unit-tested. */

export function relativeTime(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "date unknown";
  const then = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  const minutes = Math.round((now.getTime() - then.getTime()) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return days < 30 ? `${days}d ago` : `${Math.round(days / 30)}mo ago`;
}

function compactAmount(amount: number, currency: string): string {
  if (currency === "INR") {
    if (amount >= 10_000_000) return `${+(amount / 10_000_000).toFixed(1)} Cr`;
    return `${+(amount / 100_000).toFixed(1)} L`;
  }
  return amount >= 1000 ? `${Math.round(amount / 1000)}k` : String(amount);
}

const SYMBOL: Record<string, string> = { INR: "₹", USD: "$", EUR: "€", GBP: "£" };

export function salaryRange(min: number | null, max: number | null, currency: string): string | null {
  if (min == null && max == null) return null;
  const symbol = SYMBOL[currency] ?? (currency ? `${currency} ` : "");
  const fmt = (v: number) => compactAmount(v, currency || "INR");
  if (min != null && max != null) return `${symbol}${fmt(min)}–${fmt(max)}`;
  return `${symbol}${fmt((min ?? max) as number)}${min != null ? "+" : ""}`;
}

export function experienceBand(min: number | null, max: number | null): string | null {
  if (min == null && max == null) return null;
  if (min != null && max != null) return `${min}–${max} yrs`;
  return min != null ? `${min}+ yrs` : `up to ${max} yrs`;
}

export type ScoreBand = "strong" | "fair" | "weak";

export function scoreBand(score: number | null | undefined): ScoreBand {
  if (score == null || score < 50) return "weak";
  return score >= 75 ? "strong" : "fair";
}

export interface ParsedFlag {
  severity: "high" | "soft";
  code: string;
  message: string;
}

/** Server labels look like "high:upfront_fee: asks for a fee or deposit". */
export function parseFlag(label: string): ParsedFlag {
  const [severity, rest = ""] = label.split(/:(.*)/s);
  const [code, message = ""] = rest.split(/: (.*)/s);
  return { severity: severity === "high" ? "high" : "soft", code: code ?? "", message };
}
