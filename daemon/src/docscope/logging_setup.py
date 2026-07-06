"""Structured JSON-lines logging to ``~/.docscope/logs/``.

Every log record is emitted as one JSON object per line, which makes the logs
trivially greppable and machine-parseable. Extra structured fields can be
attached via ``logger.info("msg", extra={"extra_fields": {...}})``.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

_CONFIGURED = False


class JsonLineFormatter(logging.Formatter):
    """Render a log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(log_dir: Path, level: int = logging.INFO, *, to_stderr: bool = True) -> None:
    """Idempotently configure the root ``docscope`` logger."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    log_dir.mkdir(parents=True, exist_ok=True)
    logfile = log_dir / f"docscope-{datetime.now(tz=UTC):%Y%m%d}.log"

    root = logging.getLogger("docscope")
    root.setLevel(level)
    root.propagate = False

    formatter = JsonLineFormatter()
    file_handler = logging.FileHandler(logfile, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    if to_stderr:
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(formatter)
        root.addHandler(stderr_handler)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced child of the ``docscope`` logger."""
    return logging.getLogger(f"docscope.{name}")


def log_event(logger: logging.Logger, level: int, msg: str, **fields: object) -> None:
    """Log ``msg`` with arbitrary structured ``fields`` attached."""
    logger.log(level, msg, extra={"extra_fields": fields})
