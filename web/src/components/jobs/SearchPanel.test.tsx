import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router";
import { mockApi, renderWithQuery } from "../../test/utils";
import { SearchPanel } from "./SearchPanel";

afterEach(() => vi.unstubAllGlobals());

const naukri = {
  name: "naukri", label: "Naukri", enabled: true, tos_note: "", auth_methods: ["manual"],
  status: "connected", status_reason: "", has_session: true, has_credentials: false,
  last_login: null, session_expires_at: null,
};
const report = {
  since: "2026-10-13T00:00:00Z", until: "2026-10-20T00:00:00Z", queries: 2, fetched: 5, unique: 4,
  new_jobs: 3, updated_jobs: 1, skipped: { outside_window: 1 }, blocked: { shine: "captcha: sign in" },
  errors: {}, job_ids: [1, 2, 3],
};

function renderPanel() {
  return renderWithQuery(<MemoryRouter><SearchPanel /></MemoryRouter>);
}

describe("SearchPanel (checkpoint H2)", () => {
  it("needs at least one source", async () => {
    mockApi({ "GET /api/v1/portals": [] });
    renderPanel();
    expect(await screen.findByText(/No portals enabled yet/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Search/ })).toBeDisabled();
  });

  it("sends the chosen window and sources, and reports blocked portals", async () => {
    const { calls } = mockApi({ "GET /api/v1/portals": [naukri], "POST /api/v1/search": report });
    renderPanel();
    await userEvent.click(await screen.findByRole("button", { name: /Naukri/ }));
    await userEvent.click(screen.getByRole("radio", { name: "24 hours" }));
    await userEvent.type(screen.getByPlaceholderText("https://company.com/careers"), "https://hooli.example/careers{enter}");
    await userEvent.click(screen.getByRole("button", { name: /Search/ }));

    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      recency: { preset: "24h" },
      sources: ["naukri", "https://hooli.example/careers"],
      include_undated: false,
    });
    expect(await screen.findByText(/needs you \(H1\)/)).toBeInTheDocument();
    expect(screen.getByText("1 outside window")).toBeInTheDocument();
  });
});
