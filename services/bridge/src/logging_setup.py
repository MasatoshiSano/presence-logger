import json
import logging
import os
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

_RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "taskName",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=_local_tz()).isoformat(
            timespec="milliseconds"
        )
        out: dict[str, Any] = {
            "ts": ts,
            "level": record.levelname,
            "logger": record.name,
            "process": getattr(record, "_process", "unknown"),
            "device_id": getattr(record, "_device_id", "unknown"),
            "pid": os.getpid(),
        }
        # Include any extras attached to the record (non-reserved attributes).
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            out[key] = value
        if record.msg and "message" not in out:
            out["message"] = record.getMessage()
        if record.exc_info:
            out["error"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else "Exception",
                "message": str(record.exc_info[1]),
                "traceback": self.formatException(record.exc_info),
            }
        return json.dumps(out, ensure_ascii=False, default=str)


def _local_tz() -> timezone:
    # Use system's current local offset.
    return datetime.now().astimezone().tzinfo  # type: ignore[return-value]


def build_formatter() -> JsonFormatter:
    return JsonFormatter()


def install_common_fields(record: logging.LogRecord, *, process: str, device_id: str) -> None:
    record._process = process       # noqa: SLF001 (intentional sentinel attrs)
    record._device_id = device_id   # noqa: SLF001


def setup_logging(
    *,
    process: str,
    device_id: str,
    log_dir: str,
    level: str = "INFO",
) -> None:
    """Install root logger handlers (file + stdout) using the shared JSON format."""
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    formatter = build_formatter()

    file_handler = RotatingFileHandler(
        Path(log_dir) / f"{process}.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)

    # Inject process/device_id into every record via a Filter on each handler.
    class _CommonFieldsFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            install_common_fields(record, process=process, device_id=device_id)
            return True

    common_filter = _CommonFieldsFilter()
    file_handler.addFilter(common_filter)
    stdout_handler.addFilter(common_filter)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())
    root.addHandler(file_handler)
    root.addHandler(stdout_handler)
