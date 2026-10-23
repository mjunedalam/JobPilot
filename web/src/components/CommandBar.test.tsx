import { fireEvent, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { mockApi, renderWithQuery } from "../test/utils";
import { CommandBar } from "./CommandBar";

function Where() {
  return <span data-testid="where">{useLocation().pathname}</span>;
}

function setup(health = { kill_switch_engaged: false }) {
  const api = mockApi({
    "GET /api/v1/workflows/decisions": [],
    "GET /api/v1/health": health,
    "POST /api/v1/mail/poll": { fetched: 0 },
    "POST /api/v1/system/stop": { engaged: true },
  });
  const onClose = vi.fn();
  renderWithQuery(
    <MemoryRouter>
      <Routes><Route path="*" element={<><CommandBar open onClose={onClose} onTheme={() => {}} /><Where /></>} /></Routes>
    </MemoryRouter>,
  );
  return { api, onClose, input: screen.getByRole("combobox") };
}

describe("CommandBar", () => {
  it("navigates to the page you pick with the keyboard", async () => {
    const { input, onClose } = setup();
    fireEvent.change(input, { target: { value: "live ops" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/live"));
    expect(onClose).toHaveBeenCalled();
  });

  it("runs an action and reports it", async () => {
    const { input, api } = setup();
    fireEvent.change(input, { target: { value: "check mail" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await screen.findByText("Check mail now: done");
    expect(api.calls.some((c) => c.method === "POST" && c.path === "/api/v1/mail/poll")).toBe(true);
  });

  it("asks for a second Enter before stopping everything", async () => {
    const { input, api } = setup();
    fireEvent.change(input, { target: { value: "stop everything" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.getByRole("status")).toHaveTextContent("Press Enter again");
    expect(api.calls.some((c) => c.path === "/api/v1/system/stop")).toBe(false);
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(api.calls.some((c) => c.path === "/api/v1/system/stop")).toBe(true));
  });

  it("closes on Escape", () => {
    const { input, onClose } = setup();
    fireEvent.keyDown(input, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});
