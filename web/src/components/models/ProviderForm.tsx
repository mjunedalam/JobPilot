import { useState, type FormEvent } from "react";
import type { Schemas } from "../../api/client";
import { useSaveProvider, type Preset, type Provider } from "../../api/queries";
import { Button, ErrorNote, Field } from "../ui";

type Mode = Schemas["ProviderIn"]["structured_mode"];
type Effort = NonNullable<Schemas["ProviderIn"]["reasoning_effort"]>;

interface Props {
  preset?: Preset;
  existing?: Provider;
  vaultReady: boolean;
  onDone: () => void;
}

const MODES: { value: NonNullable<Mode>; label: string }[] = [
  { value: "json_object", label: "json_object: JSON mode + schema in prompt (best for local models)" },
  { value: "json_schema", label: "json_schema: native constrained schema" },
  { value: "prompt", label: "prompt: schema in prompt only (servers without JSON mode)" },
];

export function ProviderForm({ preset, existing, vaultReady, onDone }: Props) {
  const [name, setName] = useState(existing?.name ?? preset?.id ?? "");
  const [baseUrl, setBaseUrl] = useState(existing?.base_url ?? preset?.base_url ?? "");
  const [mode, setMode] = useState<NonNullable<Mode>>(
    (existing?.structured_mode as NonNullable<Mode>) ?? preset?.structured_mode ?? "json_object",
  );
  const [effort, setEffort] = useState<Effort | "">(
    ((existing?.reasoning_effort ?? preset?.reasoning_effort) as Effort | null) ?? "",
  );
  const [apiKey, setApiKey] = useState("");
  const [enabled, setEnabled] = useState(existing?.enabled ?? true);
  const save = useSaveProvider();
  const type = (existing?.type ?? preset?.type ?? "openai_compatible") as Schemas["ProviderIn"]["type"];
  const needsUrl = type === "openai_compatible";

  function submit(event: FormEvent) {
    event.preventDefault();
    save.mutate(
      {
        name: name.trim(),
        body: {
          preset: existing?.preset ?? preset?.id ?? "custom",
          type,
          base_url: baseUrl.trim(),
          structured_mode: mode,
          reasoning_effort: effort || null,
          enabled,
          api_key: apiKey.trim() || null,
          clear_api_key: false,
        },
      },
      { onSuccess: onDone },
    );
  }

  return (
    <form className="provider-form" onSubmit={submit}>
      <div className="form-grid">
        <Field label="Name" hint="Lowercase id used in routes, e.g. groq or lan-vllm">
          <input
            className="input mono"
            value={name}
            onChange={(e) => setName(e.target.value)}
            pattern="[a-z0-9][a-z0-9_\-]{0,63}"
            required
            readOnly={Boolean(existing)}
          />
        </Field>
        {needsUrl && (
          <Field label="Base URL" hint="OpenAI-compatible endpoint, usually ending in /v1">
            <input className="input mono" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} required />
          </Field>
        )}
        <Field
          label="API key"
          hint={
            !vaultReady
              ? "Set JOBPILOT_MASTER_KEY to store keys; or use the provider's env var"
              : existing?.has_api_key
                ? "A key is stored. Leave blank to keep it."
                : preset?.api_key_env
                  ? `Stored encrypted. Or set ${preset.api_key_env} in .env`
                  : "Not needed for local servers"
          }
        >
          <input
            className="input mono"
            type="password"
            autoComplete="off"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            disabled={!vaultReady}
          />
        </Field>
        {type !== "anthropic" && (
          <Field label="Structured output">
            <select className="input" value={mode} onChange={(e) => setMode(e.target.value as NonNullable<Mode>)}>
              {MODES.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </select>
          </Field>
        )}
        {type === "openai_compatible" && (
          <Field
            label="Reasoning effort"
            hint="For thinking models like qwen3. 'none' was ~10x faster for resume extraction."
          >
            <select className="input" value={effort} onChange={(e) => setEffort(e.target.value as Effort | "")}>
              <option value="">Server default</option>
              <option value="none">none: skip thinking (fastest)</option>
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
            </select>
          </Field>
        )}
      </div>
      <label className="check-inline">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> Enabled
      </label>
      <ErrorNote error={save.error} />
      <div className="form-actions">
        <Button variant="ghost" onClick={onDone}>
          Cancel
        </Button>
        <Button variant="primary" type="submit" busy={save.isPending}>
          Save provider
        </Button>
      </div>
    </form>
  );
}
