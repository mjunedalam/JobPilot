import { describe, expect, it } from "vitest";
import { fuzzyScore, rankCommands, type Command } from "./commands";

const cmd = (id: string, group: Command["group"], title: string, keywords?: string): Command => ({ id, group, title, keywords, run: () => {} });

describe("fuzzyScore", () => {
  it("matches an in-order subsequence and rejects anything else", () => {
    expect(fuzzyScore("chm", "Check mail now")).not.toBeNull();
    expect(fuzzyScore("mzz", "Check mail now")).toBeNull();
  });

  it("scores word starts above letters in the middle of words", () => {
    expect(fuzzyScore("ma", "Check mail")!).toBeGreaterThan(fuzzyScore("ai", "Check mail")!);
  });
});

describe("rankCommands", () => {
  const all = [
    cmd("p-jobs", "Go to", "Jobs", "search postings"),
    cmd("mail", "Actions", "Check mail now", "poll inbox"),
    cmd("d1", "Decisions", "Approve 4 jobs?"),
  ];

  it("keeps the groups in order when the query is empty", () => {
    expect(rankCommands(all, "").map((c) => c.id)).toEqual(["d1", "mail", "p-jobs"]);
  });

  it("finds commands by their keywords", () => {
    expect(rankCommands(all, "inbox").map((c) => c.id)).toEqual(["mail"]);
  });

  it("returns nothing when nothing matches", () => {
    expect(rankCommands(all, "qqq")).toEqual([]);
  });
});
