import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Provider, Route } from "../../api/queries";
import { mockApi, renderWithQuery } from "../../test/utils";
import { RouteRow } from "./RouteRow";

afterEach(() => vi.unstubAllGlobals());

const providers: Provider[] = [
  { name: "ollama", preset: "ollama", type: "openai_compatible", base_url: "http://localhost:11434/v1",
    structured_mode: "json_object", reasoning_effort: "none", enabled: true, has_api_key: false,
    is_local: true, source: "config" },
  { name: "groq", preset: "groq", type: "openai_compatible", base_url: "https://api.groq.com/openai/v1",
    structured_mode: "json_object", reasoning_effort: null, enabled: true, has_api_key: true,
    is_local: false, source: "user" },
];
const route: Route = { role: "writing", provider: "ollama", model: "gemma3:12b", fallbacks: [], source: "config" };

describe("RouteRow", () => {
  it("lists the provider's live models and saves a new choice", async () => {
    const { calls } = mockApi({
      "GET /api/v1/llm/providers/ollama/models": { provider: "ollama", models: ["gemma3:12b", "qwen3:8b"] },
      "GET /api/v1/llm/providers/groq/models": { provider: "groq", models: ["llama-3.3-70b-versatile"] },
      "PUT /api/v1/llm/routes/writing": { ...route, provider: "groq", model: "llama-3.3-70b-versatile", source: "user" },
    });
    const { container } = renderWithQuery(<RouteRow route={route} providers={providers} />);

    await waitFor(() => expect(container.querySelectorAll("datalist option")).toHaveLength(2));
    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toBeDisabled(); // nothing changed yet

    await userEvent.selectOptions(screen.getByLabelText("Writing primary provider"), "groq");
    const model = screen.getByLabelText("Writing primary model");
    expect(model).toHaveValue(""); // switching provider clears the model
    await userEvent.type(model, "llama-3.3-70b-versatile");
    await userEvent.click(save);

    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({
      provider: "groq",
      model: "llama-3.3-70b-versatile",
      fallbacks: [],
    });
  });

  it("shows the reply from a test flight", async () => {
    mockApi({
      "GET /api/v1/llm/providers/ollama/models": { provider: "ollama", models: [] },
      "POST /api/v1/llm/routes/writing/try": {
        ok: true, provider: "ollama", model: "gemma3:12b", reply: "Hello from Gemma",
        latency_ms: 1400, input_tokens: 20, output_tokens: 6, error: "",
      },
    });
    renderWithQuery(<RouteRow route={route} providers={providers} />);
    await userEvent.click(screen.getByRole("button", { name: /Test flight/ }));
    expect(await screen.findByRole("status")).toHaveTextContent("Hello from Gemma");
  });
});
