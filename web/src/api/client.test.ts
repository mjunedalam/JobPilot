import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, getToken, setToken, unwrap } from "./client";
import { mockApi } from "../test/utils";

afterEach(() => vi.unstubAllGlobals());

describe("unwrap", () => {
  it("returns data from a successful envelope", () => {
    const response = new Response(null, { status: 200 });
    expect(unwrap({ data: { success: true, data: { a: 1 } }, response })).toEqual({ a: 1 });
  });

  it("throws ApiError carrying the server message and code", () => {
    const response = new Response(null, { status: 423 });
    const error = { success: false, error: { code: "kill_switch_engaged", message: "all agents are halted" } };
    expect(() => unwrap({ error, response })).toThrowError(
      expect.objectContaining({ status: 423, code: "kill_switch_engaged", message: "all agents are halted" }),
    );
  });

  it("flags 401 as unauthorized", () => {
    const err = new ApiError("nope", 401, "http_401");
    expect(err.unauthorized).toBe(true);
  });
});

describe("token handling", () => {
  it("attaches the bearer token to API requests", async () => {
    setToken("secret-token");
    const { fetchMock } = mockApi({ "GET /api/v1/profile": { full_name: "Asha" } });
    const data = unwrap(await api.GET("/api/v1/profile"));
    expect(data).toEqual({ full_name: "Asha" });
    const request = fetchMock.mock.calls[0]?.[0] as Request;
    expect(request.headers.get("Authorization")).toBe("Bearer secret-token");
  });

  it("clears the token", () => {
    setToken("x");
    setToken(null);
    expect(getToken()).toBeNull();
  });
});
