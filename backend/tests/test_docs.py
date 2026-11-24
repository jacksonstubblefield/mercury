"""Docs as Mercury's memory: sections, search, agent tools, summaries, memory import and the memory guard."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from sqlmodel import Session

from mercury.services import doc_store


def _call(tools, name, **args):
    handler = next(t for t in tools if t.name == name).handler
    result = asyncio.run(handler(args))
    text = result["content"][0]["text"]
    return (text, True) if result.get("is_error") else (json.loads(text), False)


def _project(client, name="Demo", repo_path=None):
    return client.post("/api/projects", json={"name": name, "repo_path": repo_path}).json()


def _doc(client, pid, title, body="", **extra):
    r = client.post(f"/api/projects/{pid}/docs", json={"title": title, "body": body, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _search(client, pid, q, **params):
    return client.get(f"/api/projects/{pid}/docs/search", params={"q": q, **params}).json()


# --------------------------------------------------------------------------- sections


def test_split_sections_follows_headings_and_skips_code_fences():
    body = "Intro line\n\n# Design\nText\n## Storage\nSQLite\n```\n# not a heading\n```\n## API\nREST\n# Other\nEnd"
    secs = doc_store.split_sections(body)
    assert [s.heading for s in secs] == ["", "Design", "Design › Storage", "Design › API", "Other"]
    storage = secs[2]
    assert "# not a heading" in storage.text
    lines = body.split("\n")
    assert lines[storage.start_line - 1] == "## Storage" and lines[storage.end_line - 1] == "```"


def test_split_sections_splits_long_sections_and_handles_empty_bodies():
    para = "word " * 150
    body = "# Big\n" + "\n\n".join([para] * 20)
    secs = doc_store.split_sections(body)
    assert len(secs) > 1 and secs[0].heading == "Big" and secs[1].heading == "Big (cont.)"
    assert all(len(s.text) <= doc_store.MAX_SECTION_CHARS + len(para) for s in secs)
    assert secs[-1].end_line == body.count("\n") + 1
    assert len(doc_store.split_sections("")) == 1  # title and summary stay searchable


# --------------------------------------------------------------------------- search


def test_search_ranks_titles_stems_words_and_scopes_to_project(client):
    p = _project(client)
    other = _project(client, "Other")
    auth = _doc(client, p["id"], "Token refresh design", "## Storage\nTokens live in the OS keychain.\n## Expiry\nSessions expire after 30 days.")
    _doc(client, p["id"], "Release checklist", "Mention the token refresh change in the changelog.")
    _doc(client, other["id"], "Token refresh design", "Not this project.")

    hits = _search(client, p["id"], "token refresh")
    assert [h["doc_id"] for h in hits][0] == auth["id"] and len(hits) == 2

    expiry = _search(client, p["id"], "session expiring")  # porter stemming
    assert expiry[0]["doc_id"] == auth["id"] and expiry[0]["section"] == "Expiry"
    assert expiry[0]["lines"] == [3, 4] and "«" in expiry[0]["snippet"]

    for weird in ['"', "AND OR NOT", "a*b:(c)-d", "the of"]:
        assert client.get(f"/api/projects/{p['id']}/docs/search", params={"q": weird}).status_code == 200


def test_search_ranks_superseded_low_and_hides_archived(client):
    p = _project(client)
    old = _doc(client, p["id"], "Cache plan", "Use redis for the cache layer cache cache.")
    new = _doc(client, p["id"], "Cache plan v2", "Use an in-process LRU cache.")
    gone = _doc(client, p["id"], "Cache notes", "cache cache cache cache")
    client.patch(f"/api/docs/{old['id']}", json={"superseded_by": new["id"]})
    client.patch(f"/api/docs/{gone['id']}", json={"status": "archived"})

    assert client.get(f"/api/docs/{old['id']}").json()["status"] == "superseded"
    ids = [h["doc_id"] for h in _search(client, p["id"], "cache plan", include_archived=False)]
    assert ids == [new["id"], old["id"]]
    assert gone["id"] in [h["doc_id"] for h in _search(client, p["id"], "cache", include_archived=True)]


def test_index_follows_edits_and_deletes(client):
    p = _project(client)
    a = _doc(client, p["id"], "Alpha", "mentions zebra")
    b = _doc(client, p["id"], "Beta", "replaced", superseded_by=None)
    client.patch(f"/api/docs/{b['id']}", json={"superseded_by": a["id"]})
    client.patch(f"/api/docs/{a['id']}", json={"body": "mentions giraffe"})
    assert _search(client, p["id"], "zebra") == []
    assert _search(client, p["id"], "giraffe")[0]["doc_id"] == a["id"]

    assert client.delete(f"/api/docs/{a['id']}").status_code == 204
    assert _search(client, p["id"], "giraffe") == []
    assert client.get(f"/api/docs/{b['id']}").json()["superseded_by"] is None

    assert client.patch(f"/api/docs/{b['id']}", json={"superseded_by": b["id"]}).status_code == 422
    assert "body" not in client.get(f"/api/projects/{p['id']}/docs").json()[0]  # list stays light


# --------------------------------------------------------------------------- agent tools


def _tools(pid):
    from mercury.services.mercury_tools import build_doc_tools

    return build_doc_tools(pid)


def test_doc_tools_create_search_read_update_delete(client):
    p = _project(client)
    other = _project(client, "Other")
    foreign = _doc(client, other["id"], "Not yours", "secret")
    tools = _tools(p["id"])

    assert _call(tools, "create_doc", title="  ")[1]
    msg, err = _call(tools, "create_doc", title="Design", body="# Design\nUse SQLite.")
    assert err and "summary" in msg
    created, err = _call(tools, "create_doc", title=" Storage design ", body="## DB\nUse SQLite.", summary="Picks SQLite.")
    assert not err and created["title"] == "Storage design" and created["origin"] == "agent" and not created["summary_stale"]
    doc_id = created["id"]

    msg, err = _call(tools, "create_doc", title="Storage design notes", body="x", summary="y")
    assert err and "similar titles" in msg and str(doc_id) in msg
    dup, err = _call(tools, "create_doc", title="Storage design notes", body="x", summary="y", confirm_new=True)
    assert not err

    results = _call(tools, "search_docs", query="sqlite")[0]["results"]
    assert results[0]["doc_id"] == doc_id and results[0]["summary"] == "Picks SQLite."
    assert _call(tools, "search_docs", query="secret")[0]["results"] == []  # other projects are invisible

    listed = _call(tools, "list_docs", limit=1)[0]
    assert listed["total"] == 2 and len(listed["docs"]) == 1

    read, _ = _call(tools, "read_doc", doc_id=doc_id)
    assert read["body"] == "## DB\nUse SQLite." and read["total_lines"] == 2

    updated, err = _call(tools, "update_doc", doc_id=doc_id, old_text="SQLite", new_text="Postgres")
    assert not err and updated["summary_stale"] and "note" in updated
    updated, _ = _call(tools, "update_doc", doc_id=doc_id, summary="Picks Postgres.")
    assert not updated["summary_stale"]
    assert _call(tools, "search_docs", query="postgres")[0]["results"][0]["doc_id"] == doc_id

    msg, err = _call(tools, "update_doc", doc_id=dup["id"], superseded_by=foreign["id"])
    assert err and "another doc in this project" in msg
    retired, err = _call(tools, "update_doc", doc_id=dup["id"], superseded_by=doc_id)
    assert not err and retired["status"] == "superseded" and retired["superseded_by"] == doc_id

    for name, extra in (("read_doc", {}), ("update_doc", {"title": "Mine"}), ("delete_doc", {})):
        msg, err = _call(tools, name, doc_id=foreign["id"], **extra)
        assert err and "isn't in this project" in msg

    assert _call(tools, "delete_doc", doc_id=doc_id)[0] == {"deleted": doc_id, "title": "Storage design"}
    assert client.get(f"/api/docs/{doc_id}").status_code == 404


def test_read_doc_pages_long_docs(client):
    p = _project(client)
    body = "\n".join(f"## Part {i}\n" + ("lorem ipsum " * 200) for i in range(20))
    doc = _doc(client, p["id"], "Long", body)
    tools = _tools(p["id"])

    whole, _ = _call(tools, "read_doc", doc_id=doc["id"])
    assert "body" not in whole and whole["outline"][0]["section"] == "Part 0" and "opening" in whole
    part = next(o for o in whole["outline"] if o["section"] == "Part 3")
    read, _ = _call(tools, "read_doc", doc_id=doc["id"], start_line=part["lines"][0], end_line=part["lines"][1])
    assert read["body"].startswith("## Part 3") and read["lines"] == part["lines"]
    assert "body" not in _call(tools, "read_doc", doc_id=doc["id"], outline=True)[0]


# --------------------------------------------------------------------------- summaries


def test_summary_worker_waits_for_quiet_docs_and_skips_changed_ones(client):
    from mercury.db import get_engine
    from mercury.models import Doc
    from mercury.services.doc_summaries import SummaryWorker

    p = _project(client)
    a = _doc(client, p["id"], "A", "alpha text")
    b = _doc(client, p["id"], "B", "beta text")
    _doc(client, p["id"], "Empty")  # nothing to summarize
    _doc(client, p["id"], "Given", "has one", summary="Written by hand.")
    assert client.get(f"/api/docs/{a['id']}").json()["summary_stale"]

    calls = []

    async def fake(title, body):
        calls.append(title)
        if title == "B":  # the doc changes while its summary is being written
            with Session(get_engine()) as s:
                doc = s.get(Doc, b["id"])
                doc.body = "beta, edited"
                doc_store.save_doc(s, doc)
        return f"Summary of {title}."

    assert asyncio.run(SummaryWorker(fake, quiet_seconds=60).run_once()) == 0  # just edited: wait
    assert asyncio.run(SummaryWorker(fake, quiet_seconds=0).run_once()) == 1
    assert sorted(calls) == ["A", "B"]
    got = client.get(f"/api/docs/{a['id']}").json()
    assert got["summary"] == "Summary of A." and not got["summary_stale"]
    assert client.get(f"/api/docs/{b['id']}").json()["summary_stale"]  # written for old content: dropped
    assert _search(client, p["id"], "summary")[0]["doc_id"] == a["id"]  # summaries are indexed


def test_summary_worker_backs_off_after_failures(client):
    from mercury.services.doc_summaries import SummaryWorker

    p = _project(client)
    _doc(client, p["id"], "A", "alpha")
    attempts = []

    async def failing(title, body):
        attempts.append(title)
        raise RuntimeError("offline")

    worker = SummaryWorker(failing, quiet_seconds=0)
    asyncio.run(worker.run_once())
    asyncio.run(worker.run_once())
    assert attempts == ["A"]


# --------------------------------------------------------------------------- import from Claude Code memory


def _memory(tmp_path: Path, repo: Path, folder_name: str | None = None) -> Path:
    from mercury.services.memory_import import _sanitize

    name = folder_name or _sanitize(str(repo))
    mem = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / name / "memory"
    mem.mkdir(parents=True)
    (mem / "MEMORY.md").write_text(
        "- [Cache Plan](cache_plan.md) — the cache\n- [Scoped Tests](feedback_tests.md) — run world tests only\n"
        "- [Roadmap](roadmap.md) — everything\n",
        encoding="utf-8",
    )
    (mem / "cache_plan.md").write_text(
        '---\nname: cache-plan\ndescription: "Use an LRU cache; redis was dropped"\nmetadata:\n  type: project\n---\n\nLRU, 512 entries.\n',
        encoding="utf-8",
    )
    (mem / "feedback_tests.md").write_text(
        "---\nname: scoped-tests\ndescription: only world tests\nmetadata:\n  type: feedback\n---\n\nRun only `worlds/x/test`.\n",
        encoding="utf-8",
    )
    sections = "\n\n".join(f"## Phase {i}{' (disproven)' if i == 2 else ''}\n" + ("detail " * 2000) for i in range(4))
    (mem / "roadmap.md").write_text(
        f"---\nname: roadmap\ndescription: Full plan\ntype: project\n---\n\nOverview of the plan.\n\n{sections}\n", encoding="utf-8"
    )
    return mem


def test_memory_import_preview_apply_and_reimport(client, tmp_path):
    repo = tmp_path / "Repo-Dir"
    repo.mkdir()
    mem = _memory(tmp_path, repo, folder_name=None)
    p = _project(client, repo_path=str(repo).upper())  # folder lookup ignores case
    url = f"/api/projects/{p['id']}/docs/import/claude-memory"

    preview = client.get(url).json()
    assert Path(preview["memory_dir"]) == mem
    items = {i["key"]: i for i in preview["items"]}
    assert list(items)[0] == "claude-memory:rules" and items["claude-memory:rules"]["kind"] == "rules"
    assert items["claude-memory:cache_plan.md"]["title"] == "Cache Plan"
    assert items["claude-memory:cache_plan.md"]["flags"]  # "dropped" in the description
    split = [k for k in items if k.startswith("claude-memory:roadmap.md#")]
    assert len(split) == 4 and items["claude-memory:roadmap.md"]["pinned"]
    assert items["claude-memory:roadmap.md#phase-2-disproven"]["flags"]
    assert all(i["action"] == "create" and i["include"] for i in items.values())

    result = client.post(url, json={"choices": {"claude-memory:roadmap.md#phase-0": {"include": False},
                                                "claude-memory:roadmap.md#phase-2-disproven": {"status": "superseded"}}}).json()
    assert result == {"created": 6, "updated": 0, "skipped": 1}

    docs = {d["title"]: d for d in client.get(f"/api/projects/{p['id']}/docs").json()}
    cache = client.get(f"/api/docs/{docs['Cache Plan']['id']}").json()
    assert cache["origin"] == "import" and cache["summary"] == "Use an LRU cache; redis was dropped" and not cache["summary_stale"]
    assert cache["body"] == "LRU, 512 entries."
    rules = client.get(f"/api/docs/{docs['Agent working rules']['id']}").json()
    assert rules["kind"] == "rules" and "## Scoped Tests" in rules["body"]
    assert docs["Roadmap: Phase 2 (disproven)"]["status"] == "superseded"
    assert client.get(f"/api/docs/{docs['Roadmap: Phase 1']['id']}").json()["summary_stale"]  # the worker writes it
    assert _search(client, p["id"], "lru")[0]["title"] == "Cache Plan"
    # A split memory is filed in a collection named after it; other docs stay unfiled.
    cols = client.get(f"/api/projects/{p['id']}/collections").json()
    assert [(c["name"], c["doc_count"]) for c in cols] == [("Roadmap", 4)]
    assert docs["Roadmap"]["collection_id"] == cols[0]["id"] and docs["Cache Plan"]["collection_id"] is None

    # Re-run: nothing changed, except what changed in memory, and Mercury edits win.
    (mem / "cache_plan.md").write_text("---\ndescription: LRU, now 1024\ntype: project\n---\nLRU, 1024 entries.\n", encoding="utf-8")
    client.patch(f"/api/docs/{rules['id']}", json={"body": rules["body"] + "\n\nEdited in Mercury."})
    (mem / "feedback_tests.md").write_text("---\ntype: feedback\n---\nChanged upstream.\n", encoding="utf-8")
    actions = {i["key"]: i["action"] for i in client.get(url).json()["items"]}
    assert actions["claude-memory:cache_plan.md"] == "update"
    assert actions["claude-memory:rules"] == "edited"
    assert actions["claude-memory:roadmap.md#phase-1"] == "unchanged"
    assert actions["claude-memory:roadmap.md#phase-0"] == "create"  # skipped last time
    result = client.post(url, json={"choices": {"claude-memory:roadmap.md#phase-0": {"include": False}}}).json()
    assert result["updated"] == 1 and result["created"] == 0
    client.post(url, json={"choices": {"claude-memory:roadmap.md#phase-0": {"include": True}}})
    cols = client.get(f"/api/projects/{p['id']}/collections").json()
    assert [(c["name"], c["doc_count"]) for c in cols] == [("Roadmap", 5)]  # reused, not duplicated
    assert client.get(f"/api/docs/{cache['id']}").json()["body"] == "LRU, 1024 entries."
    assert "Edited in Mercury." in client.get(f"/api/docs/{rules['id']}").json()["body"]

    assert (mem / "cache_plan.md").read_text(encoding="utf-8").startswith("---")  # memory is never modified
    assert client.delete(url).json() == {"deleted": 7}
    assert client.get(f"/api/projects/{p['id']}/collections").json() == []  # its empty collection goes too
    assert client.get(f"/api/projects/{p['id']}/docs").json() == []


def test_memory_import_folder_checks(client, tmp_path):
    p = _project(client, repo_path=str(tmp_path / "nowhere"))
    url = f"/api/projects/{p['id']}/docs/import/claude-memory"
    assert client.get(url).status_code == 404
    assert client.get(url, params={"memory_dir": str(tmp_path)}).status_code == 422  # outside ~/.claude/projects


# --------------------------------------------------------------------------- instructions and the memory guard


def test_docs_instructions_include_rules_and_pinned_docs(client):
    from mercury.services.agent_memory import docs_instructions

    p = _project(client)
    _doc(client, p["id"], "Rules", "Never touch prod.", kind="rules")
    old_rules = _doc(client, p["id"], "Old rules", "Use tabs.", kind="rules")
    client.patch(f"/api/docs/{old_rules['id']}", json={"status": "archived"})
    _doc(client, p["id"], "Overview", "x", pinned=True, summary="What the project is.")
    _doc(client, p["id"], "Unpinned", "y", summary="Hidden.")

    text = docs_instructions(p["id"])
    assert "Never touch prod." in text and "Use tabs." not in text
    assert "What the project is." in text and "Hidden." not in text
    assert "4 doc(s)" in text and "never write to it" in text


def test_memory_guard_blocks_writes_to_claude_code_memory(tmp_path, monkeypatch):
    from mercury.services.agent_memory import guard_memory_writes, memory_write_blocked

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    root = tmp_path / "claude-config" / "projects"
    mem_file = root / "c--src-app" / "memory" / "notes.md"
    assert memory_write_blocked("Write", {"file_path": str(mem_file)})
    assert memory_write_blocked("Edit", {"file_path": str(mem_file).upper()})
    assert memory_write_blocked("Write", {"file_path": "memory/notes.md"}, cwd=str(root / "c--src-app"))
    assert not memory_write_blocked("Write", {"file_path": str(root / "c--src-app" / "session.jsonl")})
    assert not memory_write_blocked("Write", {"file_path": str(tmp_path / "repo" / "memory" / "x.md")})
    assert not memory_write_blocked("Read", {"file_path": str(mem_file)})

    assert memory_write_blocked("Bash", {"command": f"echo hi > {mem_file}"})
    assert memory_write_blocked("Bash", {"command": "rm ~/.claude/projects/c--src-app/memory/notes.md"})
    assert memory_write_blocked("PowerShell", {"command": f"Set-Content -Path '{mem_file}' -Value hi"})
    assert not memory_write_blocked("Bash", {"command": f"cat {mem_file}"})
    assert not memory_write_blocked("Bash", {"command": "echo hi > notes.md"})

    denied = asyncio.run(guard_memory_writes({"tool_name": "Write", "tool_input": {"file_path": str(mem_file)}}, None, None))
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert asyncio.run(guard_memory_writes({"tool_name": "Write", "tool_input": {"file_path": "x.md"}}, None, None)) == {}


# --------------------------------------------------------------------------- meaning search (fake embedder)


class FakeEmbedder:
    """Deterministic stand-in for the local model: words map to concepts, so synonyms land close together."""

    model = "fake-embedder"
    CONCEPTS = {"car": "vehicle", "automobile": "vehicle", "vehicle": "vehicle", "truck": "vehicle",
                "cat": "pet", "kitten": "pet", "dog": "pet", "puppy": "pet"}
    DIM = 512

    def __init__(self):
        self.passages = 0

    def _vec(self, text):
        import hashlib

        import numpy as np

        v = np.zeros(self.DIM, dtype=np.float32)
        for w in doc_store.words(text):
            c = self.CONCEPTS.get(w, w)
            v[int(hashlib.md5(c.encode()).hexdigest(), 16) % self.DIM] += 1
        n = np.linalg.norm(v)
        return v / n if n else v

    def embed_passages(self, texts):
        import numpy as np

        self.passages += len(texts)
        return np.array([self._vec(t) for t in texts])

    def embed_query(self, query):
        return self._vec(query)


@pytest.fixture
def fake_embedder():
    from mercury.services import embeddings

    fake = FakeEmbedder()
    embeddings.set_embedder(fake)
    yield fake
    embeddings.set_embedder(None)


def _embed_all(fake):
    from mercury.services.embeddings import EmbeddingWorker

    worker = EmbeddingWorker()
    while asyncio.run(worker.run_once(fake)):
        pass


def test_meaning_search_finds_synonyms_keyword_search_misses(client, fake_embedder, monkeypatch):
    monkeypatch.setattr(doc_store, "MIN_VECTOR_SIMILARITY", 0.3)  # the fake's similarities run lower than bge's
    p = _project(client)
    cars = _doc(client, p["id"], "Fleet", "## Garage\nEvery car gets serviced in spring.")
    _doc(client, p["id"], "Pets", "## Home\nThe kitten sleeps all day.")
    _embed_all(fake_embedder)

    from mercury.db import get_engine

    with Session(get_engine()) as s:
        assert doc_store.search(s, p["id"], "automobile", mode="keyword") == []
        hits = doc_store.search(s, p["id"], "automobile")
        assert hits[0].doc.id == cars["id"] and hits[0].matched == "meaning" and hits[0].heading == "Garage"
        both = doc_store.search(s, p["id"], "car")[0]
        assert both.doc.id == cars["id"] and both.matched == "words+meaning"

    status = client.get(f"/api/projects/{p['id']}/docs/search-status").json()
    assert status == {"state": "ready", "model": "fake-embedder", "error": None, "sections": 2, "embedded": 2}


def test_embeddings_are_reused_for_unchanged_sections_and_collected(client, fake_embedder):
    from mercury.db import get_engine
    from mercury.models import Embedding
    from mercury.services import embeddings

    p = _project(client)
    doc = _doc(client, p["id"], "Notes", "## One\nfirst part\n## Two\nsecond part")
    _embed_all(fake_embedder)
    assert fake_embedder.passages == 2

    client.patch(f"/api/docs/{doc['id']}", json={"body": "## One\nfirst part\n## Two\nsecond part, edited"})
    _embed_all(fake_embedder)
    assert fake_embedder.passages == 3  # only the changed section

    hits = _search(client, p["id"], "edited")
    assert hits[0]["section"] == "Two"  # the vector cache picked up the new section ids

    with Session(get_engine()) as s:
        s.add(Embedding(model="old-model", text_hash="x", vector=b"\0" * 4))
        s.commit()
        assert embeddings.collect_garbage(s, fake_embedder.model) == 2  # the stale "Two" vector and the old model's
        assert len(s.exec(select_all(Embedding)).all()) == 2


def select_all(model):
    from sqlmodel import select

    return select(model)


def test_search_falls_back_to_keywords_without_an_embedder(client):
    from mercury.services import embeddings

    assert embeddings.ready_embedder() is None
    p = _project(client)
    _doc(client, p["id"], "Fleet", "Every car gets serviced.")
    hits = _search(client, p["id"], "car")
    assert hits[0]["matched"] == "words"
    assert _search(client, p["id"], "automobile") == []
    assert client.get(f"/api/projects/{p['id']}/docs/search-status").json()["state"] == "off"


def test_index_version_change_reindexes_every_doc(client):
    from mercury.db import get_engine, init_db
    from mercury.models import DocSection, MercuryMeta

    p = _project(client)
    _doc(client, p["id"], "A", "## One\ntext")
    with Session(get_engine()) as s:
        for sec in s.exec(select_all(DocSection)).all():
            sec.embed_hash = ""
            s.add(sec)
        s.get(MercuryMeta, "doc_index_version").value = "0"
        s.commit()
    init_db()
    with Session(get_engine()) as s:
        assert all(sec.embed_hash for sec in s.exec(select_all(DocSection)).all())
        assert s.get(MercuryMeta, "doc_index_version").value == doc_store.INDEX_VERSION
