import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Decision } from "../../api/queries";
import { mockApi, renderWithQuery } from "../../test/utils";
import { H3Review, toSelection } from "./H3Review";
import { H4Questions } from "./H4Questions";
import { H5Preview } from "./H5Preview";

afterEach(() => vi.unstubAllGlobals());

const row = (rank: number, flags: string[] = []) => ({
  rank, job_id: 100 + rank, title: `Job ${rank}`, company: "Acme", location: "Pune",
  experience_min: null, experience_max: null, salary_min: null, salary_max: null,
  salary_currency: "", posted_at: null, portals: ["naukri"], match_score: 80,
  match_reason: "", red_flags: flags, url: "https://x/1",
});

const decision = (checkpoint: string, payload: Record<string, unknown>): Decision => ({
  thread_id: "t1", kind: "apply", interrupt_id: "i1", checkpoint,
  payload: { checkpoint, ...payload },
});

describe("toSelection", () => {
  it("compresses runs", () => {
    expect(toSelection([5, 1, 6, 3, 7, 8])).toBe("1,3,5-8");
    expect(toSelection([2, 3])).toBe("2,3");
    expect(toSelection([])).toBe("");
  });
});

describe("H3Review", () => {
  it("approves exactly the ticked jobs and warns about high red flags", async () => {
    const { calls } = mockApi({ "POST /api/v1/decisions/t1": { thread_id: "t1", status: "running" } });
    const jobs = [row(1), row(2, ["high:upfront_fee: asks for a fee or deposit"]), row(3)];
    renderWithQuery(<H3Review decision={decision("H3", { jobs, allowed: ["approve", "reject"] })} />);
    await userEvent.click(screen.getByLabelText("Select #1 Job 1"));
    await userEvent.click(screen.getByLabelText("Select #2 Job 2"));
    expect(screen.getByText(/high-severity red flag/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Approve 2" }));
    await waitFor(() => expect(calls.length).toBe(1));
    expect(calls[0]?.body).toEqual({ interrupt_id: "i1", answer: { action: "approve", selection: "1,2" } });
  });
});

describe("H4Questions", () => {
  it("prefills the suggestion and sends answers with the save choice", async () => {
    const { calls } = mockApi({ "POST /api/v1/decisions/t1": { thread_id: "t1", status: "running" } });
    const questions = [{ key: "why", label: "Why us?", kind: "textarea", options: [],
                         suggestion: "", confidence: 0, source: "none" },
                       { key: "relocate", label: "Relocate?", kind: "radio", options: ["Yes", "No"],
                         suggestion: "Yes", confidence: 0.5, source: "rag" }];
    renderWithQuery(<H4Questions decision={decision("H4", { questions, job: { title: "Job", company: "Acme" } })} />);
    const cont = screen.getByRole("button", { name: "Continue" });
    expect(cont).toBeDisabled(); // "Why us?" is still empty
    await userEvent.type(screen.getByLabelText("Why us?"), "Scale");
    await userEvent.click(screen.getAllByLabelText(/Save to my Answer Bank/)[1] as HTMLElement); // untick relocate
    await userEvent.click(cont);
    await waitFor(() => expect(calls.length).toBe(1));
    expect(calls[0]?.body).toEqual({ interrupt_id: "i1",
      answer: { action: "answer", values: { why: "Scale", relocate: "Yes" }, save: ["why"] } });
  });
});

describe("H5Preview", () => {
  it("submits as shown, or sends only changed fields by key", async () => {
    const { calls } = mockApi({ "POST /api/v1/decisions/t1": { thread_id: "t1", status: "running" } });
    const payload = { answers: { "Full name": "Asha Rao", "Upload resume / CV": "(resume file)" },
                      keys: { "Full name": "fullName", "Upload resume / CV": "cv" }, job: { title: "Job", company: "Acme" } };
    renderWithQuery(<H5Preview decision={decision("H5", payload)} />);
    await userEvent.click(screen.getByRole("button", { name: /Edit/ }));
    expect(screen.queryByLabelText("Edit Upload resume / CV")).not.toBeInTheDocument(); // files aren't text
    const name = screen.getByLabelText("Edit Full name");
    await userEvent.clear(name);
    await userEvent.type(name, "Asha R.");
    await userEvent.click(screen.getByRole("button", { name: "Apply 1 edit(s)" }));
    await waitFor(() => expect(calls.length).toBe(1));
    expect(calls[0]?.body).toEqual({ interrupt_id: "i1", answer: { action: "edit", values: { fullName: "Asha R." } } });
  });
});
