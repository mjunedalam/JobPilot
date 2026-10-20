import type { Role } from "../api/queries";

export const ROLE_INFO: Record<Role, { title: string; purpose: string }> = {
  reasoning: { title: "Reasoning", purpose: "Filling application forms, unknown portals" },
  extraction: { title: "Extraction", purpose: "Reading resumes into your profile" },
  writing: { title: "Writing", purpose: "Drafting email replies" },
  fast: { title: "Fast", purpose: "Email triage, match reasons, quick checks" },
  router: { title: "Router", purpose: "Understanding chat requests" },
  judge: { title: "Judge", purpose: "Evaluations and second opinions" },
};

export const ROLE_ORDER: Role[] = ["reasoning", "extraction", "writing", "fast", "router", "judge"];
