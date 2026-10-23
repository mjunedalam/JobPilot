/** Desktop notifications and the tab badge for decisions that wait for the user.
 *  Shown only when the tab is in the background; the Decision Center shows the rest. */

const TITLE = "JobPilot";
const ENABLED_KEY = "jobpilot.desktopNotify";

const LABELS: Record<string, string> = {
  H1: "A portal needs you to sign in",
  H3: "Jobs are ready for your approval",
  H4: "A form question needs your answer",
  H5: "An application is ready to submit",
  H6: "An email needs you",
  H7: "A reply is ready to send",
};

export function notificationsSupported(): boolean {
  return typeof window !== "undefined" && "Notification" in window;
}

export function desktopEnabled(): boolean {
  try {
    return notificationsSupported() && Notification.permission === "granted" && localStorage.getItem(ENABLED_KEY) !== "off";
  } catch {
    return false;
  }
}

export async function enableDesktop(): Promise<boolean> {
  if (!notificationsSupported()) return false;
  const permission = Notification.permission === "granted" ? "granted" : await Notification.requestPermission();
  try {
    localStorage.setItem(ENABLED_KEY, permission === "granted" ? "on" : "off");
  } catch {
    /* preference not remembered; permission still applies */
  }
  return permission === "granted";
}

export function disableDesktop() {
  try {
    localStorage.setItem(ENABLED_KEY, "off");
  } catch {
    /* nothing to undo */
  }
}

export function describeDecision(checkpoint: string, payload: Record<string, unknown>): { title: string; body: string } {
  const title = LABELS[checkpoint] ?? "A decision is waiting";
  const job = payload.job as { title?: string; company?: string } | undefined;
  const email = payload.email as { subject?: string; sender?: string } | undefined;
  const jobs = payload.jobs as unknown[] | undefined;
  let body = "Open JobPilot to decide.";
  if (checkpoint === "H3" && jobs) body = `${jobs.length} ranked jobs to review.`;
  else if (job?.title) body = `${job.title}${job.company ? ` at ${job.company}` : ""}`;
  else if (email?.subject) body = email.subject;
  return { title, body };
}

export function notifyDecision(checkpoint: string, payload: Record<string, unknown>) {
  if (!desktopEnabled() || !document.hidden) return;
  const { title, body } = describeDecision(checkpoint, payload);
  try {
    const n = new Notification(title, { body, tag: `jobpilot-${checkpoint}`, silent: false });
    n.onclick = () => {
      window.focus();
      window.location.assign("/approvals");
      n.close();
    };
  } catch {
    /* some browsers only allow notifications from a service worker; the badge still shows */
  }
}

/** "(3) JobPilot" in the tab title while decisions wait. */
export function updateBadge(count: number) {
  const base = document.title.replace(/^\(\d+\)\s*/, "") || TITLE;
  document.title = count > 0 ? `(${count}) ${base}` : base;
}
