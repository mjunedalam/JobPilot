import { useHealth, useRoutes, useProviders } from "../api/queries";
import { Lamp } from "./ui";

/** Status strip modelled on an aircraft annunciator panel: each lamp is one system state. */
export function Annunciator() {
  const health = useHealth();
  const routes = useRoutes();
  const providers = useProviders();
  const h = health.data;

  const local = new Set((providers.data ?? []).filter((p) => p.is_local).map((p) => p.name));
  const roles = routes.data ?? [];
  const localRoles = roles.filter((r) => local.has(r.provider)).length;

  return (
    <div className="annunciator" role="status" aria-live="polite">
      <Lamp tone={health.isError ? "stop" : h ? "go" : "neutral"} label={health.isError ? "API offline" : "API"} />
      <Lamp
        tone={h?.kill_switch_engaged ? "stop" : "go"}
        label={h?.kill_switch_engaged ? "Halted" : "Agents armed"}
      />
      <Lamp
        tone={h?.llm_privacy_mode === "local_only" ? "go" : "amber"}
        label={h?.llm_privacy_mode === "local_only" ? "Local only" : "Standard privacy"}
      />
      {roles.length > 0 && (
        <Lamp
          tone={localRoles === roles.length ? "go" : "sky"}
          label={`${localRoles}/${roles.length} roles on local models`}
        />
      )}
      <Lamp tone={h?.tracing === "langfuse" ? "go" : "neutral"} label={`Tracing ${h?.tracing ?? "…"}`} />
    </div>
  );
}
