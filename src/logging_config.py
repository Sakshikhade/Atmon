"""Centralized logging configuration for AAMAS.

Modules obtain a logger with ``logging.getLogger(__name__)`` and emit at the
appropriate level. The application entry point calls :func:`configure_logging`
once (honoring the ``--log-level`` CLI flag) to set up formatting and the level.

The live HUD telemetry line in ``main.py`` (the ``\\r`` single-line FPS/latency
readout) is intentionally kept as ``print()`` — it is an interactive display,
not a log stream.
"""

import logging

_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging once, with a concise timestamped format.

    Parameters
    ----------
    level:
        One of DEBUG, INFO, WARNING, ERROR, CRITICAL (case-insensitive).
        Defaults to INFO.
    """
    normalized = str(level).upper()
    if normalized not in _LEVELS:
        normalized = "INFO"

    logging.basicConfig(
        level=getattr(logging, normalized),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def get_logger(name: str) -> logging.Logger:
    """Return a module-scoped logger. Thin wrapper over ``logging.getLogger``."""
    return logging.getLogger(name)
