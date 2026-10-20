import { Lock, Globe2 } from "lucide-react";
import { useState } from "react";
import {
  useLLMSettings,
  usePresets,
  useProviders,
  useRoutes,
  useSetPrivacy,
  type Preset,
  type Provider,
} from "../api/queries";
import { ProviderCard } from "../components/models/ProviderCard";
import { ProviderForm } from "../components/models/ProviderForm";
import { RouteRow } from "../components/models/RouteRow";
import { Badge, ErrorNote, Panel } from "../components/ui";
import { ROLE_ORDER } from "../lib/roles";

type Editing = { preset?: Preset; existing?: Provider } | null;

export function Models() {
  const routes = useRoutes();
  const providers = useProviders();
  const presets = usePresets();
  const settings = useLLMSettings();
  const privacy = useSetPrivacy();
  const [editing, setEditing] = useState<Editing>(null);

  const providerList = providers.data ?? [];
  const sortedRoutes = [...(routes.data ?? [])].sort(
    (a, b) => ROLE_ORDER.indexOf(a.role) - ROLE_ORDER.indexOf(b.role),
  );
  const localOnly = settings.data?.privacy_mode === "local_only";

  return (
    <div className="page">
      <header className="page-head">
        <p className="eyebrow">Engines</p>
        <h1 className="page-title">Models</h1>
        <p className="page-lede">
          Pick any provider and model for each agent role. Open-source models on your machine are
          the default; cloud models are optional, and every change applies to the next call
          without a restart.
        </p>
      </header>

      <Panel className="privacy" eyebrow="Privacy" title={localOnly ? "Local only" : "Standard"}>
        <div className="segmented" role="radiogroup" aria-label="Privacy mode">
          <button
            role="radio"
            aria-checked={!localOnly}
            className={!localOnly ? "seg-on" : ""}
            onClick={() => privacy.mutate("standard")}
          >
            <Globe2 size={15} /> Standard
          </button>
          <button
            role="radio"
            aria-checked={localOnly}
            className={localOnly ? "seg-on" : ""}
            onClick={() => privacy.mutate("local_only")}
          >
            <Lock size={15} /> Local only
          </button>
        </div>
        <p className="muted">
          {localOnly
            ? "Prompts go only to providers on localhost or your private network. Cloud routes and fallbacks are skipped."
            : "Each role uses exactly the provider you choose below, local or cloud."}
        </p>
        <ErrorNote error={privacy.error} />
      </Panel>

      <section className="section" aria-labelledby="routes-title">
        <div className="section-head">
          <h2 id="routes-title" className="section-title">
            Role assignments
          </h2>
          <span className="muted">{sortedRoutes.length} roles</span>
        </div>
        <ErrorNote error={routes.error ?? providers.error} />
        <div className="route-grid">
          {providerList.length > 0 &&
            sortedRoutes.map((route) => (
              <RouteRow key={route.role} route={route} providers={providerList} />
            ))}
        </div>
      </section>

      <section className="section" aria-labelledby="providers-title">
        <div className="section-head">
          <h2 id="providers-title" className="section-title">
            Providers
          </h2>
          <span className="muted">{providerList.length} configured</span>
        </div>
        <div className="provider-grid">
          {providerList.map((p) => (
            <ProviderCard key={p.name} provider={p} onEdit={() => setEditing({ existing: p })} />
          ))}
        </div>

        {editing ? (
          <Panel
            eyebrow={editing.existing ? "Edit provider" : "Add provider"}
            title={editing.existing?.name ?? editing.preset?.label}
          >
            <ProviderForm
              key={editing.existing?.name ?? editing.preset?.id}
              preset={editing.preset}
              existing={editing.existing}
              vaultReady={Boolean(settings.data?.vault_ready)}
              onDone={() => setEditing(null)}
            />
          </Panel>
        ) : (
          <Panel eyebrow="Add provider" title="Connect another engine">
            <div className="preset-grid">
              {(presets.data ?? []).map((preset) => (
                <button key={preset.id} className="preset" onClick={() => setEditing({ preset })}>
                  <span className="preset-label">{preset.label}</span>
                  <span className="preset-badges">
                    {preset.is_local && <Badge tone="go">local</Badge>}
                    {preset.open_source && <Badge tone="neutral">open models</Badge>}
                  </span>
                  {preset.model_hint && <span className="preset-hint mono">{preset.model_hint}</span>}
                </button>
              ))}
              <button
                className="preset preset-custom"
                onClick={() =>
                  setEditing({
                    preset: {
                      id: "custom",
                      label: "Custom OpenAI-compatible",
                      type: "openai_compatible",
                      base_url: "http://",
                      api_key_env: null,
                      is_local: false,
                      open_source: false,
                      structured_mode: "json_object",
                      reasoning_effort: null,
                      model_hint: "",
                      docs_url: "",
                    },
                  })
                }
              >
                <span className="preset-label">Custom endpoint</span>
                <span className="preset-hint">Any OpenAI-compatible URL</span>
              </button>
            </div>
          </Panel>
        )}
      </section>
    </div>
  );
}
