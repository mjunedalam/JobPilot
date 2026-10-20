import { useQueryClient } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";
import { ApiError, api, getToken, setToken, unwrap } from "../api/client";
import { Button, Field } from "./ui";

/** Asks once for the API bearer token (JOBPILOT_API_TOKEN) and verifies it before continuing. */
export function TokenGate({ children }: { children: ReactNode }) {
  const [token, setLocal] = useState(getToken() ?? "");
  const [ready, setReady] = useState(Boolean(getToken()));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const client = useQueryClient();

  if (ready) return <>{children}</>;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setToken(token.trim());
    try {
      unwrap(await api.GET("/api/v1/profile"));
      await client.invalidateQueries();
      setReady(true);
    } catch (err) {
      setToken(null);
      setError(
        err instanceof ApiError && err.unauthorized
          ? "That token was rejected. Check JOBPILOT_API_TOKEN in your .env."
          : err instanceof Error
            ? err.message
            : "Could not reach the API.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="gate">
      <form className="gate-card" onSubmit={submit}>
        <KeyRound size={22} className="gate-icon" />
        <p className="eyebrow">Pre-flight</p>
        <h1 className="gate-title">Sign in to your flight deck</h1>
        <p className="muted">
          Paste the API token from <span className="mono">JOBPILOT_API_TOKEN</span> in your{" "}
          <span className="mono">.env</span>. Generate one with{" "}
          <span className="mono">jobpilot keygen --token</span>.
        </p>
        <Field label="API token" error={error}>
          <input
            className="input mono"
            type="password"
            autoComplete="off"
            value={token}
            onChange={(e) => setLocal(e.target.value)}
            required
          />
        </Field>
        <Button variant="primary" type="submit" busy={busy}>
          Continue
        </Button>
      </form>
    </div>
  );
}
