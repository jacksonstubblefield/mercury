# Mercury

A desktop project manager with Claude coding agents built in. You can track all your projects in one place and plan work on a kanban board. Each project keeps its own docs (specs, decisions, notes), and you can give a task to a Claude agent that works directly in the project's repository.

## Features

- **Projects** are linked to a local git checkout and a GitHub repo, which is detected from the `origin` remote. The overview shows the branch, uncommitted changes, recent commits, task counts and agent spend.
- **Board**: a kanban board with Backlog, To do, In progress, Review and Done columns. Drag cards between columns, set priorities, and write task descriptions in Markdown.
  - **✨ Organize** starts an agent that tidies the board, so tasks can be jotted down without care. It merges duplicate tasks (the merged task keeps everything they said, and their agent sessions), gives vague tasks clear titles and descriptions, splits tasks that bundle unrelated work, and orders the Backlog and To do columns. It doesn't move tasks between columns or close them. Its summary appears on the board when it's done.
- **Docs**: the project's long-term memory, as Markdown docs with pinning and autosave. Built to scale to thousands of docs per project.
  - Docs are split at their headings (and into ~2,000-character sections) and searched two ways at once: by words (SQLite FTS5) and by meaning, with a local embedding model (`BAAI/bge-small-en-v1.5` via fastembed, CPU only, run inside the backend). Search returns the best-matching section of each doc.
  - The model is downloaded once (~65 MB) to `~/.mercury/models` the first time the backend starts. Sections are embedded in the background, and only changed sections are re-embedded. Until a section is embedded, or if the model can't load, search falls back to words only.
  - To measure search quality on your own docs, write a JSON list of `{"query": ..., "expect": <title text or doc id>}` and run `backend/.venv/Scripts/python -m mercury.search_eval cases.json -v` from `backend/` (point `MERCURY_DATABASE_URL` at a copy of the database if you like). It compares keyword-only, meaning-only and hybrid search.
  - Each doc has a short summary written for agents, shown with search results. Agents write one when they save a doc. When you edit a doc, Mercury rewrites its summary in the background with a small model, once you've stopped editing.
  - Docs are filed in collections: shallow folders, at most two levels. Each collection has a description of what belongs in it, and a summary Mercury writes in the background from its docs' and subcollections' summaries, so agents can get oriented before they know what to search for. Deleting a collection never deletes docs: they move up a level.
  - A doc's status can be current, draft, superseded (with a link to its replacement) or archived. Retired docs rank lower in search.
  - "Rules" docs go in full into every agent run's instructions; pinned docs are listed there with their summaries.
  - Agents browse, search, read, create and update docs on their own initiative, record lasting knowledge as they work, and file docs into collections (creating collections when a topic needs one). Each run's instructions include the top-level collection map. Deleting a doc asks for approval unless the run is on Full auto.
  - **Import…** copies a project's Claude Code memory (`~/.claude/projects/<repo>/memory`) into docs. The memory folder is never changed; there's no ongoing sync. A long memory that's split by section is filed in its own collection.
  - Inside Mercury runs, Claude Code's memory folder is read-only: a hook blocks writes to it, and agents save to docs instead.
  - **✨ Organize** starts an agent that tidies the docs: it files docs into collections (creating, renaming and folding collections together), combines docs that cover the same topic and marks the originals superseded, retitles vague docs and archives empty ones. It never deletes a doc.
- **Agents**: runs Claude agents through the [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk) in the project's folder.
  - The transcript streams live: messages, tool calls and their output.
  - A permission mode is chosen per run:

    | Mode | Behavior |
    |---|---|
    | *Plan only* | Read-only |
    | *Ask for everything* | Every edit and command waits for approval |
    | *Auto-edit* | File edits happen automatically; shell commands need approval |
    | *Full auto* | No approvals |

    New sessions start in your default mode (*Auto-edit* until you change it). Pick another mode in the New session form and click **Make default** to change it. The default is saved in `~/.mercury/preferences.json`. A project can set its own default in **Settings**, and that overrides the app-wide one.

  - When a tool call needs permission, the run pauses and shows **Allow / Deny** buttons in the transcript.
  - You can link a run to a task. The agent then sees the task's description, the task moves to *In progress* when the run starts, and to *Review* when it succeeds.
  - Organizer runs (the **✨ Organize** buttons) only use Mercury's task and doc tools, plus read-only access to the repo. They never edit files, run commands or ask for approval, and they work in projects without a repo folder too.
  - **Follow-ups** continue the same Claude session. Each run records its cost and number of turns, and you can set an optional budget cap ($) per run.
- **Source Control**: a VS Code-style view of the project's git checkout.
  - Staged changes, unstaged changes, untracked files and merge conflicts are listed, with stage, unstage and discard on each file or group. Click a file to see its diff.
  - Commit with a message, or use **Commit & Push**. If nothing is staged, the commit includes every change.
  - **✨ Generate** asks Claude to write the commit message from the staged diff, or from all changes when nothing is staged.
  - Fetch, Pull and Push, with ahead/behind counts. A branch with no upstream gets **Publish branch**, which pushes it and sets the upstream.
  - Push and pull use your existing git credentials. They fail instead of hanging when git would ask for a password in a terminal.
- **GitHub**: lists open PRs and issues. **Sync issues → board** creates a task for each open issue and marks the task done when its issue closes. Mercury reads GitHub through the `gh` CLI, so it uses your existing `gh auth login`.

## Architecture

```
desktop/    Electron shell. Starts the backend, opens the window, provides the folder picker.
frontend/   React + TypeScript + Vite UI.
backend/    Python FastAPI + SQLite (SQLModel) + claude-agent-sdk.
  mercury/
    app.py                    app factory; serves frontend/dist when it has been built
    models.py                 tables: Project, Task, Doc, AgentRun, AgentEvent
    routers/                  REST + WebSocket endpoints under /api
    services/agent_runner.py  runs agents, stores events, handles approvals, streams to the UI
    services/mercury_tools.py  task and doc tools that agents call (in-process MCP server)
    services/doc_store.py     saves, splits, indexes and searches docs (every doc write goes through it)
    services/doc_summaries.py  agent-facing doc and collection summaries, and the background worker that refreshes them
    services/doc_collections.py  doc collections: nesting rules, moving docs, summary inputs
    services/agent_memory.py  docs section of run instructions; hook keeping Claude Code memory read-only
    services/memory_import.py  imports Claude Code memory into docs
    services/embeddings.py    local embedding model, background indexer, vector search
    search_eval.py            measures doc search quality against known-answer questions
    services/gitinfo.py       git status and log for the overview
    services/source_control.py  staging, diffs, commit, push, pull for the Source Control tab
    services/commit_message.py  writes commit messages with a single Claude call (no tools)
    services/github.py        issues and PRs through the gh CLI
    security.py               local-only Host/Origin guard
scripts/    setup and test helpers (Node, cross-platform)
```

- **Data** goes in `~/.mercury/mercury.db`. Override the location with `MERCURY_DATA_DIR`.
- **Agent runs**: each run is an asyncio task inside the backend. Every message is saved as an `AgentEvent` and pushed over `WS /api/runs/{id}/stream`. A UI that reconnects gets the full history replayed.
- **Security**: the API listens only on `127.0.0.1`. It rejects any request whose `Origin` is not a known Mercury origin, so a web page open in your browser can't start agents on your machine.

## Requirements

- Python 3.10+
- Node 20+
- git
- `gh` (GitHub CLI, optional; needed for the GitHub tab)
- Claude credentials for agents. Either be logged in to Claude Code (`claude` then `/login`) or set `ANTHROPIC_API_KEY`.

## Getting started

```bash
npm run setup    # root deps + Electron, backend/.venv with the backend, frontend deps
npm run dev      # Vite dev server (hot reload) + Electron, backend on :8765
```

Other commands:

```bash
npm start        # build the UI, then run Electron against the built files
npm test         # backend pytest + frontend typecheck
```

To run the backend without Electron, use `backend/.venv/Scripts/python -m mercury` and open http://127.0.0.1:8765 after `npm run build`.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `MERCURY_DATA_DIR` | `~/.mercury` | Folder for the database |
| `MERCURY_PORT` | `8765` (dev) / random free port (app) | Backend port |
| `MERCURY_AGENT_MODEL` | `claude-opus-5-5` | Default model for agent runs |
| `MERCURY_COMMIT_MESSAGE_MODEL` | `claude-haiku-5-5` | Model that writes commit messages |
| `MERCURY_DOC_SUMMARY_MODEL` | `claude-haiku-5-5` | Model that writes doc summaries |
| `MERCURY_DOC_SUMMARIES` | `1` | Set to `0` to stop writing doc summaries in the background |
| `MERCURY_EMBEDDINGS` | `1` | Set to `0` for word-only doc search (no local model) |
| `MERCURY_EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | fastembed model for meaning search; changing it re-embeds in the background |
| `MERCURY_EMBEDDING_THREADS` | `4` | CPU threads for embedding |
| `MERCURY_PYTHON` | `backend/.venv` python | Python interpreter Electron uses to start the backend |
| `MERCURY_ALLOWED_ORIGINS` | — | Extra allowed origins, comma-separated |

## Roadmap ideas

- Package the app as an installer (electron-builder, with the backend bundled by PyInstaller)
- Run each agent in its own git worktree so parallel runs don't collide, and add an "open PR from run" button
- Show the agent's diff in the run view
- Search across all projects, and a "today" view of in-progress work
- Agent-written summaries of project status, saved to docs
