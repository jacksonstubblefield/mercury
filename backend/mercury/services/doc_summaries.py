"""Agent-facing doc summaries: written with a single tool-less Claude call, kept fresh in the background.

Agents pass a summary when they write a doc. When a person edits a doc (or an import
has none), its summary goes stale, and SummaryWorker rewrites it once the doc has
been left alone for a little while, so autosave doesn't trigger a call per keystroke.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any, Awaitable, Callable

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock
from sqlmodel import Session, select

from .. import config
from ..db import get_engine
from ..models import Doc, DocCollection, utcnow
from . import doc_collections, doc_store

log = logging.getLogger(__name__)

MAX_BODY_CHARS = 60_000
MAX_SUMMARY_CHARS = 800

SYSTEM_PROMPT = """You summarize a project document for other AI coding agents. They see your summary in \
search results and decide from it whether to read the document, so it must say what the document \
actually contains. Reply with only the summary: plain text, no heading, no preamble, 2-4 sentences, \
under 600 characters.

Include, when the document has them:
- What it is about and what it is for.
- The key decisions, findings or facts, with the concrete names, identifiers and values a searcher would look for.
- What is still open or unverified.
- Whether it says it is outdated, superseded, disproven or abandoned, and what replaced it.

Don't describe the document's formatting, and don't invent anything it doesn't say."""

COLLECTION_SYSTEM_PROMPT = """You summarize a collection (folder) of project documents for AI coding agents. They read your summary to get oriented in a large set of documents and to decide whether to look inside this collection, so say what it covers. Reply with only the summary: plain text, no heading, no preamble, 2-5 sentences, under 700 characters.

Say what the collection is about, the main topics and decisions its documents cover (with the concrete names a searcher would use), and what is still open. Mention if much of it is superseded or archived. Don't list every document, don't describe formatting, and don't invent anything that isn't in the input."""

Summarize = Callable[[str, str], Awaitable[str]]
SummarizeCollection = Callable[[str], Awaitable[str]]


def build_prompt(title: str, body: str) -> str:
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS] + "\n\n[document truncated]"
    return f"Title: {title}\n\n{body}\n\nWrite the summary."


async def generate(title: str, body: str, client_factory: Callable[[ClaudeAgentOptions], Any]) -> str:
    return await _one_shot(SYSTEM_PROMPT, build_prompt(title, body), client_factory)


async def generate_collection(contents: str, client_factory: Callable[[ClaudeAgentOptions], Any]) -> str:
    return await _one_shot(COLLECTION_SYSTEM_PROMPT, f"{contents}\n\nWrite the summary.", client_factory)


async def _one_shot(system: str, prompt: str, client_factory: Callable[[ClaudeAgentOptions], Any]) -> str:
    options = ClaudeAgentOptions(
        model=config.DOC_SUMMARY_MODEL,
        system_prompt=system,
        tools=[],
        max_turns=1,
        setting_sources=[],  # no settings, hooks, CLAUDE.md or memory
        strict_mcp_config=True,
    )
    texts: list[str] = []
    result: str | None = None
    async with client_factory(options) as client:
        await client.query(prompt)
        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                texts.extend(b.text for b in message.content if isinstance(b, TextBlock))
            elif isinstance(message, ResultMessage):
                if message.is_error:
                    raise RuntimeError(message.result or f"Claude couldn't write a summary ({message.subtype}).")
                result = message.result
    summary = " ".join((result or "\n".join(texts)).split())
    if not summary:
        raise RuntimeError("Claude returned an empty summary.")
    return summary[:MAX_SUMMARY_CHARS]


def store_summary(doc_id: int, expected_hash: str, summary: str) -> bool:
    """Save a summary if the doc still has the content it was written for."""
    with Session(get_engine()) as s:
        doc = s.get(Doc, doc_id)
        if doc is None or doc.content_hash != expected_hash:
            return False
        doc_store.save_doc(s, doc, summary=summary, touch=False)
        return True


def store_collection_summary(collection_id: int, expected_hash: str, summary: str) -> bool:
    """Save a collection summary if its contents are still what it was written from."""
    with Session(get_engine()) as s:
        c = s.get(DocCollection, collection_id)
        if c is None or doc_collections.contents_hash(s, c) != expected_hash:
            return False
        c.summary, c.summary_hash = summary, expected_hash
        s.add(c)
        s.commit()
        return True


class SummaryWorker:
    """Rewrites stale summaries: docs unchanged for `quiet_seconds`, then collections unchanged
    for `collection_quiet_seconds` whose docs' and subcollections' summaries are current."""

    def __init__(
        self,
        summarize: Summarize,
        summarize_collection: SummarizeCollection | None = None,
        *,
        quiet_seconds: float = 30,
        collection_quiet_seconds: float = 120,
        poll_seconds: float = 10,
        batch: int = 5,
        retry_after_seconds: float = 600,
    ):
        self.summarize = summarize
        self.summarize_collection = summarize_collection
        self.collection_quiet = collection_quiet_seconds
        self._stale_since: dict[tuple[int, str], float] = {}  # (collection id, contents hash) -> first seen
        self.quiet = timedelta(seconds=quiet_seconds)
        self.poll_seconds = poll_seconds
        self.batch = batch
        self.retry_after = retry_after_seconds
        self._failed: dict[tuple[int, str], float] = {}  # (doc id, content hash) -> when it failed
        self._task: asyncio.Task | None = None

    def due(self) -> list[tuple[int, str, str, str]]:
        cutoff = utcnow() - self.quiet
        now = time.monotonic()
        with Session(get_engine()) as s:
            docs = s.exec(
                select(Doc).where(doc_store.stale_summary_clause(), Doc.updated_at <= cutoff).order_by(Doc.updated_at)
            ).all()
            out = []
            for d in docs:
                failed_at = self._failed.get((d.id, d.content_hash))
                if failed_at is not None and now - failed_at < self.retry_after:
                    continue
                out.append((d.id, d.content_hash, d.title, d.body))
                if len(out) >= self.batch:
                    break
            return out

    def due_collections(self) -> list[tuple[int, str, str]]:
        """(id, contents hash, summary input) of collections whose summary should be rewritten now."""
        now = time.monotonic()
        out = []
        with Session(get_engine()) as s:
            cols = s.exec(select(DocCollection)).all()
            stale = {c.id: doc_collections.summary_stale(s, c) for c in cols}
            for c in cols:
                if not stale[c.id] or any(stale[k.id] for k in cols if k.parent_id == c.id):
                    continue  # current, or waiting for a subcollection's summary
                h = doc_collections.contents_hash(s, c)
                first = self._stale_since.setdefault((c.id, h), now)
                failed_at = self._failed.get((-c.id, h))
                if now - first < self.collection_quiet or (failed_at is not None and now - failed_at < self.retry_after):
                    continue
                text_, ready = doc_collections.summary_input(s, c)
                if ready:
                    out.append((c.id, h, text_))
                if len(out) >= self.batch:
                    break
        return out

    async def run_once(self) -> int:
        """Summarize one batch of due docs, then of due collections. Returns how many summaries were saved."""
        saved = await self._docs_once()
        if self.summarize_collection is not None:
            for cid, h, contents in self.due_collections():
                try:
                    summary = await self.summarize_collection(contents)
                except Exception:
                    log.warning("Couldn't summarize collection %s", cid, exc_info=True)
                    self._failed[(-cid, h)] = time.monotonic()  # negative ids: collections
                    continue
                if store_collection_summary(cid, h, summary):
                    saved += 1
        return saved

    async def _docs_once(self) -> int:
        saved = 0
        for doc_id, h, title, body in self.due():
            try:
                summary = await self.summarize(title, body)
            except Exception:
                log.warning("Couldn't summarize doc %s", doc_id, exc_info=True)
                self._failed[(doc_id, h)] = time.monotonic()
                continue
            if store_summary(doc_id, h, summary):
                saved += 1
        return saved

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:
                log.warning("Summary worker pass failed", exc_info=True)
            await asyncio.sleep(self.poll_seconds)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
