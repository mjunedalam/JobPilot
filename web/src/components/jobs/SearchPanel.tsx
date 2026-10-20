import { Globe, Plus, Search, X } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { usePortals, useRunSearch, type RecencyPreset } from "../../api/queries";
import { Button, ErrorNote, Panel } from "../ui";
import { ReportStrip } from "./ReportStrip";

const PRESETS: { value: RecencyPreset; label: string }[] = [
  { value: "1h", label: "Last hour" },
  { value: "24h", label: "24 hours" },
  { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" },
  { value: "custom", label: "Custom" },
];

function useElapsed(running: boolean): number {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    if (!running) return;
    setSeconds(0);
    const started = Date.now();
    const timer = window.setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 500);
    return () => window.clearInterval(timer);
  }, [running]);
  return seconds;
}

/** Checkpoint H2: the user chooses the recency window before every search. */
export function SearchPanel() {
  const portals = usePortals();
  const search = useRunSearch();
  const elapsed = useElapsed(search.isPending);
  const [preset, setPreset] = useState<RecencyPreset>("7d");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [urls, setUrls] = useState<string[]>([]);
  const [draftUrl, setDraftUrl] = useState("");
  const [includeUndated, setIncludeUndated] = useState(false);

  const enabled = (portals.data ?? []).filter((p) => p.enabled);
  const sources = [...selected.filter((s) => enabled.some((p) => p.name === s)), ...urls];

  function addUrl() {
    const url = draftUrl.trim();
    if (/^https?:\/\/\S+\.\S+/.test(url) && !urls.includes(url)) setUrls([...urls, url]);
    setDraftUrl("");
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    search.mutate({
      recency: preset === "custom"
        ? { preset, since: since ? new Date(since).toISOString() : null, until: until ? new Date(until).toISOString() : null }
        : { preset },
      sources,
      include_undated: includeUndated,
    });
  }

  return (
    <Panel eyebrow="Flight plan · H2" title="Search recent jobs" className="search-panel">
      <form onSubmit={submit} className="stack">
        <div>
          <p className="field-label">Posted within</p>
          <div className="segmented" role="radiogroup" aria-label="Recency window">
            {PRESETS.map((p) => (
              <button type="button" key={p.value} role="radio" aria-checked={preset === p.value}
                      className={preset === p.value ? "seg-on" : ""} onClick={() => setPreset(p.value)}>
                {p.label}
              </button>
            ))}
          </div>
          {preset === "custom" && (
            <div className="custom-range">
              <label className="field"><span className="field-label">From</span>
                <input className="input" type="datetime-local" value={since} onChange={(e) => setSince(e.target.value)} required />
              </label>
              <label className="field"><span className="field-label">To</span>
                <input className="input" type="datetime-local" value={until} onChange={(e) => setUntil(e.target.value)} />
              </label>
            </div>
          )}
        </div>

        <div>
          <p className="field-label">Sources</p>
          <div className="source-chips">
            {enabled.map((p) => {
              const on = selected.includes(p.name);
              return (
                <button type="button" key={p.name} aria-pressed={on} className={`source-chip ${on ? "on" : ""}`}
                        onClick={() => setSelected(on ? selected.filter((s) => s !== p.name) : [...selected, p.name])}>
                  {p.label}
                  {p.status !== "connected" && <span className="source-warn" title={p.status_reason || p.status}>•</span>}
                </button>
              );
            })}
            {urls.map((u) => (
              <span key={u} className="source-chip on url-chip">
                <Globe size={13} /> {new URL(u).host}
                <button type="button" aria-label={`Remove ${u}`} onClick={() => setUrls(urls.filter((x) => x !== u))}>
                  <X size={12} />
                </button>
              </span>
            ))}
          </div>
          {enabled.length === 0 && (
            <p className="field-hint">No portals enabled yet. Enable one under Portals, or add any careers page URL.</p>
          )}
          <div className="url-add">
            <input className="input" placeholder="https://company.com/careers" value={draftUrl}
                   onChange={(e) => setDraftUrl(e.target.value)}
                   onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addUrl(); } }} />
            <Button variant="ghost" onClick={addUrl}><Plus size={15} /> Add page</Button>
          </div>
        </div>

        <label className="check-inline">
          <input type="checkbox" checked={includeUndated} onChange={(e) => setIncludeUndated(e.target.checked)} />
          Include listings without a posted date
        </label>

        <div className="form-actions search-actions">
          {search.isPending && <span className="muted">Pages load at a human pace… {elapsed}s</span>}
          <Button variant="primary" type="submit" busy={search.isPending} disabled={sources.length === 0}>
            <Search size={15} /> Search
          </Button>
        </div>
        <ErrorNote error={search.error} />
      </form>
      {search.data && <ReportStrip report={search.data} />}
    </Panel>
  );
}
