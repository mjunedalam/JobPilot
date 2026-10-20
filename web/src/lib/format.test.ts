import { describe, expect, it } from "vitest";
import { experienceBand, parseFlag, relativeTime, salaryRange, scoreBand } from "./format";

describe("format", () => {
  const now = new Date("2026-10-20T12:00:00Z");

  it("relative time handles naive UTC strings from the API", () => {
    expect(relativeTime("2026-10-20T11:30:00", now)).toBe("30m ago");
    expect(relativeTime("2026-10-17T12:00:00Z", now)).toBe("3d ago");
    expect(relativeTime(null, now)).toBe("date unknown");
  });

  it("salary uses lakhs and crores for INR", () => {
    expect(salaryRange(2_500_000, 3_500_000, "INR")).toBe("₹25 L–35 L");
    expect(salaryRange(12_000_000, null, "INR")).toBe("₹1.2 Cr+");
    expect(salaryRange(120_000, 150_000, "USD")).toBe("$120k–150k");
    expect(salaryRange(null, null, "")).toBeNull();
  });

  it("experience band", () => {
    expect(experienceBand(5, 8)).toBe("5–8 yrs");
    expect(experienceBand(10, null)).toBe("10+ yrs");
    expect(experienceBand(null, null)).toBeNull();
  });

  it("score bands", () => {
    expect(scoreBand(90)).toBe("strong");
    expect(scoreBand(60)).toBe("fair");
    expect(scoreBand(null)).toBe("weak");
  });

  it("parses server flag labels, keeping colons in the message", () => {
    expect(parseFlag("high:upfront_fee: asks for a fee or deposit")).toEqual({
      severity: "high", code: "upfront_fee", message: "asks for a fee or deposit",
    });
    expect(parseFlag("soft:model_review: reasons: a; b").message).toBe("reasons: a; b");
  });
});
