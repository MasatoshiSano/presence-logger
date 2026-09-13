"""Child MQTT (heartbeat / status / record) をファイルに残す。

パイプライン監視②はメモリだけなので、ハブでは bridge が同じ内容を
ローテ付きでディスクへ書く。監視を閉じていても残る。
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

DEFAULT_MQTT_LOG_PATH = "/var/log/presence-logger/child-mqtt.log"


def resolve_mqtt_log_path(liv_cfg: dict) -> str:
    """Missing key → default path. Explicit empty/null → disable."""
    if "mqtt_log_path" not in liv_cfg:
        return DEFAULT_MQTT_LOG_PATH
    return str(liv_cfg.get("mqtt_log_path") or "")


def filter_out_confirmed_records(lines: list[str], event_ids: set[str]) -> tuple[list[str], int]:
    """Drop kind=record lines whose payload.event_id is Oracle-confirmed.

    heartbeat / status / 壊れた JSON / event_id 不明の record は残す。
    """
    if not event_ids:
        return list(lines), 0
    kept: list[str] = []
    dropped = 0
    for line in lines:
        text = line.strip()
        if not text:
            continue
        try:
            row = json.loads(text)
        except json.JSONDecodeError:
            kept.append(line)
            continue
        if not isinstance(row, dict) or row.get("kind") != "record":
            kept.append(line)
            continue
        payload = row.get("payload")
        event_id = payload.get("event_id") if isinstance(payload, dict) else None
        if event_id and str(event_id) in event_ids:
            dropped += 1
            continue
        kept.append(line)
    return kept, dropped


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
        self._lock = threading.Lock()
        self._max_bytes = max_bytes
        self._backup_count = backup_count
        if not self.path:
            return
        if not self.path.is_absolute():
            self.error = "mqtt_log_path must be an absolute path"
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            logger = logging.getLogger(f"mqtt_file_log.{self.path}")
            logger.setLevel(logging.INFO)
            logger.propagate = False
            if not logger.handlers:
                handler = RotatingFileHandler(
                    self.path,
                    maxBytes=self._max_bytes,
                    backupCount=self._backup_count,
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
        with self._lock:
            try:
                self._log.info(line)
            except OSError:
                pass

    def drop_records(self, event_ids: set[str]) -> int:
        """Remove Oracle-confirmed record lines. Does not stop further writes."""
        if self._log is None or self.path is None or not event_ids:
            return 0
        with self._lock:
            try:
                raw = self.path.read_text(encoding="utf-8")
            except OSError:
                return 0
            kept, dropped = filter_out_confirmed_records(raw.splitlines(), event_ids)
            if dropped == 0:
                return 0
            tmp = self.path.with_name(self.path.name + ".tmp")
            try:
                tmp.write_text(("\n".join(kept) + ("\n" if kept else "")), encoding="utf-8")
                tmp.replace(self.path)
            except OSError:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                return 0
            self._reopen_handler()
            return dropped

    def _reopen_handler(self) -> None:
        if self._log is None or self.path is None:
            return
        for handler in list(self._log.handlers):
            self._log.removeHandler(handler)
            handler.close()
        handler = RotatingFileHandler(
            self.path,
            maxBytes=self._max_bytes,
            backupCount=self._backup_count,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        self._log.addHandler(handler)
