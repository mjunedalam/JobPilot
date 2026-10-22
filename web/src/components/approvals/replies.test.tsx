import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Decision } from "../../api/queries";
import { mockApi, renderWithQuery } from "../../test/utils";
import { H6Email } from "./H6Email";
import { H7Reply } from "./H7Reply";

afterEach(() => vi.unstubAllGlobals());

const email = {
  email_id: 5, sender: "Priya <priya@acme.com>", subject: "Interview for Backend Engineer",
  summary: "Priya invites you to a technical interview.", category: "interview_invite",
  suggested_action: "Reply with your availability", received_at: new Date().toISOString(),
  snippet: "", application: { id: 1, title: "Backend Engineer", company: "Acme", status: "interview" },
};

const draft = {
  n: 2, versions: 2, to: ["priya@acme.com"], cc: [], subject: "Re: Interview for Backend Engineer",
  body: "Hi Priya,\n\nMonday works.\n\nBest regards,\nAsha Rao", attachments: [],
  source: "model", warnings: ["Calendar not connected: times were not checked against your calendar."],
};

const decision = (checkpoint: string, payload: Record<string, unknown>): Decision => ({
  thread_id: "r1", kind: "reply", interrupt_id: "i9", checkpoint, payload: { checkpoint, ...payload },
});

const ok = { "POST /api/v1/decisions/r1": { thread_id: "r1", status: "running" }, "GET /api/v1/resumes": [] };

describe("H6Email", () => {
  it("shows the email and drafts with the instruction", async () => {
    const { calls } = mockApi(ok);
    renderWithQuery(<H6Email decision={decision("H6", { email, allowed: ["draft", "ignore", "done"] })} />);
    expect(screen.getByText("Interview")).toBeInTheDocument();
    expect(screen.getByText(/About your application/)).toHaveTextContent("Backend Engineer");
    await userEvent.type(screen.getByLabelText("How should the reply go?"), "accept, propose Thursday 3-5 PM IST");
    await userEvent.click(screen.getByRole("button", { name: /Draft a reply/ }));
    await waitFor(() => expect(calls.length).toBe(1));
    expect(calls[0]?.body).toEqual({
      interrupt_id: "i9", answer: { action: "draft", instruction: "accept, propose Thursday 3-5 PM IST" },
    });
  });
});

describe("H7Reply", () => {
  it("shows the exact draft and approves it", async () => {
    const { calls } = mockApi(ok);
    renderWithQuery(<H7Reply decision={decision("H7", { email, draft, error: "", allowed: ["approve", "edit", "regenerate", "discard"] })} />);
    expect(screen.getByText("Version 2 of 2")).toBeInTheDocument();
    expect(screen.getByLabelText("Reply draft")).toHaveTextContent("Monday works.");
    expect(screen.getByText(/Calendar not connected/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Approve & send/ }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ interrupt_id: "i9", answer: { action: "approve" } });
  });

  it("edits into a new version instead of sending", async () => {
    const { calls } = mockApi(ok);
    renderWithQuery(<H7Reply decision={decision("H7", { email, draft, error: "", allowed: ["approve", "edit", "regenerate", "discard"] })} />);
    await userEvent.click(screen.getByRole("button", { name: /Edit/ }));
    const body = screen.getByLabelText("Message");
    await userEvent.clear(body);
    await userEvent.type(body, "Thursday is better.");
    await userEvent.clear(screen.getByLabelText("Cc"));
    await userEvent.type(screen.getByLabelText("Cc"), "hr@acme.com");
    await userEvent.click(screen.getByRole("button", { name: "Save as new version" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      interrupt_id: "i9",
      answer: { action: "edit", to: ["priya@acme.com"], cc: ["hr@acme.com"],
                subject: "Re: Interview for Backend Engineer", body: "Thursday is better.", attachments: [] },
    });
  });

  it("offers no approve button when drafting failed", () => {
    mockApi(ok);
    renderWithQuery(<H7Reply decision={decision("H7", { email, draft: null, error: "Drafting failed: model down", allowed: ["edit", "regenerate", "discard"] })} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Drafting failed");
    expect(screen.queryByRole("button", { name: /Approve/ })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Message")).toBeInTheDocument(); // write it yourself
  });
});
