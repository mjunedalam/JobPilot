import { useCallback, useEffect, useRef, useState } from "react";
import type { LiveEvent } from "../../lib/live";
import { useOnLiveEvent } from "../live/LiveProvider";
import { AGENT_KEY, CAST, toolTarget, type NodeKey } from "./cast";

/** One piece of work moving between two members of the cast. `t` runs 0 → 1; the Agency view
 *  walks there (0–0.45), hands over (0.45–0.55) and walks back. A gated hop holds at 0.45
 *  until you decide. */
export interface Hop {
  id: number;
  from: NodeKey;
  to: NodeKey;
  label: string;
  gate?: string;
  t: number;
  delay: number;
  dur: number;
  released: boolean;
}

export interface LogLine {
  id: number;
  at: string;
  text: string;
  tone: "neutral" | "you" | "go" | "stop";
}

export const HOLD_AT = 0.45;

// ---- the demo day -------------------------------------------------------------------------

type Step = [NodeKey, NodeKey, string, number, string?, string?, string?]; // from, to, label, n, gate, chapter, onReject

export const DEMO: Step[] = [
  ["core", "profile", "load profile v3", 1, undefined, "start"],
  ["profile", "core", "skills · preferences", 1],
  ["core", "search", "search · last 7 days", 1],
  ["search", "portal", "need signed-in sessions", 1],
  ["portal", "portals", "fetch 3 sources", 3],
  ["portals", "portal", "11 postings", 2],
  ["portal", "search", "postings", 1],
  ["search", "matching", "9 unique after dedup", 2],
  ["matching", "models", "is this one a scam?", 1],
  ["models", "matching", "1 flagged", 1],
  ["matching", "core", "shortlist of 5", 1],
  ["core", "you", "H3 · approve 4 jobs?", 1, "H3", undefined, "mail"],
  ["you", "core", "approved by you", 1],
  ["core", "application", "apply · Acme Payments", 1, undefined, "apply"],
  ["application", "chrome", "fill steps 1–2", 2],
  ["application", "models", "unsure field → ask", 1],
  ["application", "core", "preview ready", 1],
  ["core", "you", "H5 · submit to Acme?", 1, "H5", undefined, "mail"],
  ["you", "core", "approved by you", 1],
  ["application", "chrome", "submit once", 1],
  ["application", "db", "proof · applied", 1],
  ["gmail", "email", "new mail", 1, undefined, "mail"],
  ["email", "models", "classify", 1],
  ["email", "db", "link → interview", 1],
  ["email", "core", "needs you", 1],
  ["core", "you", "H6 · draft a reply?", 1, "H6", undefined, "stats"],
  ["you", "core", "draft it", 1],
  ["core", "reply", "draft with your free slots", 1],
  ["reply", "models", "write the reply", 1],
  ["reply", "core", "draft ready", 1],
  ["core", "you", "H7 · send this exact text?", 1, "H7", undefined, "stats"],
  ["you", "core", "approved by you", 1],
  ["reply", "gmail", "send · hash-bound", 1],
  ["db", "analytics", "today's records", 1, undefined, "stats"],
  ["analytics", "you", "today: 2 applied · 1 interview", 1],
];
const CHAPTER: Record<string, number> = {};
DEMO.forEach((s, i) => {
  if (s[5]) CHAPTER[s[5]] = i;
});

export const GATE_TEXT: Record<string, [string, string]> = {
  H1: ["A portal needs you to sign in", "Finish the CAPTCHA or 2FA in the browser"],
  H3: ["Approve jobs for applying?", "The shortlist is in the Decision Center"],
  H4: ["A form question needs you", "Answer it in the Decision Center"],
  H5: ["Submit this application?", "Exactly what will be sent is in the Decision Center"],
  H6: ["An email needs you", "Draft a reply, ignore, or mark done"],
  H7: ["Send this exact reply?", "Read the text, then approve or edit"],
};

// ---- live events → hops ---------------------------------------------------------------------

export function hopsFor(event: LiveEvent): Array<Pick<Hop, "from" | "to" | "label" | "gate">> {
  const d = event.data;
  const agent = AGENT_KEY[String(d.agent ?? "")];
  const phase = String(d.phase ?? "");
  if (event.kind === "agent" && agent) {
    return phase === "start"
      ? [{ from: "core", to: agent, label: String(d.task ?? "work").replace(/_/g, " ") }]
      : [{ from: agent, to: "core", label: phase === "error" ? "stopped" : "done" }];
  }
  if (event.kind === "tool" && agent && phase === "start") {
    const target = toolTarget(String(d.tool ?? ""));
    if (target) return [{ from: agent, to: target, label: String(d.tool) }];
  }
  if (event.kind === "llm" && agent) {
    return phase === "start"
      ? [{ from: agent, to: "models", label: `ask the ${String(d.role ?? "model")} model` }]
      : phase === "done" ? [{ from: "models", to: agent, label: `${Number(d.tokens ?? 0).toLocaleString()} tokens` }] : [];
  }
  if (event.kind === "workflow" && d.status === "waiting") {
    return [{ from: "core", to: "you", label: "waiting for you", gate: "H?" }];
  }
  if (event.kind === "audit" && String(d.action ?? "").startsWith("decision:")) {
    return [{ from: "you", to: "core", label: String(d.text ?? "you decided") }];
  }
  return [];
}

// ---- the engine -------------------------------------------------------------------------------

const MAX_LIVE = 14;

function stamp(): string {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function useHops(mode: "live" | "demo", durFor: (from: NodeKey, to: NodeKey) => number = () => 1.15) {
  const hops = useRef<Hop[]>([]);
  const seq = useRef(1);
  const step = useRef(0);
  const [, setFrame] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [speed, setSpeed] = useState(1);
  const [autoTaps, setAutoTaps] = useState(true);
  const [waiting, setWaiting] = useState<string | null>(null);
  const waitSince = useRef(0);
  const [log, setLog] = useState<LogLine[]>([]);
  const pulses = useRef<Partial<Record<NodeKey, number>>>({});
  const durRef = useRef(durFor);
  durRef.current = durFor;

  const addLog = useCallback((text: string, tone: LogLine["tone"] = "neutral") => {
    setLog((l) => [{ id: seq.current++, at: stamp(), text, tone }, ...l].slice(0, 10));
  }, []);

  const push = useCallback((h: Pick<Hop, "from" | "to" | "label" | "gate">, delay = 0) => {
    const list = hops.current.filter((x) => x.t < 1);
    hops.current = [...list.slice(-(MAX_LIVE - 1)), { ...h, id: seq.current++, t: 0, delay, dur: durRef.current(h.from, h.to), released: false }];
  }, []);

  const reset = useCallback(() => {
    hops.current = [];
    step.current = 0;
    setWaiting(null);
    setLog([]);
  }, []);

  useEffect(() => reset(), [mode, reset]);

  const decide = useCallback((ok: boolean) => {
    if (ok) {
      hops.current = hops.current.map((h) => (h.gate ? { ...h, released: true } : h));
      addLog(`You approved ${waiting ?? ""}`.trim(), "you");
    } else {
      const gated = hops.current.find((h) => h.gate && !h.released);
      hops.current = hops.current.filter((h) => !h.gate);
      const st = DEMO.find((s) => s[4] === gated?.gate);
      step.current = st?.[6] ? (CHAPTER[st[6]] ?? 0) : 0;
      addLog(`You rejected ${waiting ?? ""} · nothing was sent`, "stop");
    }
    setWaiting(null);
  }, [addLog, waiting]);

  // Live: every relevant event becomes a hop; a decision releases the gate.
  useOnLiveEvent(
    useCallback((event: LiveEvent) => {
      if (mode !== "live") return;
      for (const h of hopsFor(event)) {
        if (h.from === "you") hops.current = hops.current.map((x) => (x.gate ? { ...x, released: true } : x));
        push(h);
        const who = CAST[h.from].label, to = CAST[h.to].label;
        addLog(h.gate ? "A decision is waiting for you" : `${who} → ${to}: ${h.label}`, h.gate || h.from === "you" ? "you" : "neutral");
      }
      if (event.kind === "audit" && String(event.data.action ?? "").startsWith("decision:")) setWaiting(null);
    }, [mode, push, addLog]),
  );

  useEffect(() => {
    let raf = 0, last = performance.now();
    const loop = (now: number) => {
      const dt = Math.min(0.1, (now - last) / 1000) * (playing ? speed : 0);
      last = now;
      let gate: string | null = null;
      hops.current = hops.current
        .map((h) => {
          if (h.delay > 0) return { ...h, delay: h.delay - dt };
          let t = Math.min(1, h.t + dt / h.dur);
          if (h.gate && !h.released && t >= HOLD_AT) {
            t = HOLD_AT;
            gate = h.gate;
          }
          if (t >= 1 && h.t < 1) pulses.current[h.to] = now;
          return { ...h, t };
        })
        .filter((h) => h.t < 1 || h.delay > 0);
      if (mode === "demo" && playing) {
        if (gate && autoTaps && now - waitSince.current > 2600 / speed) {
          hops.current = hops.current.map((h) => (h.gate ? { ...h, released: true } : h));
          addLog(`You approved ${gate}`, "you");
          gate = null;
        }
        if (hops.current.length === 0) {
          const st = DEMO[step.current];
          if (st) {
            for (let i = 0; i < st[3]; i++) push({ from: st[0], to: st[1], label: st[2], gate: st[4] }, i * 0.22);
            if (!st[4]) addLog(`${CAST[st[0]].label} → ${CAST[st[1]].label}: ${st[2]}`, st[0] === "you" || st[1] === "you" ? "you" : "neutral");
            step.current = (step.current + 1) % DEMO.length;
          }
        }
      }
      setWaiting((w) => {
        if (gate && w !== gate) waitSince.current = now;
        return gate ?? (mode === "live" ? w : null);
      });
      setFrame((f) => (f + 1) % 1_000_000);
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [mode, playing, speed, autoTaps, push, addLog]);

  return {
    hops: hops.current, pulses: pulses.current, waiting, log,
    playing, setPlaying, speed, setSpeed, autoTaps, setAutoTaps,
    approve: () => decide(true), reject: () => decide(false),
    jumpTo: (chapter: string) => {
      hops.current = [];
      step.current = CHAPTER[chapter] ?? 0;
      setWaiting(null);
    },
  };
}

export type HopEngine = ReturnType<typeof useHops>;
