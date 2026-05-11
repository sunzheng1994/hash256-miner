"""Rich-friendly logging setup."""

from __future__ import annotations

import logging
import sys

from rich.logging import RichHandler


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, markup=True, show_path=False)],
        force=True,
    )
    logging.captureWarnings(True)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
