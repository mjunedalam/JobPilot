import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { vi } from "vitest";

export function renderWithQuery(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

type Handler = (url: URL, init: RequestInit & { body?: unknown }) => { status?: number; body: unknown };

/** Route fetch calls by "METHOD /path" and record them. Envelope-wraps plain payloads. */
export function mockApi(routes: Record<string, Handler | unknown>) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(String(input), init);
    const url = new URL(request.url, "http://localhost");
    const text = request.method === "GET" ? "" : await request.clone().text();
    const body = text ? JSON.parse(text) : undefined;
    calls.push({ method: request.method, path: url.pathname, body });
    const route = routes[`${request.method} ${url.pathname}`];
    if (route === undefined) return json(404, { success: false, error: { code: "http_404", message: "not found" } });
    const result =
      typeof route === "function"
        ? (route as Handler)(url, { ...init, body })
        : { body: { success: true, data: route, error: null, meta: {} } };
    return json(result.status ?? 200, result.body);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}
