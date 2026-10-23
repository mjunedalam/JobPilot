/** The ⌘K command bar's model: what a command is and how a query ranks them. */

export type CommandGroup = "Decisions" | "Actions" | "Go to";

export interface Command {
  id: string;
  group: CommandGroup;
  title: string;
  hint?: string;
  keywords?: string;
  /** "danger" commands ask for a second Enter before they run. */
  tone?: "danger";
  run: () => void | Promise<unknown>;
}

const GROUP_ORDER: CommandGroup[] = ["Decisions", "Actions", "Go to"];

/** Score `query` as an in-order subsequence of `text`; word starts and runs score higher.
 *  Returns null when it does not match. */
export function fuzzyScore(query: string, text: string): number | null {
  const q = query.trim().toLowerCase();
  if (!q) return 0;
  const t = text.toLowerCase();
  let score = 0, run = 0, from = 0;
  for (const ch of q) {
    if (ch === " ") continue;
    const at = t.indexOf(ch, from);
    if (at < 0) return null;
    const wordStart = at === 0 || /[\s\-·/:(]/.test(t[at - 1] ?? "");
    run = at === from ? run + 1 : 0;
    score += 1 + (wordStart ? 3 : 0) + run * 2 - Math.min(3, (at - from) * 0.1);
    from = at + 1;
  }
  return score;
}

/** Matching commands, best first, kept in their groups (Decisions → Actions → Go to). */
export function rankCommands(commands: Command[], query: string): Command[] {
  const scored = commands
    .map((c, i) => {
      const title = fuzzyScore(query, c.title);
      const extra = fuzzyScore(query, `${c.title} ${c.keywords ?? ""} ${c.hint ?? ""}`);
      const best = title !== null ? title + 2 : extra;
      return best === null ? null : { c, i, best };
    })
    .filter((x): x is { c: Command; i: number; best: number } => x !== null);
  const byGroup = (g: CommandGroup) => GROUP_ORDER.indexOf(g);
  return [...scored]
    .sort((a, b) => byGroup(a.c.group) - byGroup(b.c.group) || (query.trim() ? b.best - a.best : 0) || a.i - b.i)
    .map((x) => x.c);
}
