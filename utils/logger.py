"""Centralized logging setup."""

import logging
import sys
from pathlib import Path

_initialized = False


def setup_logger(log_path: Path, level: int = logging.INFO) -> logging.Logger:
    """Configure and return the application logger."""
    global _initialized
    logger = logging.getLogger("ai_intel")

    if _initialized:
        return logger

    logger.setLevel(level)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    # File handler
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(str(log_path), encoding="utf-8")
    fh.setLevel(level)
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    _initialized = True
    return logger


def get_logger() -> logging.Logger:
    """Get the application logger (must call setup_logger first)."""
    return logging.getLogger("ai_intel")
