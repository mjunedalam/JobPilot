import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useMemo, useSyncExternalStore, type ReactNode } from "react";
import { getToken } from "../../api/client";
import { flowKeys, jobKeys, keys, mailKeys } from "../../api/queries";
import { LiveStore, type LiveEvent, type LiveStatus } from "../../lib/live";
import { notifyDecision, updateBadge } from "../../lib/notify";

const LiveContext = createContext<LiveStore | null>(null);

/** Which cached data an event makes stale. Everything else keeps its slow fallback poll. */
export function invalidateFor(client: QueryClient, event: LiveEvent) {
  const d = event.data;
  const bust = (...queryKeys: readonly (readonly unknown[])[]) =>
    queryKeys.forEach((queryKey) => void client.invalidateQueries({ queryKey }));
  if (event.kind === "workflow") bust(flowKeys.workflows, flowKeys.decisions, flowKeys.applications);
  if (event.kind === "import") bust(["profile", "import", d.id]);
  if (event.kind !== "audit") return;
  bust(["activity"]);
  const action = String(d.action ?? "");
  if (action.startsWith("decision:")) bust(flowKeys.decisions, flowKeys.applications, jobKeys.jobs, mailKeys.allEmails);
  if (action.startsWith("attempt:")) bust(flowKeys.applications, jobKeys.jobs);
  if (action === "jobs_scored") bust(jobKeys.jobs);
  if (action.startsWith("email_") || action.startsWith("reply:")) bust(mailKeys.allEmails, mailKeys.status);
  if (action.startsWith("mail_")) bust(mailKeys.status);
  if (action.startsWith("profile_") || action === "resume_added") bust(keys.profile, keys.versions, keys.missing);
}

export function LiveProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const store = useMemo(() => new LiveStore(getToken), []);
  useEffect(() => {
    const off = store.onEvent((event) => {
      invalidateFor(client, event);
      if (event.kind === "workflow" && event.data.status === "waiting") {
        // A decision is waiting: refresh the count, then tell the user if they're elsewhere.
        void client.fetchQuery({ queryKey: flowKeys.decisions }).then((rows) => {
          const list = Array.isArray(rows) ? rows : [];
          updateBadge(list.length);
          const latest = list[list.length - 1] as { checkpoint?: string; payload?: Record<string, unknown> } | undefined;
          if (latest) notifyDecision(latest.checkpoint ?? "", latest.payload ?? {});
        });
      }
    });
    store.start();
    return () => {
      off();
      store.stop();
    };
  }, [store, client]);
  return <LiveContext.Provider value={store}>{children}</LiveContext.Provider>;
}

function useStore(): LiveStore | null {
  return useContext(LiveContext);
}

export function useLiveStatus(): LiveStatus {
  const store = useStore();
  useSyncExternalStore(store?.subscribe ?? noopSubscribe, store?.snapshot ?? zero);
  return store?.status ?? "offline";
}

/** Recent live events, newest last; optionally only some kinds. */
export function useLiveEvents(kinds?: string[]): LiveEvent[] {
  const store = useStore();
  useSyncExternalStore(store?.subscribe ?? noopSubscribe, store?.snapshot ?? zero);
  const events = store?.events ?? [];
  return kinds ? events.filter((e) => kinds.includes(e.kind)) : events;
}

/** Run a callback for every new event (for animations that react to activity). */
export function useOnLiveEvent(handler: (event: LiveEvent) => void) {
  const store = useStore();
  useEffect(() => (store ? store.onEvent(handler) : undefined), [store, handler]);
}

const noopSubscribe = () => () => undefined;
const zero = () => 0;
