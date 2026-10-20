import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { getToken } from "../api/client";
import { mockApi, renderWithQuery } from "../test/utils";
import { TokenGate } from "./TokenGate";

afterEach(() => vi.unstubAllGlobals());

describe("TokenGate", () => {
  it("shows the app after a valid token", async () => {
    mockApi({ "GET /api/v1/profile": {} });
    renderWithQuery(<TokenGate>app body</TokenGate>);
    await userEvent.type(screen.getByLabelText("API token"), "good-token");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByText("app body")).toBeInTheDocument();
    expect(getToken()).toBe("good-token");
  });

  it("explains a rejected token and does not keep it", async () => {
    mockApi({
      "GET /api/v1/profile": () => ({ status: 401, body: { success: false, error: { code: "http_401", message: "bad" } } }),
    });
    renderWithQuery(<TokenGate>app body</TokenGate>);
    await userEvent.type(screen.getByLabelText("API token"), "bad-token");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("JOBPILOT_API_TOKEN");
    expect(screen.queryByText("app body")).not.toBeInTheDocument();
    expect(getToken()).toBeNull();
  });
});
