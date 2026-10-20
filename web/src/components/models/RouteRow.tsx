import { Plane, RotateCcw, Plus, Trash2 } from "lucide-react";
import { useEffect, useId, useState } from "react";
import type { Schemas } from "../../api/client";
import {
  useProviderModels,
  useResetRoute,
  useSaveRoute,
  useTryRole,
  type Provider,
  type Route,
} from "../../api/queries";
import { ROLE_INFO } from "../../lib/roles";
import { Badge, Button, ErrorNote } from "../ui";

type Target = Schemas["ModelTarget"];

interface TargetPickerProps {
  value: Target;
  providers: Provider[];
  onChange: (value: Target) => void;
  label: string;
}

/** Provider select + model combobox. The model list is fetched live from the provider,
 *  but any model id can be typed (new releases, fine-tunes, LiteLLM ids). */
export function TargetPicker({ value, providers, onChange, label }: TargetPickerProps) {
  const listId = useId();
  const models = useProviderModels(value.provider);
  const provider = providers.find((p) => p.name === value.provider);
  return (
    <div className="target">
      <select
        className="input"
        aria-label={`${label} provider`}
        value={value.provider}
        onChange={(e) => onChange({ provider: e.target.value, model: "" })}
      >
        {providers.map((p) => (
          <option key={p.name} value={p.name} disabled={!p.enabled}>
            {p.name}
            {p.is_local ? " · local" : ""}
          </option>
        ))}
      </select>
      <input
        className="input mono"
        aria-label={`${label} model`}
        list={listId}
        value={value.model}
        placeholder={models.isLoading ? "loading models…" : "model id"}
        onChange={(e) => onChange({ ...value, model: e.target.value })}
      />
      <datalist id={listId}>
        {(models.data ?? []).map((m) => (
          <option key={m} value={m} />
        ))}
      </datalist>
      {provider && <Badge tone={provider.is_local ? "go" : "sky"}>{provider.is_local ? "local" : "cloud"}</Badge>}
      {models.isError && <span className="field-hint tone-amber">models unavailable, type an id</span>}
    </div>
  );
}

export function RouteRow({ route, providers }: { route: Route; providers: Provider[] }) {
  const [primary, setPrimary] = useState<Target>({ provider: route.provider, model: route.model });
  const [fallbacks, setFallbacks] = useState<Target[]>(route.fallbacks);
  const save = useSaveRoute();
  const reset = useResetRoute();
  const trial = useTryRole();

  useEffect(() => {
    setPrimary({ provider: route.provider, model: route.model });
    setFallbacks(route.fallbacks);
  }, [route]);

  const dirty =
    primary.provider !== route.provider ||
    primary.model !== route.model ||
    JSON.stringify(fallbacks) !== JSON.stringify(route.fallbacks);
  const valid = primary.model.trim() !== "" && fallbacks.every((f) => f.model.trim() !== "");
  const info = ROLE_INFO[route.role];

  return (
    <article className="route">
      <header className="route-head">
        <div>
          <h3 className="route-role">{info.title}</h3>
          <p className="muted route-purpose">{info.purpose}</p>
        </div>
        <Badge tone={route.source === "user" ? "amber" : "neutral"}>
          {route.source === "user" ? "custom" : "default"}
        </Badge>
      </header>

      <TargetPicker label={`${info.title} primary`} value={primary} providers={providers} onChange={setPrimary} />

      {fallbacks.map((fb, index) => (
        <div className="fallback" key={index}>
          <span className="eyebrow">Fallback {index + 1}</span>
          <TargetPicker
            label={`${info.title} fallback ${index + 1}`}
            value={fb}
            providers={providers}
            onChange={(next) => setFallbacks(fallbacks.map((f, i) => (i === index ? next : f)))}
          />
          <button
            className="icon-btn"
            aria-label={`Remove fallback ${index + 1}`}
            onClick={() => setFallbacks(fallbacks.filter((_, i) => i !== index))}
          >
            <Trash2 size={15} />
          </button>
        </div>
      ))}

      <footer className="route-actions">
        {fallbacks.length < 2 && (
          <Button
            variant="ghost"
            onClick={() =>
              setFallbacks([...fallbacks, { provider: providers[0]?.name ?? "", model: "" }])
            }
          >
            <Plus size={15} /> Fallback
          </Button>
        )}
        <span className="spacer" />
        {route.source === "user" && (
          <Button variant="ghost" busy={reset.isPending} onClick={() => reset.mutate(route.role)}>
            <RotateCcw size={15} /> Default
          </Button>
        )}
        <Button busy={trial.isPending} onClick={() => trial.mutate(route.role)} disabled={dirty}>
          <Plane size={15} /> Test flight
        </Button>
        <Button
          variant="primary"
          disabled={!dirty || !valid}
          busy={save.isPending}
          onClick={() => save.mutate({ role: route.role, body: { ...primary, fallbacks } })}
        >
          Save
        </Button>
      </footer>

      <ErrorNote error={save.error ?? reset.error} />
      {trial.data && (
        <p className={`trial ${trial.data.ok ? "trial-ok" : "trial-fail"}`} role="status">
          {trial.data.ok ? (
            <>
              <span className="mono">
                {trial.data.provider}/{trial.data.model} · {(trial.data.latency_ms / 1000).toFixed(1)}s ·{" "}
                {trial.data.output_tokens} tok
              </span>
              <span>“{trial.data.reply}”</span>
            </>
          ) : (
            <span>{trial.data.error}</span>
          )}
        </p>
      )}
    </article>
  );
}
