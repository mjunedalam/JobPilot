import { CornerDownLeft, Search } from "lucide-react";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { useDecisions, useHealth, useKillSwitch, usePollMail, useRunAutopilot } from "../api/queries";
import { rankCommands, type Command } from "../lib/commands";
import { describeDecision } from "../lib/notify";

const PAGES: [string, string, string][] = [
  ["/", "Mission control", "home pipeline autopilot"],
  ["/approvals", "Decisions", "approvals queue review gate"],
  ["/live", "Live ops", "x-ray agency simulation agents"],
  ["/jobs", "Jobs", "search postings find"],
  ["/inbox", "Inbox", "email mail replies"],
  ["/profile", "Profile", "cv resume skills"],
  ["/answers", "Answer bank", "questions forms"],
  ["/setup", "Pre-flight", "setup checklist"],
  ["/models", "Models", "llm providers ollama claude"],
  ["/portals", "Portals", "linkedin indeed sign in"],
  ["/system", "System", "kill switch health"],
];

const CLOSE_AFTER_MS = 900;

function message(error: unknown): string {
  return error instanceof Error ? error.message : "That did not work.";
}

/** Commands from what the app knows right now: pages, waiting decisions and actions. */
function useCommands(onTheme: () => void): Command[] {
  const navigate = useNavigate();
  const decisions = useDecisions(false).data ?? [];
  const halted = Boolean(useHealth().data?.kill_switch_engaged);
  const poll = usePollMail();
  const autopilot = useRunAutopilot();
  const kill = useKillSwitch();

  return useMemo(() => {
    const go = (to: string) => () => navigate(to);
    return [
      ...decisions.map((d): Command => {
        const { title, body } = describeDecision(d.checkpoint, d.payload as Record<string, unknown>);
        return {
        id: `decision-${d.interrupt_id}`,
        group: "Decisions",
        title,
        hint: `${d.checkpoint} · ${body}`,
        keywords: "approve review decide",
        run: go(`/approvals?focus=${encodeURIComponent(d.interrupt_id)}`),
        };
      }),
      { id: "mail", group: "Actions", title: "Check mail now", keywords: "poll inbox email", run: () => poll.mutateAsync() },
      { id: "autopilot", group: "Actions", title: "Run Autopilot now", hint: "searches and ranks; never approves", keywords: "search schedule", run: () => autopilot.mutateAsync() },
      { id: "find", group: "Actions", title: "Find jobs", keywords: "search postings", run: go("/jobs") },
      { id: "xray", group: "Actions", title: "Watch the agents", hint: "Live ops", keywords: "x-ray agency simulation", run: go("/live") },
      { id: "theme", group: "Actions", title: "Switch theme", keywords: "dark light mode", run: onTheme },
      halted
        ? { id: "resume", group: "Actions", title: "Resume the agents", keywords: "kill switch start", run: () => kill.mutateAsync(false) }
        : { id: "stop", group: "Actions", title: "Stop everything", hint: "kill switch", keywords: "halt emergency", tone: "danger", run: () => kill.mutateAsync(true) },
      ...PAGES.map(([to, title, keywords]): Command => ({ id: `page-${to}`, group: "Go to", title, keywords, run: go(to) })),
    ];
  }, [decisions, halted, navigate, onTheme, poll, autopilot, kill]);
}

export function CommandBar({ open, onClose, onTheme }: { open: boolean; onClose: () => void; onTheme: () => void }) {
  const commands = useCommands(onTheme);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [armed, setArmed] = useState<string | null>(null);
  const [status, setStatus] = useState<{ text: string; tone: "busy" | "ok" | "error" } | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const restore = useRef<HTMLElement | null>(null);
  const listId = useId();
  const results = useMemo(() => rankCommands(commands, query), [commands, query]);
  const current = results[Math.min(active, results.length - 1)];

  useEffect(() => {
    if (!open) return;
    restore.current = document.activeElement as HTMLElement | null;
    setQuery("");
    setActive(0);
    setArmed(null);
    setStatus(null);
    input.current?.focus();
    return () => restore.current?.focus?.();
  }, [open]);

  useEffect(() => setActive(0), [query]);

  const run = useCallback(async (c: Command) => {
    if (c.tone === "danger" && armed !== c.id) {
      setArmed(c.id);
      setStatus({ text: `Press Enter again to ${c.title.toLowerCase()}.`, tone: "error" });
      return;
    }
    const result = c.run();
    if (!(result instanceof Promise)) return onClose();
    setStatus({ text: `${c.title}…`, tone: "busy" });
    try {
      await result;
      setStatus({ text: `${c.title}: done`, tone: "ok" });
      setTimeout(onClose, CLOSE_AFTER_MS);
    } catch (error) {
      setStatus({ text: message(error), tone: "error" });
    }
    setArmed(null);
  }, [armed, onClose]);

  if (!open) return null;

  function onKey(event: React.KeyboardEvent) {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((i) => (results.length ? (i + step + results.length) % results.length : 0));
      setArmed(null);
    } else if (event.key === "Enter" && current) {
      event.preventDefault();
      void run(current);
    } else if (event.key === "Tab") {
      event.preventDefault(); // focus stays in the dialog
    }
  }

  let lastGroup = "";
  return (
    <div className="cmd-layer" onKeyDown={onKey}>
      <div className="cmd-scrim" onClick={onClose} aria-hidden />
      <div className="cmd" role="dialog" aria-modal="true" aria-label="Command bar">
        <div className="cmd-input">
          <Search size={17} aria-hidden />
          <input
            ref={input}
            role="combobox"
            aria-expanded="true"
            aria-controls={listId}
            aria-activedescendant={current ? `${listId}-${current.id}` : undefined}
            aria-autocomplete="list"
            placeholder="Type a command, a page or a decision…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <kbd>esc</kbd>
        </div>
        <ul id={listId} role="listbox" className="cmd-list" aria-label="Commands">
          {results.map((c) => {
            const header = c.group !== lastGroup ? c.group : null;
            lastGroup = c.group;
            const on = c === current;
            return (
              <li key={c.id} role="presentation">
                {header && <span className="cmd-group" aria-hidden>{header}</span>}
                <div
                  id={`${listId}-${c.id}`}
                  role="option"
                  aria-selected={on}
                  className={`cmd-item ${on ? "cmd-item-on" : ""} ${c.tone === "danger" ? "cmd-danger" : ""} ${armed === c.id ? "cmd-armed" : ""}`}
                  onMouseMove={() => setActive(results.indexOf(c))}
                  onClick={() => void run(c)}
                >
                  <span className="cmd-title">{c.title}</span>
                  {c.hint && <span className="cmd-hint">{c.hint}</span>}
                  {on && <CornerDownLeft size={14} aria-hidden className="cmd-enter" />}
                </div>
              </li>
            );
          })}
          {results.length === 0 && <li className="cmd-empty">Nothing matches “{query}”.</li>}
        </ul>
        <p className={`cmd-status ${status ? `cmd-status-${status.tone}` : ""}`} role="status">
          {status?.text ?? "↑↓ to move · Enter to run · ⌘K anywhere"}
        </p>
      </div>
    </div>
  );
}

/** ⌘K / Ctrl+K toggles the bar from anywhere. */
export function useCommandHotkey(toggle: () => void) {
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        toggle();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggle]);
}
