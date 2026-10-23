/** Live connection to the server's event stream (GET /api/v1/events, Server-Sent Events).
 *  Read with fetch() so the bearer token goes in a header; reconnects with backoff and asks
 *  for everything after the last event id it saw, so nothing is missed. */

export interface LiveEvent {
  id: number;
  ts: number;
  kind: "audit" | "workflow" | "agent" | "tool" | "llm" | "import" | string;
  data: Record<string, unknown>;
}

export type LiveStatus = "connecting" | "live" | "offline";

type Listener = () => void;
type EventHandler = (event: LiveEvent) => void;

const KEEP = 300;

export class LiveStore {
  status: LiveStatus = "connecting";
  events: LiveEvent[] = [];
  private lastId = 0;
  private listeners = new Set<Listener>();
  private handlers = new Set<EventHandler>();
  private abort: AbortController | null = null;
  private stopped = false;
  private backoff = 1000;

  constructor(private readonly token: () => string | null, private readonly url = "/api/v1/events") {}

  subscribe = (listener: Listener) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  onEvent(handler: EventHandler): () => void {
    this.handlers.add(handler);
    return () => {
      this.handlers.delete(handler);
    };
  }

  snapshot = () => this.version;
  private version = 0;

  private emitChange() {
    this.version += 1;
    this.listeners.forEach((l) => l());
  }

  private setStatus(status: LiveStatus) {
    if (this.status !== status) {
      this.status = status;
      this.emitChange();
    }
  }

  start() {
    this.stopped = false;
    void this.loop();
  }

  stop() {
    this.stopped = true;
    this.abort?.abort();
  }

  private async loop() {
    while (!this.stopped) {
      try {
        await this.connect();
        this.backoff = 1000;
      } catch {
        /* network error or server restart: retry below */
      }
      if (this.stopped) return;
      this.setStatus("offline");
      await new Promise((r) => setTimeout(r, this.backoff));
      this.backoff = Math.min(this.backoff * 2, 15_000);
    }
  }

  private async connect() {
    this.abort = new AbortController();
    this.setStatus("connecting");
    const token = this.token();
    const response = await fetch(`${this.url}?since=${this.lastId}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      signal: this.abort.signal,
    });
    if (!response.ok || !response.body) throw new Error(`stream ${response.status}`);
    this.setStatus("live");
    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) return;
      buffer += value;
      let cut;
      while ((cut = buffer.indexOf("\n\n")) >= 0) {
        this.frame(buffer.slice(0, cut));
        buffer = buffer.slice(cut + 2);
      }
    }
  }

  /** One SSE frame: only `data:` lines matter (each carries the whole event as JSON). */
  frame(raw: string) {
    const data = raw
      .split("\n")
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart())
      .join("\n");
    if (!data) return;
    let event: LiveEvent;
    try {
      event = JSON.parse(data) as LiveEvent;
    } catch {
      return;
    }
    if (event.id <= this.lastId) return;
    this.lastId = event.id;
    this.events = [...this.events.slice(-(KEEP - 1)), event];
    this.handlers.forEach((h) => h(event));
    this.emitChange();
  }
}
