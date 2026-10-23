import { useEffect, useState, useSyncExternalStore } from "react";
import { undoStore } from "../../lib/undo";

/** "Sending the reply · 4 s · Undo": shown while an approval waits out its undo window. */
export function UndoToast() {
  useSyncExternalStore(undoStore.subscribe, undoStore.snapshot);
  const [now, setNow] = useState(Date.now());
  const items = undoStore.pending;
  useEffect(() => {
    if (!items.length) return;
    const t = window.setInterval(() => setNow(Date.now()), 100);
    return () => window.clearInterval(t);
  }, [items.length]);
  if (!items.length) return null;
  return (
    <div className="undo-stack" aria-live="assertive">
      {items.map((i) => {
        const left = Math.max(0, i.ms - (now - i.startedAt));
        const C = 2 * Math.PI * 11;
        return (
          <div key={i.id} className="undo-toast" role="status">
            <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden="true">
              <circle cx="14" cy="14" r="11" fill="none" className="undo-track" strokeWidth="3" />
              <circle cx="14" cy="14" r="11" fill="none" className="undo-ring" strokeWidth="3" strokeLinecap="round"
                      strokeDasharray={`${(C * left) / i.ms} ${C}`} transform="rotate(-90 14 14)" />
            </svg>
            <span className="undo-text">{i.label} · {Math.ceil(left / 1000)} s</span>
            <button type="button" className="undo-btn" onClick={() => undoStore.undo(i.id)}>Undo</button>
          </div>
        );
      })}
    </div>
  );
}
