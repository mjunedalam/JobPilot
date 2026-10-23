import { describe, expect, it } from "vitest";
import { agencyDuration, route, walkerOf } from "./agencyArt";
import { hopsFor } from "./engine";

const ev = (kind: string, data: Record<string, unknown>) => ({ id: 1, ts: 0, kind, data });

describe("live events become moving work", () => {
  it("maps agent runs, tool calls, model calls and decisions", () => {
    expect(hopsFor(ev("agent", { agent: "search_agent", phase: "start", task: "search" }))).toEqual([{ from: "core", to: "search", label: "search" }]);
    expect(hopsFor(ev("tool", { agent: "application_agent", tool: "apply.submit", phase: "start" }))[0]).toMatchObject({ from: "application", to: "chrome" });
    expect(hopsFor(ev("tool", { agent: "email_monitor", tool: "mail.list_new", phase: "done" }))).toEqual([]);
    expect(hopsFor(ev("llm", { agent: "reply_drafting", role: "writing", phase: "done", tokens: 1200 }))[0]).toMatchObject({ from: "models", to: "reply" });
    expect(hopsFor(ev("workflow", { status: "waiting" }))[0]).toMatchObject({ from: "core", to: "you", gate: "H?" });
    expect(hopsFor(ev("audit", { action: "decision:approve", text: "You approved job #4" }))[0]).toMatchObject({ from: "you", to: "core" });
    expect(hopsFor(ev("tool", { agent: "unknown_agent", tool: "x", phase: "start" }))).toEqual([]);
  });

  it("picks who walks in the office, and the boss never walks", () => {
    expect(walkerOf("gmail", "email")).toEqual({ walker: "email", dest: "gmail" });
    expect(walkerOf("core", "you")).toEqual({ walker: "core", dest: "you" });
    expect(walkerOf("you", "core")).toBeNull();
    const path = route("search", "matching");
    expect(path[0]).toEqual([462, 150]); // from the Scout's seat
    expect(path[path.length - 1]).toEqual([602, 260]); // to the Analyst's desk
    expect(agencyDuration("search", "matching")).toBeGreaterThan(2);
  });
});
