import createClient, { type Middleware } from "openapi-fetch";
import type { components, paths } from "./schema";

export type Schemas = components["schemas"];

const TOKEN_KEY = "jobpilot.apiToken";

/** The API binds to 127.0.0.1 and this is a single-user tool, so the bearer token is kept in
 *  localStorage for convenience. Storage can be unavailable (private mode), so every access
 *  is guarded and the app still works for the current session. */
let memoryToken: string | null = null;

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY); // storage is the source of truth when available
  } catch {
    return memoryToken; // storage blocked: fall back to this tab's copy
  }
}

export function setToken(token: string | null): void {
  memoryToken = token;
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage blocked: memory token still works */
  }
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
  ) {
    super(message);
    this.name = "ApiError";
  }

  get unauthorized(): boolean {
    return this.status === 401;
  }
}

const auth: Middleware = {
  onRequest({ request }) {
    const token = getToken();
    if (token) request.headers.set("Authorization", `Bearer ${token}`);
    return request;
  },
};

// Same-origin API. An absolute base keeps Request construction valid outside browsers (tests).
// fetch is resolved per call (not captured at import) so it can be swapped in tests.
export const api = createClient<paths>({
  baseUrl: globalThis.location?.origin ?? "",
  fetch: (request) => globalThis.fetch(request),
});
api.use(auth);

interface EnvelopeLike<T> {
  success?: boolean;
  data?: T | null;
  error?: { code: string; message: string } | null;
}

/** Turn an openapi-fetch result into data or a thrown ApiError. */
export function unwrap<T>(result: {
  data?: EnvelopeLike<T>;
  error?: unknown;
  response: Response;
}): T {
  const body = (result.data ?? result.error) as EnvelopeLike<T> | undefined;
  if (!result.response.ok || !body?.success) {
    const message = body?.error?.message ?? `Request failed (${result.response.status})`;
    throw new ApiError(message, result.response.status, body?.error?.code ?? "unknown");
  }
  return body.data as T;
}

/** Multipart upload; openapi-fetch's JSON body handling does not cover files. */
export async function uploadResume(file: File, label?: string) {
  const form = new FormData();
  form.append("file", file);
  if (label) form.append("label", label);
  const token = getToken();
  const response = await fetch("/api/v1/profile/import", {
    method: "POST",
    body: form,
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  const body = (await response.json().catch(() => ({}))) as EnvelopeLike<Schemas["ImportJobOut"]>;
  return unwrap({ data: body, response });
}
