"""Measure doc search quality on real docs: python -m mercury.search_eval CASES.json [--project ID]

CASES.json is a list of {"query": "...", "expect": "..."}, where "expect" is a doc id or
text that the expected doc's title contains (case-insensitive). The tool embeds any
sections still missing vectors, then reports, for keyword-only, meaning-only and
hybrid search, how often the expected doc ranks first (hit@1), in the top 3 and in
the top 5, plus the mean reciprocal rank (MRR).

Uses the database in MERCURY_DATABASE_URL (default ~/.mercury/mercury.db) and the
model in MERCURY_EMBEDDING_MODEL. Point it at a copy to compare models safely.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from sqlmodel import Session, select

from . import config
from .db import get_engine, init_db
from .models import Project
from .services import doc_store, embeddings

MODES = ("keyword", "meaning", "hybrid")


def _rank(hits, expect) -> int | None:
    for i, h in enumerate(hits, 1):
        if (isinstance(expect, int) and h.doc.id == expect) or (
            isinstance(expect, str) and expect.lower() in h.doc.title.lower()
        ):
            return i
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mercury.search_eval", description=__doc__.split("\n")[0])
    parser.add_argument("cases", type=Path)
    parser.add_argument("--project", type=int, help="Project id (default: the only project, or the first)")
    parser.add_argument("--depth", type=int, default=10, help="Results to look through per query")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show each query's ranks and top hit")
    args = parser.parse_args(argv)

    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    init_db()
    embedder = embeddings.load_default()
    if embedder is None:
        print(f"Embedding model unavailable: {embeddings.status().error}", file=sys.stderr)
        return 1
    with Session(get_engine()) as s:
        first = select(Project).where(Project.scratch == False)  # noqa: E712
        project = s.get(Project, args.project) if args.project else s.exec(first).first()
        if project is None:
            print("No such project.", file=sys.stderr)
            return 1
        start, done = time.perf_counter(), 0
        while batch := embeddings.pending_sections(s, embedder.model, embeddings.BATCH):
            vectors = embedder.embed_passages([t for _, t in batch])
            done += embeddings.store_vectors(s, embedder.model, [(h, v) for (h, _), v in zip(batch, vectors)])
        if done:
            print(f"Embedded {done} sections in {time.perf_counter() - start:.1f}s")
        sections, embedded = embeddings.counts(s, project.id, embedder.model)
        print(f"Project {project.id} ({project.name}): {sections} sections, model {config.EMBEDDING_MODEL}\n")

        ranks: dict[str, list[int | None]] = {m: [] for m in MODES}
        for case in cases:
            row = []
            for mode in MODES:
                hits = doc_store.search(s, project.id, case["query"], limit=args.depth, include_archived=True, mode=mode)
                r = _rank(hits, case["expect"])
                ranks[mode].append(r)
                row.append(f"{mode}={r or '-'}")
            if args.verbose:
                print(f"{case['query'][:60]:60}  {'  '.join(row)}")
        if args.verbose:
            print()

    n = len(cases)
    print(f"{'mode':8} {'hit@1':>6} {'top3':>6} {'top5':>6} {'MRR':>6}   ({n} queries)")
    for mode in MODES:
        rs = ranks[mode]
        at = lambda k: sum(1 for r in rs if r and r <= k) / n  # noqa: E731
        mrr = sum(1 / r for r in rs if r) / n
        print(f"{mode:8} {at(1):6.0%} {at(3):6.0%} {at(5):6.0%} {mrr:6.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
