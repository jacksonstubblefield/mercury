"""Run the Mercury backend: `python -m mercury`."""

from __future__ import annotations

import logging

import uvicorn

from . import config
from .app import create_app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run(create_app(), host=config.host(), port=config.port(), log_level="info")


if __name__ == "__main__":
    main()
