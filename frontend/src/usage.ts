import { useCallback } from "react";
import { api } from "./api";
import { useLoader, usePolling } from "./hooks";

// This only asks Mercury's backend, which decides when to ask Anthropic. Short, so a reading
// the backend fetched by itself (after a session succeeds) shows up soon.
const POLL_MS = 15_000;

/** Claude Code plan usage, loaded once in App and shared by the gauge and the Usage page. */
export function useClaudeUsage() {
  const { data, setData, error, reload } = useLoader(() => api.claudeUsage(), []);
  usePolling(reload, POLL_MS);

  /** Ask for a new reading now. The backend limits how often that reaches Anthropic. */
  const pull = useCallback(async () => {
    try {
      setData(await api.claudeUsage(true));
    } catch {
      await reload();
    }
  }, [setData, reload]);

  return { data, error, pull };
}

export type ClaudeUsageState = ReturnType<typeof useClaudeUsage>;
