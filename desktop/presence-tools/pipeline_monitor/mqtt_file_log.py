"""監視TUI起動中の MQTT 生ログをディスクへ残す（閉じたあとは追記しない）。"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path


class MqttFileLog:
    def __init__(
        self,
        path: str,
        *,
        max_bytes: int = 10 * 1024 * 1024,
        backup_count: int = 5,
    ):
        self.path = Path(path) if path else None
        self.enabled = False
        self.error: str | None = None
        self._log: logging.Logger | None = None
        if not self.path:
            return
        if not self.path.is_absolute():
            self.error = "mqtt_log_path must be an absolute path"
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            logger = logging.getLogger(f"pipeline_mqtt_file.{self.path}")
            logger.setLevel(logging.INFO)
            logger.propagate = False
            if not logger.handlers:
                handler = RotatingFileHandler(
                    self.path,
                    maxBytes=max_bytes,
                    backupCount=backup_count,
                    encoding="utf-8",
                )
                handler.setFormatter(logging.Formatter("%(message)s"))
                logger.addHandler(handler)
            self._log = logger
            self.enabled = True
        except OSError as e:
            self.error = str(e)
            self._log = None

    def write(self, kind: str, device_id: str, payload: str) -> None:
        if self._log is None:
            return
        ts = datetime.now().astimezone().isoformat(timespec="milliseconds")
        text = (payload or "").strip()
        try:
            body: object = json.loads(text) if text else ""
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = text
        line = json.dumps(
            {
                "ts": ts,
                "kind": kind,
                "device_id": device_id or "",
                "payload": body,
            },
            ensure_ascii=False,
            default=str,
        )
        try:
            self._log.info(line)
        except OSError:
            pass
