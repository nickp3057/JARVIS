"""Shared logging setup for the JARVIS assistant.

Provides a single configured logger that writes to both the console and a
rotating log file at the project root. Call ``get_logger(__name__)`` from any
module to grab a child logger.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
_LOG_FILE = _PROJECT_ROOT / "jarvis.log"

_ROOT_LOGGER_NAME = "jarvis"
_CONFIGURED = False


def _configure_root_logger() -> logging.Logger:
    """Attach handlers to the root jarvis logger exactly once."""
    global _CONFIGURED

    logger = logging.getLogger(_ROOT_LOGGER_NAME)

    if _CONFIGURED:
        return logger

    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        _LOG_FILE,
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    _CONFIGURED = True
    return logger


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the shared ``jarvis`` namespace."""
    _configure_root_logger()

    clean_name = name.replace("__main__", "main")
    return logging.getLogger(f"{_ROOT_LOGGER_NAME}.{clean_name}")
