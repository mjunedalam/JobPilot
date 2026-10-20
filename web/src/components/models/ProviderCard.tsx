import { KeyRound, PlugZap, Trash2 } from "lucide-react";
import { useDeleteProvider, useTestProvider, type Provider } from "../../api/queries";
import { Badge, Button, ErrorNote } from "../ui";

export function ProviderCard({ provider, onEdit }: { provider: Provider; onEdit: () => void }) {
  const test = useTestProvider();
  const remove = useDeleteProvider();
  const result = test.data;

  return (
    <article className={`provider ${provider.enabled ? "" : "provider-off"}`}>
      <header className="provider-head">
        <h3 className="provider-name mono">{provider.name}</h3>
        <Badge tone={provider.is_local ? "go" : "sky"}>{provider.is_local ? "local" : "cloud"}</Badge>
        {provider.has_api_key && (
          <span className="key-chip" title="API key stored encrypted">
            <KeyRound size={13} /> key
          </span>
        )}
      </header>
      <dl className="provider-meta">
        <dt>Type</dt>
        <dd>{provider.type}</dd>
        {provider.base_url && (
          <>
            <dt>Endpoint</dt>
            <dd className="mono truncate" title={provider.base_url}>
              {provider.base_url}
            </dd>
          </>
        )}
        <dt>JSON mode</dt>
        <dd className="mono">{provider.structured_mode}</dd>
        {provider.reasoning_effort && (
          <>
            <dt>Thinking</dt>
            <dd className="mono">{provider.reasoning_effort}</dd>
          </>
        )}
        <dt>Source</dt>
        <dd>{provider.source === "config" ? "config.yaml" : "added here"}</dd>
      </dl>
      {result && (
        <p className={`trial ${result.ok ? "trial-ok" : "trial-fail"}`} role="status">
          {result.ok
            ? `Reachable · ${result.models_found} models · ${result.latency_ms} ms`
            : result.error}
        </p>
      )}
      <ErrorNote error={test.error ?? remove.error} />
      <footer className="provider-actions">
        <Button variant="ghost" busy={test.isPending} onClick={() => test.mutate(provider.name)}>
          <PlugZap size={15} /> Test
        </Button>
        <Button variant="ghost" onClick={onEdit}>
          Edit
        </Button>
        {provider.source === "user" && (
          <Button
            variant="ghost"
            busy={remove.isPending}
            aria-label={`Delete ${provider.name}`}
            onClick={() => {
              if (window.confirm(`Remove provider "${provider.name}"?`)) remove.mutate(provider.name);
            }}
          >
            <Trash2 size={15} />
          </Button>
        )}
      </footer>
    </article>
  );
}
