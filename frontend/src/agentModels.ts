import type { AgentModel } from "./types";

/** Models offered for agent sessions. Mirrors `AgentModel` in models.py. */
export const AGENT_MODELS: { value: AgentModel; label: string; hint: string }[] = [
  { value: "claude-opus-5-5", label: "Opus 5.5", hint: "Strong all-rounder for coding and agent work." },
  { value: "claude-sonnet-5-5", label: "Sonnet 5.5", hint: "Faster and cheaper; good for everyday changes." },
  { value: "claude-haiku-5-5", label: "Haiku 5.5", hint: "Fastest and cheapest; for small, well-defined tasks." },
  { value: "claude-fable-5-2", label: "Fable 5.2", hint: "Most capable, for the hardest work. Costs about twice as much as Opus." },
];

/** Used until the saved preferences load. Mirrors `Preferences` in models.py. */
export const BUILT_IN_DEFAULT_MODEL: AgentModel = "claude-opus-5-5";

export const modelInfo = (model: string | null | undefined) => AGENT_MODELS.find((m) => m.value === model);

/** A short name for a model id, including ids no longer in the list (older runs). */
export const modelLabel = (model: string | null | undefined) => modelInfo(model)?.label ?? model ?? "";
