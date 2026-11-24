# Mercury — notes for coding agents

Mercury is a desktop project manager with Claude coding agents built in. It has three parts: an Electron shell (`desktop/`), a React/Vite UI (`frontend/`), and a Python FastAPI backend (`backend/`).

## Commands

- `npm run setup`: one-time install. It creates `backend/.venv`, installs the backend, installs frontend deps and the Electron binary.
- `npm run dev`: Vite on :5173 plus Electron. Electron starts the backend on :8765, and Vite proxies `/api` to it.
- `npm test`: backend pytest, then frontend `tsc --noEmit`. Run it before you finish.
- `node scripts/test-backend.cjs -k <expr>`: a subset of backend tests.

## Conventions

- **Backend**
  - The SQLModel tables and API schemas both live in `backend/mercury/models.py`.
  - There is no migration tool yet, and tables are created with `create_all`. If you add a column, the user's existing `~/.mercury/mercury.db` won't get it, so add it to `_ADDED_COLUMNS` in `db.py`.
  - Endpoints that call `RunManager.start` must be `async def`, because they need the running event loop.
- **Agent runs**
  - `serialize_message` in `services/agent_runner.py` maps Agent SDK messages to `AgentEvent` kinds.
  - If you add a kind, also render it in `EventItem` in `frontend/src/components/AgentsTab.tsx`.
  - Check Agent SDK APIs against the installed `claude_agent_sdk` package, not from memory.
- **Docs**
  - Save and delete docs only through `services/doc_store.py` (`save_doc` and `delete_doc`), so each doc's sections and search index stay in step with it.
  - Create, change and delete collections through `services/doc_collections.py`, which enforces the rules: at most two levels, unique names among siblings, and deleting moves docs up instead of deleting them.
  - Claude Code's memory folder is read-only in agent runs (`services/agent_memory.py`). Don't loosen that hook.
  - If you change how docs are split or indexed, bump `INDEX_VERSION` in `doc_store.py` so existing docs are reindexed. Check search changes with `python -m mercury.search_eval`; tests use `FakeEmbedder` (`tests/test_docs.py`) and must never download a model.
- **Frontend**: the TypeScript types in `frontend/src/types.ts` mirror the backend models. Keep them in sync.
- **Tests**: use `FakeClient` (`backend/tests/test_agent_runs.py`) for agent flows. Tests must never call the real Claude API.
- **Security**: don't loosen `security.py`'s Host/Origin checks. The API can execute code on the user's machine.
