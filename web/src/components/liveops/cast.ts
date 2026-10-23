/** The cast of Live ops: the nine agents, the outside world, and where each sits.
 *  Shared by the X-ray (anatomy) and the Agency (people at work) views. */

export type NodeKey =
  | "core" | "profile" | "search" | "portal" | "matching" | "application" | "analytics" | "email" | "reply"
  | "models" | "portals" | "chrome" | "db" | "gmail" | "you";

export interface CastMember {
  label: string;
  kind: "core" | "agent" | "ext" | "you";
  /** X-ray: angle on its ring (degrees) and outline shape. */
  ang: number;
  shape: string;
  role: string;
  organs?: string;
  /** Agency: who they are on the office floor. */
  person?: { title: string; seat: [number, number]; front: [number, number]; row: "top" | "bottom" | "table" | "office";
             shirt: string; skin: string; hair: string; prop?: string };
  place?: { label: string; sub: string; front: [number, number]; dwell: number };
}

export const LANE = 430;

export const CAST: Record<NodeKey, CastMember> = {
  core: { label: "Orchestrator", kind: "core", ang: 0, shape: "hexagon", role: "Routes every request and pauses at your checkpoints", organs: "router · LangGraph · checkpointer",
          person: { title: "the Ops Lead", seat: [500, 322], front: [500, 404], row: "table", shirt: "#2B4BEE", skin: "#E1B48D", hair: "#5A3A22" } },
  profile: { label: "Profile", kind: "agent", ang: -90, shape: "idcard", role: "Reads your CV, keeps your profile and Answer Bank", organs: "parser · extractor · Q&A",
             person: { title: "the Archivist", seat: [322, 150], front: [322, 260], row: "top", shirt: "#8E5CC2", skin: "#B57A52", hair: "#1E1611", prop: "cabinet" } },
  search: { label: "Job Search", kind: "agent", ang: -45, shape: "magnifier", role: "Searches portals and careers pages in your window (H2)", organs: "adapters · dedup · filters",
            person: { title: "the Scout", seat: [462, 150], front: [462, 260], row: "top", shirt: "#E07A2F", skin: "#EAC4A0", hair: "#A0522D", prop: "monitor" } },
  portal: { label: "Portal Connector & Auth", kind: "agent", ang: 0, shape: "shield", role: "Signs in with saved sessions; stops for CAPTCHA or 2FA (H1)", organs: "vault · sessions · OTP",
            person: { title: "the Gatekeeper", seat: [760, 150], front: [760, 260], row: "top", shirt: "#3B3F46", skin: "#D9A27A", hair: "#2A2320", prop: "keys" } },
  matching: { label: "Matching & Ranking", kind: "agent", ang: 45, shape: "podium", role: "Scores jobs against your profile and flags scams", organs: "scorer · red flags · RAG",
              person: { title: "the Analyst", seat: [602, 150], front: [602, 260], row: "top", shirt: "#2F8F6B", skin: "#8D5A3B", hair: "#141010", prop: "scales" } },
  application: { label: "Application", kind: "agent", ang: 90, shape: "form", role: "Fills the form in real Chrome; submits only with your approval (H5)", organs: "form filler · answerer · proof",
                 person: { title: "the Clerk", seat: [682, 650], front: [682, 540], row: "bottom", shirt: "#C2445A", skin: "#E3B08A", hair: "#4A2C1A", prop: "laptop" } },
  analytics: { label: "Analytics", kind: "agent", ang: 135, shape: "chart", role: "Counts what happened; numbers match the raw records", organs: "funnel · rates · ranges",
               person: { title: "the Accountant", seat: [532, 650], front: [532, 540], row: "bottom", shirt: "#6B7D2F", skin: "#A86F48", hair: "#1A1411", prop: "chart" } },
  email: { label: "Email Monitor", kind: "agent", ang: 180, shape: "envelope", role: "Classifies job mail, links it to applications, raises H6", organs: "triage · linker · DMARC",
           person: { title: "the Mail Sorter", seat: [232, 650], front: [232, 540], row: "bottom", shirt: "#B08A2E", skin: "#C48A60", hair: "#3A2517", prop: "pigeonholes" } },
  reply: { label: "Reply Drafting", kind: "agent", ang: 225, shape: "bubble", role: "Drafts replies with free slots; sends only approved text (H7)", organs: "drafter · calendar · signer",
           person: { title: "the Writer", seat: [382, 650], front: [382, 540], row: "bottom", shirt: "#3A7CC2", skin: "#F0CDAE", hair: "#D9B26F", prop: "typewriter" } },
  models: { label: "Local models", kind: "ext", ang: -90, shape: "chip", role: "Ollama · qwen3 · gemma3",
            place: { label: "Server room", sub: "local models", front: [902, LANE], dwell: 1.3 } },
  portals: { label: "Job portals", kind: "ext", ang: -18, shape: "cards", role: "Naukri · Shine · web",
             place: { label: "Front door", sub: "to the job portals", front: [930, 270], dwell: 1.6 } },
  chrome: { label: "Chrome", kind: "ext", ang: 62, shape: "browser", role: "egress-vetted",
            place: { label: "Chrome booth", sub: "the real form", front: [852, 540], dwell: 1.6 } },
  db: { label: "Records", kind: "ext", ang: 112, shape: "cylinder", role: "audit · jobs · apps",
        place: { label: "Records vault", sub: "audit · jobs · apps", front: [96, LANE], dwell: 0.8 } },
  gmail: { label: "Gmail", kind: "ext", ang: 198, shape: "postbox", role: "read + send",
           place: { label: "Mail chute", sub: "Gmail", front: [96, 560], dwell: 0.9 } },
  you: { label: "You", kind: "you", ang: 252, shape: "phone", role: "phone · desktop",
         person: { title: "the boss", seat: [118, 160], front: [190, 286], row: "office", shirt: "#1D1A16", skin: "#C98E62", hair: "#2B1D14" } },
};

export const AGENTS: NodeKey[] = ["core", "profile", "search", "portal", "matching", "application", "analytics", "email", "reply"];

/** Harness spec names (live events) → cast. */
export const AGENT_KEY: Record<string, NodeKey> = {
  profile_agent: "profile",
  search_agent: "search",
  matching_agent: "matching",
  application_agent: "application",
  email_monitor: "email",
  reply_drafting: "reply",
};

/** Where a tool call goes. */
export function toolTarget(tool: string): NodeKey | null {
  if (tool.startsWith("portal.")) return "portals";
  if (tool.startsWith("apply.")) return "chrome";
  if (tool.startsWith("mail.")) return "gmail";
  if (tool.startsWith("calendar.")) return "db";
  if (tool === "notify") return "you";
  return null;
}
