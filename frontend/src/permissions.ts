import type { PermissionMode } from "./types";

export const MODES: { value: PermissionMode; label: string; hint: string }[] = [
  { value: "acceptEdits", label: "Auto-edit", hint: "File edits are automatic; shell commands ask you first." },
  { value: "default", label: "Ask for everything", hint: "Every edit and command needs your approval." },
  { value: "plan", label: "Plan only", hint: "Read-only. The agent investigates and proposes a plan." },
  { value: "bypassPermissions", label: "Full auto", hint: "No approvals at all. Use only on trusted, committed repos." },
];

/** Used until the saved preferences load. Mirrors `Preferences` in models.py. */
export const BUILT_IN_DEFAULT_MODE: PermissionMode = "acceptEdits";

export const modeInfo = (mode: PermissionMode) => MODES.find((m) => m.value === mode);
