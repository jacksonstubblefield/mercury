import { useEffect, useRef } from "react";
import { api } from "./api";
import { attentionDetail, attentionLabel, attentionState, plainExcerpt } from "./sessions";
import type { Project, SessionAttention } from "./types";

/**
 * Desktop notifications for agent sessions: one when a session starts waiting on the user, and one when
 * it finishes, while the Mercury window isn't in focus. Driven by the sidebar's Sessions list, so it
 * notices changes on the next poll. Nothing fires for what's already there when the app opens.
 * Turned off by the `agent_notifications` setting; needs the desktop shell (window.mercury.notify).
 */
export function useAgentNotifications(sessions: SessionAttention[] | undefined, projects: Project[] | undefined) {
  // Session (root run id) -> "runId:status" last time we looked. null until the first load.
  const last = useRef<Map<number, string> | null>(null);
  const projectsRef = useRef(projects);
  projectsRef.current = projects;

  useEffect(() => {
    if (!sessions) return;
    const prev = last.current;
    last.current = new Map(sessions.map((s) => [s.root_run_id, `${s.run_id}:${s.status}`]));
    if (prev === null || !window.mercury?.notify || document.hasFocus()) return;

    const fresh = sessions.filter(
      (s) => attentionState(s) !== "working" && prev.get(s.root_run_id) !== `${s.run_id}:${s.status}`,
    );
    if (fresh.length === 0) return;
    void api
      .preferences()
      .then((prefs) => {
        if (!prefs.agent_notifications) return;
        for (const s of fresh) {
          const project = projectsRef.current?.find((p) => p.id === s.project_id);
          window.mercury?.notify({ ...describe(s, project?.name ?? "Mercury"), projectId: s.project_id, runId: s.run_id });
        }
      })
      .catch(() => {});
  }, [sessions]);
}

function describe(s: SessionAttention, projectName: string): { title: string; body: string } {
  const label = plainExcerpt(attentionLabel(s), 120);
  switch (attentionState(s)) {
    case "needs_you":
      return { title: `${projectName}: ${attentionDetail(s)}`, body: label };
    case "failed":
      return { title: `${projectName}: agent failed`, body: plainExcerpt(s.error ?? "", 180) || label };
    default:
      return { title: `${projectName}: agent finished`, body: plainExcerpt(s.result ?? "", 180) || label };
  }
}
