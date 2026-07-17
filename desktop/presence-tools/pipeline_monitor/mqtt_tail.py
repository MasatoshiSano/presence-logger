"""MQTT 生ログの要約（純関数）と、mosquitto_sub 購読の薄いラッパ。

購読部（MqttTail）は別タスクで追加する。まず純関数だけを置く。
"""
from __future__ import annotations

import json
import shutil
import subprocess  # noqa: S404
import threading
import time
from collections import deque
from collections.abc import Callable

from pipeline_monitor.model import MqttMsg

RECORD_TOPIC = "presence/record"
STATUS_PREFIX = "presence/status/"
HEARTBEAT_PREFIX = "presence/heartbeat/"
_ACK_SUFFIX = "/ack"

def _t1_label(t1: object) -> str:
    # T1_STATUS は入退室に限らない任意コードなので、ENTER/EXIT に変換せず
    # 生の数値をそのまま見せる（例: 1,2,3,...）。
    return "T1=?" if t1 is None else f"T1={t1}"


def _fmt_mk(mk: str) -> str:
    if len(mk) == 14 and mk.isdigit():
        return f"{mk[0:4]}-{mk[4:6]}-{mk[6:8]} {mk[8:10]}:{mk[10:12]}:{mk[12:14]}"
    return mk


def _short(s: str, n: int = 12) -> str:
    return s if len(s) <= n else s[:n] + "…"


def mqtt_summarize(topic: str, payload: str, *, now: float) -> MqttMsg:
    """1件の MQTT メッセージを種別判定して要約する。決して例外を投げない。"""
    device_id: str | None = None
    event_id: str | None = None

    if topic == RECORD_TOPIC:
        kind = "record"
        try:
            d = json.loads(payload)
            event_id = str(d["event_id"])
            device_id = d.get("device_id")
            summary = (
                f"{device_id or '?'} {_t1_label(d.get('t1_status'))} "
                f"{_fmt_mk(str(d.get('mk_date', '')))} id={_short(event_id)}"
            )
        except (ValueError, KeyError, TypeError):
            summary = "record(解析不可)"
    elif topic.startswith(STATUS_PREFIX):
        kind = "status"
        device_id = topic[len(STATUS_PREFIX):] or None
        state = payload.strip()
        summary = f"{device_id or '?'} → {state}"
    elif topic.startswith(HEARTBEAT_PREFIX):
        kind = "heartbeat"
        device_id = topic[len(HEARTBEAT_PREFIX):] or None
        summary = f"{device_id or '?'} 💓"
    elif topic.endswith(_ACK_SUFFIX):
        kind = "ack"
        try:
            d = json.loads(payload)
            event_id = str(d["event_id"]) if "event_id" in d else None
        except (ValueError, TypeError):
            pass
        summary = f"ack {topic} {('id=' + _short(event_id)) if event_id else ''}".strip()
    else:
        kind = "other"
        summary = _short(payload, 40)

    return MqttMsg(
        ts=now, topic=topic, kind=kind, device_id=device_id,
        event_id=event_id, summary=summary, raw=payload,
    )


class MqttTail:
    """`mosquitto_sub -v` を子プロセスで走らせ、行をリングバッファに溜める。

    ingest_line() は純粋（テスト可能）。start()/stop() が subprocess を管理する。
    """

    def __init__(
        self,
        host: str,
        port: int,
        topic: str = "presence/#",
        maxlen: int = 500,
        clock: Callable[[], float] = time.time,
    ):
        self._host = host
        self._port = port
        self._topic = topic
        self._clock = clock
        self._buf: deque[MqttMsg] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._running = False

    def ingest_line(self, line: str) -> None:
        line = line.rstrip("\n")
        if not line.strip():
            return
        # `-v` 出力は "<topic> <payload>"。payload に空白/JSON があるので1回だけ分割。
        topic, _, payload = line.partition(" ")
        msg = mqtt_summarize(topic, payload, now=self._clock())
        with self._lock:
            self._buf.append(msg)

    def messages(self) -> list[MqttMsg]:
        with self._lock:
            return list(self._buf)

    def event_ids(self) -> set[str]:
        with self._lock:
            return {m.event_id for m in self._buf if m.event_id}

    # --- subprocess management (ユニットテスト対象外) ---
    def _cmd(self) -> list[str]:
        exe = shutil.which("mosquitto_sub")
        if not exe:
            msg = "mosquitto_sub が見つかりません（mosquitto-clients を入れてください）"
            raise RuntimeError(msg)
        return [exe, "-h", self._host, "-p", str(self._port), "-t", self._topic, "-v"]

    def _run(self) -> None:
        while self._running:
            try:
                self._proc = subprocess.Popen(  # noqa: S603
                    self._cmd(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, bufsize=1,
                )
                assert self._proc.stdout is not None
                for line in self._proc.stdout:
                    if not self._running:
                        break
                    self.ingest_line(line)
            except (RuntimeError, OSError):
                pass
            # 落ちたら少し待って再購読（broker 再起動・AP 再接続に追従）
            for _ in range(20):
                if not self._running:
                    break
                time.sleep(0.1)

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
