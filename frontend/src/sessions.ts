import type { AgentRun, RunStatus, SessionAttention } from "./types";

export const ACTIVE_STATUSES: ReadonlySet<RunStatus> = new Set(["queued", "running", "waiting_approval"]);

/** How to name a run in lists: its prompt, or its images when the user only pasted those. */
export function runLabel(run: AgentRun): string {
  return label(run.prompt, run.images?.length ?? 0);
}

/** The same, for a session in the sidebar's Sessions list. */
export function attentionLabel(a: SessionAttention): string {
  return label(a.title, a.image_count) || "Untitled session";
}

function label(prompt: string, images: number): string {
  const text = prompt.trim();
  if (text) return text;
  return images ? `[${images === 1 ? "image" : `${images} images`}]` : "";
}

/** What a session in the Sessions list is doing, from the user's point of view. */
export type AttentionState = "needs_you" | "done" | "failed" | "working";

export function attentionState(a: SessionAttention): AttentionState {
  if (a.status === "waiting_approval") return "needs_you";
  if (a.status === "succeeded") return "done";
  if (a.status === "failed") return "failed";
  return "working";
}

/** One line on what a session is doing or waiting for. */
export function attentionDetail(a: SessionAttention): string {
  switch (attentionState(a)) {
    case "needs_you":
      return a.pending_tool === "AskUserQuestion"
        ? "Has a question"
        : a.pending_tool
          ? `Needs approval: ${toolName(a.pending_tool)}`
          : "Needs you";
    case "done":
      return "Done";
    case "failed":
      return "Failed";
    default:
      return a.status === "queued" ? "Starting…" : "Working…";
  }
}

/** mcp__mercury__update_task -> update_task; built-in tools are shown as they are. */
function toolName(tool: string): string {
  return tool.startsWith("mcp__") ? tool.split("__").slice(2).join("__") || tool : tool;
}

/** A session is a run plus its chain of follow-ups (which resume the same Claude session). */
export interface Session {
  rootId: number;
  runs: AgentRun[]; // oldest first
  latest: AgentRun;
}

/** Group runs into sessions, newest activity first. */
export function groupSessions(runs: AgentRun[]): Session[] {
  const byId = new Map(runs.map((r) => [r.id, r]));
  const rootOf = (run: AgentRun) => {
    let cur = run;
    for (let hops = 0; cur.parent_run_id != null && byId.has(cur.parent_run_id) && hops < runs.length; hops++) {
      cur = byId.get(cur.parent_run_id)!;
    }
    return cur.id;
  };
  const groups = new Map<number, AgentRun[]>();
  for (const run of runs) {
    const root = rootOf(run);
    groups.set(root, [...(groups.get(root) ?? []), run]);
  }
  return [...groups]
    .map(([rootId, rs]) => {
      rs.sort((a, b) => a.id - b.id);
      return { rootId, runs: rs, latest: rs[rs.length - 1] };
    })
    .sort((a, b) => b.latest.id - a.latest.id);
}

export function sessionCost(session: Session): number {
  return session.runs.reduce((sum, r) => sum + (r.cost_usd ?? 0), 0);
}

/**
 * The agent's own wrap-up for a session: the newest turn's final message. Agents are told to
 * end every run with a short summary of what they changed, so this needs no extra model call.
 */
export function sessionSummary(session: Session): { text: string; kind: "result" | "error" | "none" } {
  for (const run of [...session.runs].reverse()) {
    if (run.status === "failed" && run.error) return { text: run.error, kind: "error" };
    if (run.result?.trim()) return { text: run.result.trim(), kind: "result" };
  }
  return { text: "", kind: "none" };
}

/** Flatten Markdown to one line of plain text for a preview. */
export function plainExcerpt(markdown: string, max = 220): string {
  const text = markdown
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/^\s{0,3}(#{1,6}\s+|>\s?|[-*+]\s+|\d+[.)]\s+)/gm, "")
    .replace(/(\*\*|__|`)/g, "") // not single * or _: they'd mangle snake_case names
    .replace(/\s+/g, " ")
    .trim();
  return text.length > max ? text.slice(0, max).replace(/\s+\S*$/, "") + "…" : text;
}
