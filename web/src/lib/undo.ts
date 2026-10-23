/** A short undo window before an answer that submits or sends leaves the page.
 *  The answer is held for `UNDO_MS`; Undo cancels it before anything reaches the server. */

export const UNDO_MS = 5000;

export interface Pending {
  id: number;
  label: string;
  startedAt: number;
  ms: number;
}

export class Cancelled extends Error {
  constructor() {
    super("undone");
    this.name = "Cancelled";
  }
}

type Listener = () => void;

class UndoStore {
  private items: Pending[] = [];
  private timers = new Map<number, { timer: number; reject: (e: Error) => void }>();
  private listeners = new Set<Listener>();
  private seq = 0;
  private version = 0;
  /** Length of the window; tests set it to 0. */
  windowMs = UNDO_MS;

  subscribe = (l: Listener) => {
    this.listeners.add(l);
    return () => {
      this.listeners.delete(l);
    };
  };

  snapshot = () => this.version;

  get pending(): Pending[] {
    return this.items;
  }

  private changed() {
    this.version += 1;
    this.listeners.forEach((l) => l());
  }

  /** Resolves after the window, or rejects with Cancelled if undone. */
  hold(label: string, ms = this.windowMs): Promise<void> {
    const id = ++this.seq;
    this.items = [...this.items, { id, label, startedAt: Date.now(), ms }];
    this.changed();
    return new Promise<void>((resolve, reject) => {
      const timer = window.setTimeout(() => {
        this.drop(id);
        resolve();
      }, ms);
      this.timers.set(id, { timer, reject });
    });
  }

  undo(id?: number) {
    const target = id ?? this.items[this.items.length - 1]?.id;
    if (target == null) return;
    const t = this.timers.get(target);
    if (t) {
      window.clearTimeout(t.timer);
      t.reject(new Cancelled());
    }
    this.drop(target);
  }

  private drop(id: number) {
    this.timers.delete(id);
    this.items = this.items.filter((i) => i.id !== id);
    this.changed();
  }
}

export const undoStore = new UndoStore();

/** Answers that commit to something outside JobPilot (submit, send, apply) get the window. */
export function undoLabel(checkpoint: string, answer: Record<string, unknown>): string | null {
  const action = answer.action;
  if (checkpoint === "H3" && action === "approve") return "Approving jobs for applying";
  if (checkpoint === "H5" && action === "submit") return "Submitting the application";
  if (checkpoint === "H7" && action === "approve") return "Sending the reply";
  return null;
}
