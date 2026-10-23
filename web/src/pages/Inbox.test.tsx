import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { mockApi, renderWithQuery } from "../test/utils";
import { Inbox } from "./Inbox";

afterEach(() => vi.unstubAllGlobals());

const email = {
  id: 7, sender: "careers@globex.io", subject: "Your application", snippet: "", summary: "Declined.",
  category: "rejection", suggested_action: "No action needed", classified_by: "model",
  action_status: "info", received_at: new Date().toISOString(), link_reason: "company + title",
  suggested_status: "rejected", workflow_thread_id: "", reply_open: false,
  application: { id: 3, title: "Platform Engineer", company: "Globex", status: "applied" },
};

describe("Inbox", () => {
  it("applies an unverified email's suggestion only when the user asks", async () => {
    const { calls } = mockApi({
      "GET /api/v1/mail": { account: null, configured: { gmail: false, outlook: false, local: true },
                            poll_interval_minutes: 15, notifications: [] },
      "GET /api/v1/emails": [email],
      "POST /api/v1/emails/7/apply-suggestion": { ...email, suggested_status: "" },
    });
    renderWithQuery(<MemoryRouter><Inbox /></MemoryRouter>);
    expect(await screen.findByText(/sender isn't verified as/)).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Apply rejected" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/v1/emails/7/apply-suggestion")).toBe(true));
  });

  it("warns when sending isn't allowed and offers a reconnect and a new reply", async () => {
    const { calls } = mockApi({
      "GET /api/v1/mail": {
        account: { provider: "gmail", address: "me@gmail.com", status: "read_only",
                   status_reason: "Sending was not allowed on the consent page.",
                   calendar_enabled: false, connected_at: null, last_polled_at: null },
        configured: { gmail: true, outlook: false, local: true }, poll_interval_minutes: 15,
        notifications: [],
      },
      "GET /api/v1/emails": [{ ...email, suggested_status: "", action_status: "drafting" }],
      "POST /api/v1/mail/connect": { authorization_url: "https://accounts.google.com/x", account: null },
      "POST /api/v1/emails/7/reply": { ...email, reply_open: true },
    });
    vi.stubGlobal("open", vi.fn());
    renderWithQuery(<MemoryRouter><Inbox /></MemoryRouter>);
    expect(await screen.findByRole("alert")).toHaveTextContent("Replies can't be sent yet");
    await userEvent.click(screen.getByRole("button", { name: "Reconnect with send permission" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/v1/mail/connect")).toBe(true));
    expect(calls.find((c) => c.path === "/api/v1/mail/connect")?.body).toMatchObject({ provider: "gmail" });
    await userEvent.click(screen.getByRole("button", { name: "Reply again" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/v1/emails/7/reply")).toBe(true));
  });
});
