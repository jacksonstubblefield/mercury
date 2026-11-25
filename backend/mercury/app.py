"""FastAPI application factory."""

from __future__ import annotations

import shutil
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import __version__, config
from .db import init_db
from .routers import agents, appearance, docs, github, preferences, projects, source_control, tasks, usage
from .security import LocalOnlyMiddleware
from .services import doc_summaries, embeddings
from .services import preferences as preferences_store
from .services.agent_runner import RunManager


def create_app(run_manager: RunManager | None = None) -> FastAPI:
    runs = run_manager or RunManager()

    summaries = doc_summaries.SummaryWorker(
        lambda title, body: doc_summaries.generate(title, body, runs.client_factory),
        lambda contents: doc_summaries.generate_collection(contents, runs.client_factory),
    )

    indexer = embeddings.EmbeddingWorker()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db()
        runs.recover_orphans()
        if config.doc_summaries_enabled():
            summaries.start()
        if config.embeddings_enabled():
            indexer.start()  # loads (first time: downloads) the model, then keeps sections embedded
        yield
        await indexer.stop()
        await summaries.stop()
        await runs.shutdown()

    app = FastAPI(title="Mercury", version=__version__, lifespan=lifespan)
    app.state.runs = runs
    app.add_middleware(LocalOnlyMiddleware, port=config.port())

    @app.get("/api/health")
    def health():
        return {
            "ok": True,
            "version": __version__,
            "default_model": preferences_store.load().default_model.value,
            "gh_installed": shutil.which("gh") is not None,
            "git_installed": shutil.which("git") is not None,
        }

    for module in (projects, tasks, docs, github, source_control, agents, usage, appearance, preferences):
        app.include_router(module.router)

    dist = config.frontend_dist()
    if dist is not None:
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")

    return app
