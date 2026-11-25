import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { timeAgo, useLoader, usePolling } from "../hooks";
import { ACTIVE_STATUSES as ACTIVE, groupSessions, runLabel, type Session } from "../sessions";
import { taskRef } from "../refs";
import type {
  AgentEvent,
  AgentModel,
  AgentRun,
  PermissionMode,
  Preferences,
  Project,
  SessionOpenScroll,
  StreamMessage,
  Task,
} from "../types";
import { BUILT_IN_DEFAULT_MODE, MODES, modeInfo } from "../permissions";
import { AGENT_MODELS, BUILT_IN_DEFAULT_MODEL, modelInfo, modelLabel } from "../agentModels";
import { Markdown } from "./Markdown";
import { ImageStrip, MessageImages, usePastedImages } from "./PastedImages";
import { ChangesPanel, EditCard, FilesContext, type FileEdit, fileEditsFrom, isEditTool, pathKey } from "./SessionChanges";

function ModeSelect({
  value,
  onChange,
  title,
  className,
}: {
  value: PermissionMode;
  onChange: (mode: PermissionMode) => void;
  title?: string;
  className?: string;
}) {
  return (
    <select className={className} value={value} onChange={(e) => onChange(e.target.value as PermissionMode)} title={title}>
      {MODES.map((m) => (
        <option key={m.value} value={m.value}>
          {m.label}
        </option>
      ))}
    </select>
  );
}

function ModelSelect({
  value,
  onChange,
  title,
  className,
  disabled,
}: {
  value: string;
  onChange: (model: AgentModel) => void;
  title?: string;
  className?: string;
  disabled?: boolean;
}) {
  return (
    <select
      className={className}
      value={value}
      onChange={(e) => onChange(e.target.value as AgentModel)}
      title={title}
      disabled={disabled}
    >
      {/* A session started on a model that's no longer offered still shows what it ran on. */}
      {!modelInfo(value) && <option value={value}>{modelLabel(value)}</option>}
      {AGENT_MODELS.map((m) => (
        <option key={m.value} value={m.value}>
          {m.label}
        </option>
      ))}
    </select>
  );
}

/** Next to a field label: "Default" when the choice is the default, else a link that makes it the default. */
function DefaultLink({
  isDefault,
  projectHasOwn,
  what,
  onMakeDefault,
}: {
  isDefault: boolean;
  projectHasOwn: boolean;
  what: string;
  onMakeDefault: () => void;
}) {
  if (isDefault) return <span className="faint">{projectHasOwn ? "Project default" : "Default"}</span>;
  return (
    <button
      className="link-btn"
      onClick={onMakeDefault}
      title={
        projectHasOwn
          ? `Preselect this ${what} for new sessions in this project`
          : `Preselect this ${what} for new sessions (projects can override it in Settings)`
      }
    >
      {projectHasOwn ? "Make project default" : "Make default"}
    </button>
  );
}

/** Enter sends, Shift+Enter adds a line (as in Claude Code). */
function isSendKey(e: React.KeyboardEvent) {
  return e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing;
}

function errorText(e: unknown) {
  return e instanceof Error ? e.message : String(e);
}

// ------------------------------------------------------------------------- sessions

/** `fresh`: a session just started here, which follows its output rather than opening per the setting. */
type View = { kind: "new" } | { kind: "session"; rootId: number; fresh?: boolean };
type Jump = { runId: number; smooth: boolean; nonce: number };

/**
 * Show the session containing `runId`. "run" scrolls to that run's message (links from the board and
 * docs); "open" opens it like a click in the session list, where the `session_open_scroll` setting says.
 * A new `nonce` reopens it even if it's the same run.
 */
export type FocusRequest = { runId: number; nonce: number; scroll: "run" | "open" };

/** Used until the saved preferences load, or if they can't. Mirrors `Preferences` in models.py. */
const BUILT_IN_OPEN_SCROLL: SessionOpenScroll = "last_message";

interface Props {
  project: Project;
  initialTask: Task | null;
  onConsumeInitialTask: () => void;
  focus: FocusRequest | null;
  onChanged: () => void;
}

export function AgentsTab({ project, initialTask, onConsumeInitialTask, focus, onChanged }: Props) {
  const runs = useLoader(() => api.runs(project.id), [project.id]);
  const tasks = useLoader(() => api.tasks(project.id), [project.id]);
  // Where a session opens. Sessions wait for it (it's a local read), so they don't open in one place and jump.
  const prefs = useLoader(() => api.preferences(), []);
  const prefsSettled = !!prefs.data || !!prefs.error;
  const openScroll = prefs.data?.session_open_scroll ?? BUILT_IN_OPEN_SCROLL;
  const sessions =useMemo(() => groupSessions(runs.data ?? []), [runs.data]);
  const [view, setView] = useState<View | null>(initialTask ? { kind: "new" } : null);
  const [jump, setJump] = useState<Jump | null>(
    focus?.scroll === "run" ? { runId: focus.runId, smooth: false, nonce: 0 } : null,
  );

  const anyActive = (runs.data ?? []).some((r) => ACTIVE.has(r.status));
  usePolling(runs.reload, 3000, anyActive);

  // A later focus request (e.g. a sidebar click while this tab is open) switches sessions. The run may be
  // newer than our list, so reload once if it's missing.
  const handledFocus = useRef(focus?.nonce ?? null);
  const reloadedFor = useRef<number | null>(null);
  useEffect(() => {
    if (!focus || focus.nonce === handledFocus.current || !runs.data) return;
    const target = sessions.find((s) => s.runs.some((r) => r.id === focus.runId));
    if (!target) {
      if (reloadedFor.current !== focus.nonce) {
        reloadedFor.current = focus.nonce;
        void runs.reload();
      }
      return;
    }
    handledFocus.current = focus.nonce;
    setView({ kind: "session", rootId: target.rootId });
    setJump(focus.scroll === "run" ? { runId: focus.runId, smooth: false, nonce: focus.nonce } : null);
  }, [focus, sessions, runs.data, runs.reload]);

  // Pick what to show once runs have loaded: the focused run's session, else the latest session.
  const focusRunId = focus?.runId ?? null;
  useEffect(() => {
    if (view !== null || !runs.data) return;
    const focused = focusRunId ? sessions.find((s) => s.runs.some((r) => r.id === focusRunId)) : undefined;
    const pick = focused ?? sessions[0];
    setView(pick ? { kind: "session", rootId: pick.rootId } : { kind: "new" });
  }, [view, runs.data, sessions, focusRunId]);

  // "Run agent" from the board opens a new session for that task.
  useEffect(() => {
    if (initialTask) setView({ kind: "new" });
  }, [initialTask]);

  const current = view?.kind === "session" ? sessions.find((s) => s.rootId === view.rootId) : undefined;

  // Opening a finished session counts as seeing it, which takes it off the sidebar's Sessions list.
  // Only while the window has focus: a session that finishes in the background should still show up.
  const unseenId = current && !ACTIVE.has(current.latest.status) && !current.latest.seen ? current.latest.id : null;
  const { setData: setRuns } = runs;
  useEffect(() => {
    if (unseenId === null) return;
    let sent = false;
    const mark = () => {
      if (sent || !document.hasFocus()) return;
      sent = true;
      api
        .markSeen(unseenId)
        .then((run) => {
          setRuns((prev) => prev?.map((r) => (r.id === run.id ? run : r)));
          onChanged();
        })
        .catch(() => (sent = false));
    };
    mark();
    window.addEventListener("focus", mark);
    return () => window.removeEventListener("focus", mark);
  }, [unseenId, setRuns, onChanged]);

  const addRun = (run: AgentRun) => {
    runs.setData((prev) => [run, ...(prev ?? []).filter((r) => r.id !== run.id)]);
    void runs.reload();
    onChanged();
  };

  const refresh = useCallback(() => {
    void runs.reload();
    void tasks.reload();
    onChanged();
  }, [runs.reload, tasks.reload, onChanged]);

  function jumpTo(session: Session, runId: number) {
    const already = current?.rootId === session.rootId;
    if (!already) setView({ kind: "session", rootId: session.rootId });
    setJump({ runId, smooth: already, nonce: Date.now() });
  }

  const taskRefs = useMemo(
    () => new Map((tasks.data ?? []).map((t) => [t.id, taskRef(project.key, t)])),
    [tasks.data, project.key],
  );

  return (
    <div className="agents">
      <div className="agents-side">
        <button className="btn primary" disabled={view?.kind === "new"} onClick={() => setView({ kind: "new" })}>
          + New session
        </button>
        <div className="card run-list">
          {sessions.map((s) => (
            <SessionItem
              key={s.rootId}
              session={s}
              taskLabel={s.latest.task_id != null ? taskRefs.get(s.latest.task_id) : undefined}
              selected={s.rootId === current?.rootId}
              onSelect={() => {
                setView({ kind: "session", rootId: s.rootId });
                setJump(null);
              }}
              onJump={(runId) => jumpTo(s, runId)}
            />
          ))}
          {runs.data?.length === 0 && <p className="faint" style={{ padding: 8 }}>No sessions yet.</p>}
        </div>
      </div>

      {view?.kind === "new" ? (
        <NewSession
          project={project}
          tasks={tasks.data ?? []}
          initialTask={initialTask}
          onConsumeInitialTask={onConsumeInitialTask}
          onProjectChanged={onChanged}
          onCancel={sessions.length ? () => setView({ kind: "session", rootId: sessions[0].rootId }) : undefined}
          onStarted={(run) => {
            setView({ kind: "session", rootId: run.id, fresh: true });
            setJump(null);
            addRun(run);
          }}
        />
      ) : current && prefsSettled ? (
        <SessionView
          key={current.rootId}
          project={project}
          session={current}
          taskLabel={current.latest.task_id != null ? taskRefs.get(current.latest.task_id) : undefined}
          openAt={view?.kind === "session" && view.fresh ? "bottom" : openScroll}
          jump={jump}
          onSent={(run) => {
            setJump(null);
            addRun(run);
          }}
          onChanged={refresh}
        />
      ) : (
        <div className="run-view empty">Loading…</div>
      )}
    </div>
  );
}

function SessionItem({
  session,
  taskLabel,
  selected,
  onSelect,
  onJump,
}: {
  session: Session;
  /** The linked task's id (e.g. MER-12), if it still exists. */
  taskLabel?: string;
  selected: boolean;
  onSelect: () => void;
  onJump: (runId: number) => void;
}) {
  const { runs, latest } = session;
  const cost = runs.reduce((sum, r) => sum + (r.cost_usd ?? 0), 0);
  return (
    <div className={`run-item ${selected ? "active" : ""}`} onClick={onSelect}>
      <div className="prompt">{runLabel(runs[0])}</div>
      <div className="row" style={{ fontSize: 12 }}>
        <span className={`status ${latest.status}`}>{latest.status.replace("_", " ")}</span>
        {taskLabel && <span className="faint">{taskLabel}</span>}
        {runs.length > 1 && <span className="faint">{runs.length} messages</span>}
        <span className="spacer" />
        {cost > 0 && <span className="faint">${cost.toFixed(2)}</span>}
        <span className="faint">{timeAgo(latest.created_at)}</span>
      </div>
      {selected && runs.length > 1 && (
        <div className="bookmarks">
          {runs.map((r) => (
            <div
              key={r.id}
              className="bookmark"
              title={runLabel(r)}
              onClick={(e) => {
                e.stopPropagation();
                onJump(r.id);
              }}
            >
              {runLabel(r)}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------------- new session

function NewSession({
  project,
  tasks,
  initialTask,
  onConsumeInitialTask,
  onCancel,
  onStarted,
  onProjectChanged,
}: {
  project: Project;
  tasks: Task[];
  initialTask: Task | null;
  onConsumeInitialTask: () => void;
  onCancel?: () => void;
  onStarted: (run: AgentRun) => void;
  onProjectChanged: () => void;
}) {
  const [prompt, setPrompt] = useState("");
  const [taskId, setTaskId] = useState<number | null>(null);
  // The project's own defaults, where it has them, else the app-wide ones (loaded below).
  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const projectDefault = project.default_permission_mode;
  const defaultMode = projectDefault ?? prefs?.default_permission_mode ?? null;
  const [mode, setMode] = useState<PermissionMode>(projectDefault ?? BUILT_IN_DEFAULT_MODE);
  const modeTouched = useRef(false);
  const projectModel = project.default_model;
  const defaultModel = projectModel ?? prefs?.default_model ?? null;
  const [model, setModel] = useState<AgentModel>(projectModel ?? BUILT_IN_DEFAULT_MODEL);
  const modelTouched = useRef(false);
  const [budget, setBudget] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const pasted = usePastedImages();

  useEffect(() => {
    if (initialTask) {
      setTaskId(initialTask.id);
      setPrompt(`Complete task ${taskRef(project.key, initialTask)}: ${initialTask.title}`);
      onConsumeInitialTask();
    }
  }, [initialTask, onConsumeInitialTask, project.key]);

  useEffect(() => {
    let live = true;
    api
      .preferences()
      .then((p) => live && setPrefs(p))
      .catch(() => {}); // keep the built-in defaults
    return () => {
      live = false;
    };
  }, []);

  // Preselect the default once it's known (or when it changes), unless the user has already picked a mode.
  useEffect(() => {
    if (defaultMode && !modeTouched.current) setMode(defaultMode);
  }, [defaultMode]);

  useEffect(() => {
    if (defaultModel && !modelTouched.current) setModel(defaultModel);
  }, [defaultModel]);

  function pickMode(next: PermissionMode) {
    modeTouched.current = true;
    setMode(next);
  }

  function pickModel(next: AgentModel) {
    modelTouched.current = true;
    setModel(next);
  }

  /** Saves a choice as this project's default if the project has its own, else as the app-wide default. */
  async function makeDefault(
    changes: { default_permission_mode: PermissionMode } | { default_model: AgentModel },
    projectHasOwn: boolean,
  ) {
    try {
      if (projectHasOwn) {
        await api.updateProject(project.id, changes);
        onProjectChanged(); // reloads the project, so its defaults catch up
      } else {
        setPrefs(await api.updatePreferences(changes));
      }
    } catch (e) {
      setError(errorText(e));
    }
  }

  const canStart = (!!prompt.trim() || pasted.images.length > 0) && !busy && !!project.repo_path;

  async function start() {
    if (!canStart) return;
    setBusy(true);
    setError(null);
    try {
      const run = await api.startRun(project.id, {
        prompt,
        task_id: taskId,
        permission_mode: mode,
        model,
        max_budget_usd: budget ? Number(budget) : null,
        images: pasted.payload,
      });
      onStarted(run);
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  }

  const openTasks = tasks.filter((t) => t.status !== "done");
  const linkedImages = tasks.find((t) => t.id === taskId)?.images ?? [];

  return (
    <div className="run-view new-session">
      <div className="new-session-inner">
        <h2>New session</h2>
        <p className="faint">Claude works directly in {project.repo_path ?? "the project's repo"}.</p>
        {!project.repo_path && <p className="error-text">Set a local repo folder in project Settings first.</p>}
        <textarea
          autoFocus
          rows={5}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="What should Claude do? e.g. “Add a /health endpoint and a test for it.” Paste or drop screenshots here too."
          onKeyDown={(e) => {
            if (isSendKey(e)) {
              e.preventDefault();
              void start();
            }
          }}
          {...pasted.handlers}
        />
        <ImageStrip images={pasted.images} onRemove={pasted.remove} />
        {pasted.error && <p className="error-text">{pasted.error}</p>}
        <label>Linked task (its description and pictures are shared with the agent)</label>
        <select value={taskId ?? ""} onChange={(e) => setTaskId(e.target.value ? Number(e.target.value) : null)}>
          <option value="">None</option>
          {openTasks.map((t) => (
            <option key={t.id} value={t.id}>
              {taskRef(project.key, t)}: {t.title}
            </option>
          ))}
        </select>
        {linkedImages.length > 0 && (
          <div className="linked-task-images faint">
            The task's {linkedImages.length > 1 ? `${linkedImages.length} pictures go` : "picture goes"} with your prompt:
            <MessageImages names={linkedImages} />
          </div>
        )}
        <div className="row">
          <div style={{ flex: 2 }}>
            <div className="mode-label">
              <label>Model</label>
              {defaultModel !== null && (
                <DefaultLink
                  isDefault={model === defaultModel}
                  projectHasOwn={!!projectModel}
                  what="model"
                  onMakeDefault={() => void makeDefault({ default_model: model }, !!projectModel)}
                />
              )}
            </div>
            <ModelSelect value={model} onChange={pickModel} title={modelInfo(model)?.hint} />
          </div>
          <div style={{ flex: 2 }}>
            <div className="mode-label">
              <label>Permissions</label>
              {defaultMode !== null && (
                <DefaultLink
                  isDefault={mode === defaultMode}
                  projectHasOwn={!!projectDefault}
                  what="mode"
                  onMakeDefault={() => void makeDefault({ default_permission_mode: mode }, !!projectDefault)}
                />
              )}
            </div>
            <ModeSelect value={mode} onChange={pickMode} title={modeInfo(mode)?.hint} />
          </div>
          <div style={{ flex: 1 }}>
            <label>Budget ($)</label>
            <input type="number" min="0" step="0.5" value={budget} onChange={(e) => setBudget(e.target.value)} placeholder="none" />
          </div>
        </div>
        <p className="faint" style={{ fontSize: 12, margin: "6px 0 12px" }}>
          {modelInfo(model)?.hint} {modeInfo(mode)?.hint}
        </p>
        {error && <p className="error-text">{error}</p>}
        <div className="row">
          <span className="faint" style={{ fontSize: 12 }}>
            Enter to start · Shift+Enter for a new line · Paste screenshots
          </span>
          <span className="spacer" />
          {onCancel && (
            <button className="btn" onClick={onCancel}>
              Cancel
            </button>
          )}
          <button className="btn primary" disabled={!canStart} onClick={start}>
            ▶ Start
          </button>
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------- session view

type Follow = { mode: "bottom" } | { mode: "free" } | { mode: "anchor"; runId: number };
const NEAR_BOTTOM_PX = 40;

const PANEL_KEY = "mercury.changesPanel";

function readPanelOpen(): boolean {
  try {
    return localStorage.getItem(PANEL_KEY) === "1";
  } catch {
    return false;
  }
}

function SessionView({
  project,
  session,
  taskLabel,
  openAt,
  jump,
  onSent,
  onChanged,
}: {
  project: Project;
  session: Session;
  taskLabel?: string;
  /** Where the conversation starts when it opens; a `jump` overrides it. Read once, on mount. */
  openAt: SessionOpenScroll;
  jump: Jump | null;
  onSent: (run: AgentRun) => void;
  onChanged: () => void;
}) {
  // Live run state from each turn's stream; fresher than the polled list.
  const [live, setLive] = useState<Record<number, AgentRun>>({});
  const runs = session.runs.map((r) => live[r.id] ?? r);
  const latest = runs[runs.length - 1];
  const active = ACTIVE.has(latest.status);
  const resumeFrom = [...runs].reverse().find((r) => r.session_id);
  const cost = runs.reduce((sum, r) => sum + (r.cost_usd ?? 0), 0);
  const turns = runs.reduce((sum, r) => sum + (r.num_turns ?? 0), 0);

  const onRun = useCallback((run: AgentRun) => setLive((prev) => ({ ...prev, [run.id]: run })), []);

  // ---- files Claude changed, gathered from each turn's edit tool calls
  const [editsByRun, setEditsByRun] = useState<Record<number, FileEdit[]>>({});
  const onEdits = useCallback((runId: number, edits: FileEdit[]) => setEditsByRun((prev) => ({ ...prev, [runId]: edits })), []);
  const edits = useMemo(() => runs.flatMap((r) => editsByRun[r.id] ?? []), [runs, editsByRun]);
  const fileCount = useMemo(() => new Set(edits.map((e) => pathKey(e.path))).size, [edits]);
  const [panelOpen, setPanelOpenState] = useState(readPanelOpen);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const setPanelOpen = (open: boolean) => {
    setPanelOpenState(open);
    try {
      localStorage.setItem(PANEL_KEY, open ? "1" : "0");
    } catch {
      /* storage unavailable: the panel just won't remember */
    }
  };
  const files = useMemo(
    () => ({
      repoPath: project.repo_path,
      openFile: (path: string) => {
        setSelectedFile(path);
        setPanelOpen(true);
      },
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [project.repo_path],
  );

  // Refresh the sidebar, board counts, etc. when the session changes state.
  const prevStatus = useRef<string | null>(null);
  useEffect(() => {
    if (prevStatus.current !== null && prevStatus.current !== latest.status) onChanged();
    prevStatus.current = latest.status;
  }, [latest.status, onChanged]);

  // ---- scrolling: follow new output only while the user is at the bottom
  const scroller = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  // Opening at the last message anchors it (re-applied while the transcript loads) until the user scrolls.
  const follow = useRef<Follow>(
    openAt === "top"
      ? { mode: "free" }
      : openAt === "last_message"
        ? { mode: "anchor", runId: session.runs[session.runs.length - 1].id }
        : { mode: "bottom" },
  );
  const [atBottom, setAtBottom] = useState(openAt === "bottom");
  const [flash, setFlash] = useState<number | null>(null);

  const messageTop = (runId: number) => {
    const target = document.getElementById(`msg-${runId}`);
    return target ? target.offsetTop - 12 : null;
  };

  const applyFollow = useCallback(() => {
    const el = scroller.current;
    const f = follow.current;
    if (!el) return;
    if (f.mode === "bottom") el.scrollTop = el.scrollHeight;
    else if (f.mode === "anchor") {
      const top = messageTop(f.runId);
      if (top !== null) el.scrollTop = top;
    }
  }, []);

  useLayoutEffect(() => {
    const observer = new ResizeObserver(() => {
      applyFollow();
      // Content can grow without a scroll event (e.g. opened at the top), so keep "↓ Latest" in step.
      const el = scroller.current;
      if (el) setAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX);
    });
    if (content.current) observer.observe(content.current);
    if (scroller.current) observer.observe(scroller.current);
    return () => observer.disconnect();
  }, [applyFollow]);

  /** Glide over short distances; jump straight there over long ones (a long glide feels sluggish). */
  const scrollToTop = (top: number) => {
    const el = scroller.current;
    if (!el) return;
    const near = Math.abs(el.scrollTop - top) < el.clientHeight * 2;
    el.scrollTo({ top, behavior: near ? "smooth" : "auto" });
  };

  useEffect(() => {
    if (!jump || !session.runs.some((r) => r.id === jump.runId)) return;
    const top = messageTop(jump.runId);
    if (jump.smooth && top !== null) {
      follow.current = { mode: "free" };
      scrollToTop(top);
    } else {
      // Content may still be loading: keep re-anchoring until the user scrolls.
      follow.current = { mode: "anchor", runId: jump.runId };
      applyFollow();
    }
    setFlash(jump.runId);
    const t = setTimeout(() => setFlash(null), 1600);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jump]);

  function onScroll() {
    const el = scroller.current!;
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX;
    if (follow.current.mode !== "anchor") follow.current = near ? { mode: "bottom" } : { mode: "free" };
    setAtBottom(near);
  }

  function onUserScrollIntent() {
    if (follow.current.mode === "anchor") follow.current = { mode: "free" };
  }

  function scrollToLatest() {
    follow.current = { mode: "bottom" };
    if (scroller.current) scrollToTop(scroller.current.scrollHeight - scroller.current.clientHeight);
  }

  // ---- composer
  const [text, setText] = useState("");
  const [nextMode, setNextMode] = useState<PermissionMode | null>(null);
  const [nextModel, setNextModel] = useState<AgentModel | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const pasted = usePastedImages();
  const shownMode = active ? latest.permission_mode : nextMode ?? latest.permission_mode;
  // Runs from before models could be chosen have none recorded; they ran on Opus 5.5.
  const sessionModel = latest.model ?? BUILT_IN_DEFAULT_MODEL;
  const shownModel = active ? sessionModel : nextModel ?? sessionModel;
  const canSend = !active && !!resumeFrom && (!!text.trim() || pasted.images.length > 0) && !sending;

  async function send() {
    if (!canSend || !resumeFrom) return;
    setActionError(null);
    setSending(true);
    try {
      const run = await api.followUp(resumeFrom.id, text, shownMode, pasted.payload, nextModel);
      setText("");
      pasted.clear();
      follow.current = { mode: "bottom" };
      onSent(run);
    } catch (e) {
      setActionError(errorText(e));
    } finally {
      setSending(false);
    }
  }

  async function changeMode(mode: PermissionMode) {
    if (!active) return setNextMode(mode);
    setActionError(null);
    try {
      onRun(await api.setRunMode(latest.id, mode));
    } catch (e) {
      setActionError(errorText(e));
    }
  }

  async function stop() {
    setActionError(null);
    try {
      await api.cancelRun(latest.id);
    } catch (e) {
      setActionError(errorText(e));
    }
  }

  return (
    <FilesContext.Provider value={files}>
    <div className={`session-layout ${panelOpen ? "with-changes" : ""}`}>
    <div className="run-view">
      <div className="run-head row">
        <span className={`status ${latest.status}`}>{latest.status.replace("_", " ")}</span>
        <span className="session-title" title={runLabel(runs[0])}>
          {runLabel(runs[0])}
        </span>
        <span className="spacer" />
        {taskLabel && <span className="faint">{taskLabel}</span>}
        {turns > 0 && <span className="faint">· {turns} turns</span>}
        {cost > 0 && <span className="faint">· ${cost.toFixed(4)}</span>}
        <button
          className={`btn small ${panelOpen ? "active" : ""}`}
          onClick={() => setPanelOpen(!panelOpen)}
          title={panelOpen ? "Hide the files changed in this session" : "Show the files changed in this session"}
        >
          Files changed{fileCount > 0 ? ` · ${fileCount}` : ""}
        </button>
      </div>

      <div className="transcript-wrap">
        <div
          className="transcript"
          ref={scroller}
          onScroll={onScroll}
          onWheel={onUserScrollIntent}
          onTouchStart={onUserScrollIntent}
          onPointerDown={onUserScrollIntent}
          onKeyDown={onUserScrollIntent}
          tabIndex={-1}
        >
          <div className="transcript-content" ref={content}>
            {runs.map((run, i) => (
              <Turn
                key={run.id}
                run={run}
                first={i === 0}
                flash={flash === run.id}
                onRun={onRun}
                onEdits={onEdits}
                onTasksChanged={onChanged}
              />
            ))}
          </div>
        </div>
        {!atBottom && (
          <button className="btn small jump-latest" onClick={scrollToLatest}>
            ↓ Latest
          </button>
        )}
      </div>

      <div className="composer">
        {actionError && <p className="error-text">{actionError}</p>}
        {pasted.error && <p className="error-text">{pasted.error}</p>}
        {!active && !resumeFrom && (
          <p className="faint" style={{ fontSize: 12 }}>
            This session never started, so it can't be continued. Start a new session instead.
          </p>
        )}
        <textarea
          rows={2}
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={!active && !resumeFrom}
          placeholder={active ? "Claude is working… you can type your next message." : "Reply to Claude…"}
          onKeyDown={(e) => {
            if (isSendKey(e)) {
              e.preventDefault();
              void send();
            }
          }}
          {...pasted.handlers}
        />
        <ImageStrip images={pasted.images} onRemove={pasted.remove} />
        <div className="row">
          <ModelSelect
            className="small"
            value={shownModel}
            onChange={setNextModel}
            disabled={active}
            title={
              active
                ? "You can switch models for your next message once this turn finishes."
                : `${modelInfo(shownModel)?.hint ?? ""} Applies from your next message on.`.trim()
            }
          />
          <ModeSelect
            className="small"
            value={shownMode}
            onChange={(m) => void changeMode(m)}
            title={
              active
                ? "Change permissions for the current turn. Pending approvals the new mode allows are approved automatically."
                : modeInfo(shownMode)?.hint
            }
          />
          {latest.status === "waiting_approval" ? (
            <span className="composer-status faint">
              <a href="#" onClick={(e) => (e.preventDefault(), scrollToLatest())}>
                Waiting for your approval ↓
              </a>
            </span>
          ) : active ? (
            <span className="composer-status faint">Working…</span>
          ) : (
            <span
              className="composer-status composer-hint faint"
              title="Enter to send · Shift+Enter for a new line · Paste screenshots"
            >
              Enter to send · Shift+Enter for a new line · Paste screenshots
            </span>
          )}
          <span className="spacer" />
          {active ? (
            <button className="btn small danger" onClick={() => void stop()}>
              ■ Stop
            </button>
          ) : (
            <button className="btn primary small" disabled={!canSend} onClick={() => void send()}>
              Send
            </button>
          )}
        </div>
      </div>
    </div>
    {panelOpen && (
      <ChangesPanel
        project={project}
        edits={edits}
        active={active}
        selected={selectedFile}
        onSelect={setSelectedFile}
        onClose={() => setPanelOpen(false)}
      />
    )}
    </div>
    </FilesContext.Provider>
  );
}

// ------------------------------------------------------------------------- one turn (run)

/** Events for one run: streamed live while it's active, loaded once if it's finished. */
function useRunEvents(run: AgentRun, onRun: (run: AgentRun) => void) {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [connError, setConnError] = useState<string | null>(null);
  const [live] = useState(() => ACTIVE.has(run.status));
  const onRunRef = useRef(onRun);
  onRunRef.current = onRun;

  useEffect(() => {
    if (!live) {
      let cancelled = false;
      api
        .runEvents(run.id)
        .then((evs) => !cancelled && setEvents(evs))
        .catch((e) => !cancelled && setConnError(`Couldn't load this turn: ${errorText(e)}`));
      return () => {
        cancelled = true;
      };
    }

    // On reconnect the server replays history; `seen` dedupes it.
    const seen = new Set<number>();
    let ws: WebSocket | null = null;
    let disposed = false;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;

    const connect = () => {
      ws = new WebSocket(api.streamUrl(run.id));
      ws.onopen = () => {
        attempt = 0;
        setConnError(null);
      };
      ws.onmessage = (msg) => {
        const data = JSON.parse(msg.data) as StreamMessage;
        if (data.type === "run") onRunRef.current(data.run);
        else if (!seen.has(data.seq)) {
          seen.add(data.seq);
          setEvents((prev) => [...prev, data]);
        }
      };
      ws.onclose = (e) => {
        if (disposed) return; // we closed it ourselves (switched sessions / unmounted)
        if (e.code >= 4000) {
          setConnError(`Mercury refused the live feed for this run: ${e.reason || e.code}.`);
          return;
        }
        attempt += 1;
        setConnError("Live updates paused: lost contact with the Mercury backend. Reconnecting…");
        retry = setTimeout(connect, Math.min(1000 * 2 ** (attempt - 1), 10_000));
      };
    };
    connect();
    return () => {
      disposed = true;
      clearTimeout(retry);
      ws?.close();
    };
  }, [run.id, live]);

  return { events, connError, live };
}

function Turn({
  run,
  first,
  flash,
  onRun,
  onEdits,
  onTasksChanged,
}: {
  run: AgentRun;
  first: boolean;
  flash: boolean;
  onRun: (run: AgentRun) => void;
  onEdits: (runId: number, edits: FileEdit[]) => void;
  onTasksChanged: () => void;
}) {
  const { events, connError, live } = useRunEvents(run, onRun);

  // Report this turn's file edits to the session (only when the set of finished edits changes).
  const edits = useMemo(() => fileEditsFrom(run.id, events), [run.id, events]);
  const editsKey = edits.map((e) => e.seq).join(",");
  useEffect(() => {
    onEdits(run.id, edits);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run.id, editsKey, onEdits]);
  const decided = new Set(events.filter((e) => e.kind === "approval_decision").map((e) => e.payload.approval_id));
  const results = new Map(events.filter((e) => e.kind === "tool_result").map((e) => [e.payload.tool_use_id, e.payload]));
  const hasText = events.some((e) => e.kind === "text");
  const active = ACTIVE.has(run.status);

  // The agent edited Mercury tasks while we watched: refresh task lists.
  const taskToolIds = new Set(events.filter((e) => e.kind === "tool_use" && isTaskTool(e.payload.name)).map((e) => e.payload.id));
  const taskEdits = events.filter((e) => e.kind === "tool_result" && taskToolIds.has(e.payload.tool_use_id)).length;
  const seenTaskEdits = useRef(0);
  useEffect(() => {
    if (live && taskEdits > seenTaskEdits.current) onTasksChanged();
    seenTaskEdits.current = taskEdits;
  }, [live, taskEdits, onTasksChanged]);

  return (
    <div className="turn">
      <div id={`msg-${run.id}`} className={`msg-user ${flash ? "flash" : ""}`}>
        <MessageImages names={run.images ?? []} />
        {run.prompt.trim() && <div className="msg-text">{run.prompt}</div>}
        <div className="msg-meta">
          {run.model && `${modelLabel(run.model)} · `}
          {modeInfo(run.permission_mode)?.label} · {timeAgo(run.created_at)}
        </div>
      </div>
      {events.map((e) => (
        <EventItem key={e.seq} event={e} runId={run.id} decided={decided} results={results} first={first} hasText={hasText} />
      ))}
      {active && (
        <div className="ev-working">
          <span className="pulse" />
          {run.status === "waiting_approval" ? "Waiting for your approval" : run.status === "queued" ? "Starting…" : "Working…"}
        </div>
      )}
      {connError && <p className="error-text">{connError}</p>}
    </div>
  );
}

function EventItem({
  event,
  runId,
  decided,
  results,
  first,
  hasText,
}: {
  event: AgentEvent;
  runId: number;
  decided: Set<string>;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  results: Map<string, Record<string, any>>;
  first: boolean; // first turn of the session
  hasText: boolean; // the turn has assistant text (so the result needn't repeat it)
}) {
  const p = event.payload;
  switch (event.kind) {
    case "system":
      // Every turn re-initialises the same session; only the first is worth showing.
      return first ? <div className="ev-system">Session started · {p.model} · {p.cwd}</div> : null;
    case "text":
      return (
        <div className="ev-text">
          <Markdown>{p.text}</Markdown>
        </div>
      );
    case "thinking":
      return <div className="ev-thinking">{p.text}</div>;
    case "tool_use": {
      const result = results.get(p.id);
      if (isEditTool(p.name)) return <EditCard name={p.name} input={p.input} result={result} />;
      return (
        <details className="ev-tool">
          <summary>
            <b>{toolLabel(p.name)}</b> {toolSummary(p.name, p.input)}
            {result?.is_error && <span className="error-text"> · error</span>}
          </summary>
          <div className="body">
            <pre>{JSON.stringify(p.input, null, 2)}</pre>
          </div>
          {result && (
            <div className={`body ${result.is_error ? "err" : ""}`}>
              <pre>{result.content || "(no output)"}</pre>
            </div>
          )}
        </details>
      );
    }
    case "tool_result":
      return null; // rendered inside its tool_use
    case "approval_request": {
      const pending = !decided.has(p.approval_id);
      return (
        <div className="ev-approval">
          <div>
            <b>Approval needed:</b> {toolLabel(p.tool_name)} {toolSummary(p.tool_name, p.input)}
          </div>
          {isEditTool(p.tool_name) ? (
            <div style={{ marginTop: 6 }}>
              <EditCard name={p.tool_name} input={p.input} />
            </div>
          ) : (
            <pre style={{ marginTop: 6, maxHeight: 200, overflow: "auto" }}>{JSON.stringify(p.input, null, 2)}</pre>
          )}
          {pending && (
            <div className="row" style={{ marginTop: 8 }}>
              <button className="btn primary small" onClick={() => void api.decide(runId, p.approval_id, true)}>
                Allow
              </button>
              <button
                className="btn small danger"
                onClick={() => {
                  const reason = prompt("Tell the agent why (optional):") ?? "";
                  void api.decide(runId, p.approval_id, false, reason);
                }}
              >
                Deny
              </button>
            </div>
          )}
        </div>
      );
    }
    case "approval_decision":
      return <div className="ev-system">{p.allow ? "✓ Allowed" : `✗ Denied${p.message ? `: ${p.message}` : ""}`}</div>;
    case "result": {
      // cost_usd is this run's share; older events only have the session's running total.
      const runCost = p.cost_usd !== undefined ? p.cost_usd : p.total_cost_usd;
      const cost = runCost != null ? ` · $${Number(runCost).toFixed(4)}` : "";
      const showText = p.result && (p.is_error || !hasText);
      return (
        <div className={p.is_error ? "ev-error" : "ev-done"}>
          <div>
            {p.is_error ? `✗ Finished with error (${p.subtype})` : "✓ Done"} · {p.num_turns} turns{cost}
          </div>
          {showText && <Markdown>{p.result}</Markdown>}
        </div>
      );
    }
    case "error":
      return <div className="ev-error">{p.message}</div>;
    case "status":
      return <div className="ev-system">{p.message}</div>;
    default:
      return null;
  }
}

const MERCURY_TOOL_PREFIX = "mcp__mercury__";
const TASK_TOOL_LABELS: Record<string, string> = {
  list_tasks: "List tasks",
  create_task: "Create task",
  update_task: "Update task",
};
const DOC_TOOL_LABELS: Record<string, string> = {
  browse_docs: "Browse docs",
  move_docs: "File docs",
  create_collection: "Create collection",
  update_collection: "Update collection",
  delete_collection: "Delete collection",
  search_docs: "Search docs",
  list_docs: "List docs",
  read_doc: "Read doc",
  create_doc: "Create doc",
  update_doc: "Update doc",
  delete_doc: "Delete doc",
};
const SETTINGS_TOOL_LABELS: Record<string, string> = {
  get_settings: "Read settings",
  update_settings: "Change settings",
};

function isMercuryTool(name: string): boolean {
  return name?.startsWith(MERCURY_TOOL_PREFIX) ?? false;
}

function isTaskTool(name: string): boolean {
  return isMercuryTool(name) && name.slice(MERCURY_TOOL_PREFIX.length) in TASK_TOOL_LABELS;
}

function toolLabel(name: string): string {
  if (!isMercuryTool(name)) return name;
  const short = name.slice(MERCURY_TOOL_PREFIX.length);
  return TASK_TOOL_LABELS[short] ?? DOC_TOOL_LABELS[short] ?? SETTINGS_TOOL_LABELS[short] ?? name;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function taskToolSummary(input: Record<string, any>): string {
  const parts: string[] = [];
  // Agents name tasks by their project id (MER-12) or, sometimes, the bare number.
  if (input.task_id != null) parts.push(typeof input.task_id === "number" ? `#${input.task_id}` : String(input.task_id));
  if (input.title) parts.push(input.title);
  if (input.status) parts.push(`→ ${String(input.status).replace("_", " ")}`);
  else if (input.include_done) parts.push("(incl. done)");
  return parts.join(" ");
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function settingsToolSummary(input: Record<string, any>): string {
  const changes = input.changes && typeof input.changes === "object" ? input.changes : {};
  return Object.entries(changes)
    .map(([k, v]) => `${k} → ${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(", ");
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function docToolSummary(input: Record<string, any>): string {
  const parts: string[] = [];
  if (input.doc_id != null) parts.push(`doc ${input.doc_id}`);
  if (Array.isArray(input.doc_ids)) parts.push(`${input.doc_ids.length} doc(s)`);
  if (input.collection_id != null) parts.push(`collection ${input.collection_id}`);
  else if ("collection_id" in input && input.doc_ids) parts.push("→ unfiled");
  if (input.unfiled) parts.push("unfiled");
  if (input.name) parts.push(input.name);
  if (input.start_line != null) parts.push(`lines ${input.start_line}–${input.end_line ?? "end"}`);
  if (input.title) parts.push(input.title);
  if (input.query) parts.push(`“${input.query}”`);
  return parts.join(" ");
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function toolSummary(name: string, input: Record<string, any>): string {
  if (!input) return "";
  if (isTaskTool(name)) return taskToolSummary(input);
  if (name === `${MERCURY_TOOL_PREFIX}update_settings`) return settingsToolSummary(input);
  if (isMercuryTool(name)) return docToolSummary(input);
  const v = input.command ?? input.file_path ?? input.path ?? input.pattern ?? input.url ?? input.description ?? input.query;
  if (typeof v !== "string") return "";
  const s = v.length > 90 ? v.slice(0, 90) + "…" : v;
  return name === "Bash" ? `$ ${s}` : s;
}
