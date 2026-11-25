"""Doc collections: the rules, the API, agents' tools, collection summaries and the run instructions."""

from __future__ import annotations

import asyncio
import json

from sqlmodel import Session


def _call(tools, tool_name, **args):
    handler = next(t for t in tools if t.name == tool_name).handler
    result = asyncio.run(handler(args))
    text = result["content"][0]["text"]
    return (text, True) if result.get("is_error") else (json.loads(text), False)


def _project(client, name="Demo"):
    return client.post("/api/projects", json={"name": name}).json()


def _doc(client, pid, title, body="", **extra):
    r = client.post(f"/api/projects/{pid}/docs", json={"title": title, "body": body, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _col(client, pid, name, **extra):
    return client.post(f"/api/projects/{pid}/collections", json={"name": name, **extra})


def _tools(pid):
    from mercury.services.mercury_tools import build_doc_tools

    return build_doc_tools(pid)


def test_collection_rules(client):
    p = _project(client)
    other = _project(client, "Other")
    top = _col(client, p["id"], "Design").json()
    child = _col(client, p["id"], "Storage", parent_id=top["id"]).json()
    assert child["parent_id"] == top["id"]

    assert _col(client, p["id"], "Deep", parent_id=child["id"]).status_code == 422  # three levels
    assert _col(client, p["id"], "design").status_code == 422  # same name among siblings
    assert _col(client, p["id"], "Storage").status_code == 201  # same name at another level is fine
    assert _col(client, p["id"], "  ").status_code == 422
    assert _col(client, other["id"], "X", parent_id=top["id"]).status_code == 422  # parent in another project

    lone = _col(client, p["id"], "Lone").json()
    assert client.patch(f"/api/collections/{top['id']}", json={"parent_id": lone["id"]}).status_code == 422  # has children
    r = client.patch(f"/api/collections/{lone['id']}", json={"name": "Solo", "parent_id": top["id"], "description": "misc"})
    assert r.status_code == 200 and r.json()["name"] == "Solo" and r.json()["parent_id"] == top["id"]


def test_moving_and_deleting_collections_never_deletes_docs(client):
    p = _project(client)
    top = _col(client, p["id"], "Design").json()
    child = _col(client, p["id"], "Storage", parent_id=top["id"]).json()
    a = _doc(client, p["id"], "A", "alpha", collection_id=child["id"])
    b = _doc(client, p["id"], "B", "beta")
    before = client.get(f"/api/docs/{b['id']}").json()["updated_at"]

    assert client.post(f"/api/projects/{p['id']}/docs/move", json={"doc_ids": [b["id"]], "collection_id": top["id"]}).json() == {"moved": 1}
    moved = client.get(f"/api/docs/{b['id']}").json()
    assert moved["collection_id"] == top["id"] and moved["updated_at"] == before  # filing isn't an edit
    assert client.post(f"/api/projects/{p['id']}/docs/move", json={"doc_ids": [b["id"], 999], "collection_id": None}).status_code == 422

    counts = {c["name"]: c["doc_count"] for c in client.get(f"/api/projects/{p['id']}/collections").json()}
    assert counts == {"Design": 1, "Storage": 1}
    listed = {d["id"]: d["collection_id"] for d in client.get(f"/api/projects/{p['id']}/docs").json()}
    assert listed == {a["id"]: child["id"], b["id"]: top["id"]}

    assert client.delete(f"/api/collections/{child['id']}").status_code == 204
    assert client.get(f"/api/docs/{a['id']}").json()["collection_id"] == top["id"]  # moved up a level
    assert client.delete(f"/api/collections/{top['id']}").status_code == 204
    assert {d["collection_id"] for d in client.get(f"/api/projects/{p['id']}/docs").json()} == {None}  # unfiled


def test_search_can_be_scoped_to_a_collection(client):
    p = _project(client)
    top = _col(client, p["id"], "Design").json()
    child = _col(client, p["id"], "Storage", parent_id=top["id"]).json()
    _doc(client, p["id"], "Cache in design", "cache notes", collection_id=child["id"])
    _doc(client, p["id"], "Cache elsewhere", "cache notes")

    hits = client.get(f"/api/projects/{p['id']}/docs/search", params={"q": "cache", "collection_id": top["id"]}).json()
    assert [h["title"] for h in hits] == ["Cache in design"] and hits[0]["collection"] == "Design › Storage"
    assert len(client.get(f"/api/projects/{p['id']}/docs/search", params={"q": "cache"}).json()) == 2


def test_agent_collection_tools(client):
    p = _project(client)
    other = _project(client, "Other")
    foreign = _col(client, other["id"], "Theirs").json()
    tools = _tools(p["id"])

    design, err = _call(tools, "create_collection", name="Design", description="How the app is built")
    assert not err
    storage, _ = _call(tools, "create_collection", name="Storage", parent_id=design["id"])
    assert storage["name"] == "Design › Storage"
    assert _call(tools, "create_collection", name="Deeper", parent_id=storage["id"])[1]

    doc, err = _call(tools, "create_doc", title="SQLite schema", body="tables", summary="The tables.", collection_id=storage["id"])
    assert not err and doc["collection"] == "Design › Storage" and "note" not in doc
    assert _call(tools, "create_doc", title="X", collection_id=foreign["id"])[1]

    # An unfiled new doc gets told where similar docs live.
    loose, _ = _call(tools, "create_doc", title="SQLite indexes", body="indexes on tables", summary="Indexes for the tables.")
    assert "Design › Storage" in loose["note"] and str(storage["id"]) in loose["note"]

    top, _ = _call(tools, "browse_docs")
    assert top["total_docs"] == 2 and top["unfiled_docs"] == 1
    assert top["collections"][0]["name"] == "Design" and top["collections"][0]["docs"] == 1
    assert top["collections"][0]["subcollections"] == [{"id": storage["id"], "name": "Storage", "docs": 1}]
    inside, _ = _call(tools, "browse_docs", collection_id=storage["id"])
    assert inside["name"] == "Design › Storage" and [d["title"] for d in inside["docs"]] == ["SQLite schema"]
    unfiled, _ = _call(tools, "browse_docs", unfiled=True)
    assert [d["title"] for d in unfiled["docs"]] == ["SQLite indexes"]
    assert _call(tools, "browse_docs", collection_id=foreign["id"])[1]

    assert _call(tools, "move_docs", doc_ids=[loose["id"]], collection_id=storage["id"])[0] == {"moved": 1, "to": "Design › Storage"}
    assert _call(tools, "move_docs", doc_ids=[loose["id"]], collection_id=foreign["id"])[1]
    scoped, _ = _call(tools, "search_docs", query="tables", collection_id=design["id"])
    assert {r["title"] for r in scoped["results"]} == {"SQLite schema", "SQLite indexes"}
    assert _call(tools, "search_docs", query="tables", collection_id=foreign["id"])[1]

    renamed, err = _call(tools, "update_collection", collection_id=storage["id"], name="Database", parent_id=None)
    assert not err and renamed["name"] == "Database"
    unfiled_doc, _ = _call(tools, "update_doc", doc_id=doc["id"], collection_id=None)
    assert unfiled_doc["collection_id"] is None

    gone, _ = _call(tools, "delete_collection", collection_id=renamed["id"])
    assert gone["docs_moved_to"] is None
    stored = {d["title"]: d for d in client.get(f"/api/projects/{p['id']}/docs").json()}
    assert stored["SQLite indexes"]["collection_id"] is None


def test_collection_summaries_wait_for_quiet_and_fresh_contents(client):
    from mercury.db import get_engine
    from mercury.models import DocCollection
    from mercury.services import doc_collections
    from mercury.services.doc_summaries import SummaryWorker

    p = _project(client)
    top = _col(client, p["id"], "Design").json()
    child = _col(client, p["id"], "Storage", parent_id=top["id"]).json()
    _doc(client, p["id"], "Schema", "tables", summary="The tables.", collection_id=child["id"])
    pending = _doc(client, p["id"], "Indexes", "indexes", collection_id=child["id"])  # summary still to be written

    seen: list[str] = []

    async def doc_summary(title, body):
        return f"About {title}."

    async def collection_summary(contents):
        seen.append(contents)
        return "Summary: " + contents.splitlines()[0]

    patient = SummaryWorker(doc_summary, collection_summary, quiet_seconds=3600, collection_quiet_seconds=0)
    asyncio.run(patient.run_once())
    assert seen == []  # a doc summary in Storage is stale, so Storage waits, and Design waits for Storage

    worker = SummaryWorker(doc_summary, collection_summary, quiet_seconds=0, collection_quiet_seconds=0)
    asyncio.run(worker.run_once())  # doc summary, then Storage
    asyncio.run(worker.run_once())  # then Design, from Storage's summary
    assert seen[0].startswith("Collection: Storage") and "About Indexes." in seen[0]
    assert seen[1].startswith("Collection: Design") and "Summary: Collection: Storage" in seen[1]
    cols = {c["name"]: c for c in client.get(f"/api/projects/{p['id']}/collections").json()}
    assert cols["Storage"]["summary"] == "Summary: Collection: Storage" and not cols["Storage"]["summary_stale"]

    client.patch(f"/api/docs/{pending['id']}", json={"status": "archived"})
    cols = {c["name"]: c for c in client.get(f"/api/projects/{p['id']}/collections").json()}
    assert cols["Storage"]["summary_stale"]

    hand = client.patch(f"/api/collections/{child['id']}", json={"summary": "Written by hand."}).json()
    assert hand["summary"] == "Written by hand." and not hand["summary_stale"]

    slow = SummaryWorker(doc_summary, collection_summary, quiet_seconds=0, collection_quiet_seconds=3600)
    with Session(get_engine()) as s:
        c = s.get(DocCollection, top["id"])
        assert doc_collections.summary_stale(s, c)  # Storage's summary changed
    assert slow.due_collections() == []  # not quiet long enough yet


def test_instructions_show_the_collection_map(client):
    from mercury.services.agent_memory import docs_instructions

    p = _project(client)
    top = _col(client, p["id"], "Design", description="How the app is built").json()
    _col(client, p["id"], "Storage", parent_id=top["id"])
    _doc(client, p["id"], "A", "x", collection_id=top["id"])
    _doc(client, p["id"], "B", "y")
    text = docs_instructions(p["id"])
    assert f"- Design (collection {top['id']}, 1 docs): How the app is built" in text
    assert "Subcollections: Storage" in text and "Unfiled: 1 docs" in text and "browse_docs" in text
