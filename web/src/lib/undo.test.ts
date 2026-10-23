import { afterEach, describe, expect, it, vi } from "vitest";
import { Cancelled, undoLabel, undoStore } from "./undo";

afterEach(() => {
  vi.useRealTimers();
  undoStore.windowMs = 0;
});

describe("undo window", () => {
  it("holds a submit until the window passes, and undo cancels it", async () => {
    vi.useFakeTimers();
    undoStore.windowMs = 5000;
    const sent = vi.fn();
    const first = undoStore.hold("Sending the reply").then(sent);
    expect(undoStore.pending).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(4900);
    expect(sent).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(200);
    await first;
    expect(sent).toHaveBeenCalledTimes(1);

    const second = undoStore.hold("Submitting the application");
    undoStore.undo();
    await expect(second).rejects.toBeInstanceOf(Cancelled);
    expect(undoStore.pending).toHaveLength(0);
  });

  it("only applies to answers that commit to something outside", () => {
    expect(undoLabel("H7", { action: "approve" })).toBe("Sending the reply");
    expect(undoLabel("H5", { action: "submit" })).toBe("Submitting the application");
    expect(undoLabel("H5", { action: "skip" })).toBeNull();
    expect(undoLabel("H6", { action: "draft" })).toBeNull();
  });
});
