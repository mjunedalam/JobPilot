import { KeyRound, LogIn, Unplug } from "lucide-react";
import { useState, type FormEvent } from "react";
import {
  useDisconnectPortal,
  usePortalLogin,
  useSavePortalCredentials,
  useSetPortalEnabled,
  type Portal,
} from "../../api/queries";
import { relativeTime } from "../../lib/format";
import { Badge, Button, ErrorNote, Field, Lamp } from "../ui";

type Name = "naukri" | "linkedin" | "shine";
const STATUS_TONE: Record<string, "go" | "amber" | "stop" | "neutral"> = {
  connected: "go", needs_attention: "amber", blocked: "stop", disconnected: "neutral",
};

export function PortalCard({ portal }: { portal: Portal }) {
  const name = portal.name as Name;
  const [consent, setConsent] = useState(false);
  const [showCreds, setShowCreds] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const toggle = useSetPortalEnabled();
  const login = usePortalLogin();
  const creds = useSavePortalCredentials();
  const disconnect = useDisconnectPortal();
  const canPassword = portal.auth_methods.includes("password");

  function saveCreds(event: FormEvent) {
    event.preventDefault();
    creds.mutate({ name, username, password }, {
      onSuccess: () => { setPassword(""); setShowCreds(false); },
    });
  }

  return (
    <article className={`portal ${portal.enabled ? "portal-on" : ""}`}>
      <header className="portal-head">
        <h3 className="portal-name">{portal.label}</h3>
        <Lamp tone={STATUS_TONE[portal.status] ?? "neutral"} label={portal.status.replace("_", " ")} />
      </header>
      <p className="portal-tos">{portal.tos_note}</p>

      {!portal.enabled ? (
        <div className="portal-enable">
          <label className="check-inline">
            <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
            I understand and accept this risk for my own account
          </label>
          <Button variant="primary" disabled={!consent} busy={toggle.isPending}
                  onClick={() => toggle.mutate({ name, enabled: true, accept_terms: consent })}>
            Enable {portal.label}
          </Button>
        </div>
      ) : (
        <>
          {portal.status_reason && <p className="callout callout-amber">{portal.status_reason}</p>}
          <dl className="provider-meta">
            <dt>Session</dt>
            <dd>{portal.has_session ? `saved · ${relativeTime(portal.last_login)}` : "none"}</dd>
            <dt>Password</dt>
            <dd>{portal.has_credentials ? <Badge tone="go">stored encrypted</Badge> : "not stored"}</dd>
          </dl>
          <div className="portal-actions">
            <Button busy={login.isPending && login.variables?.method === "manual"}
                    onClick={() => login.mutate({ name, method: "manual" })}>
              <LogIn size={15} /> Sign in (browser opens)
            </Button>
            {canPassword && portal.has_credentials && (
              <Button variant="ghost" busy={login.isPending && login.variables?.method === "password"}
                      onClick={() => login.mutate({ name, method: "password" })}>
                Use saved password
              </Button>
            )}
            {canPassword && (
              <Button variant="ghost" onClick={() => setShowCreds(!showCreds)}>
                <KeyRound size={15} /> {portal.has_credentials ? "Change" : "Add"} password
              </Button>
            )}
            {portal.has_session && (
              <Button variant="ghost" busy={disconnect.isPending} onClick={() => disconnect.mutate(name)}>
                <Unplug size={15} /> Forget session
              </Button>
            )}
            <span className="spacer" />
            <Button variant="ghost" busy={toggle.isPending}
                    onClick={() => toggle.mutate({ name, enabled: false, accept_terms: false })}>
              Disable
            </Button>
          </div>
          {login.isPending && login.variables?.method === "manual" && (
            <p className="muted">A browser window opened. Sign in there, including any CAPTCHA or code; this waits up to 10 minutes.</p>
          )}
          {showCreds && (
            <form className="creds-form" onSubmit={saveCreds}>
              <Field label="Email or username">
                <input className="input" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required />
              </Field>
              <Field label="Password" hint="Encrypted in your local vault; never shown again or sent to a model.">
                <input className="input" type="password" autoComplete="current-password" value={password}
                       onChange={(e) => setPassword(e.target.value)} required />
              </Field>
              <Button variant="primary" type="submit" busy={creds.isPending}>Save</Button>
            </form>
          )}
        </>
      )}
      <ErrorNote error={toggle.error ?? login.error ?? creds.error ?? disconnect.error} />
    </article>
  );
}
