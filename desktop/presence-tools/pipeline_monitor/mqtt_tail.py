"""MQTT 生ログの要約（純関数）と、mosquitto_sub 購読の薄いラッパ。

購読部（MqttTail）は別タスクで追加する。まず純関数だけを置く。
"""
from __future__ import annotations

import json

from pipeline_monitor.model import MqttMsg

RECORD_TOPIC = "presence/record"
STATUS_PREFIX = "presence/status/"
HEARTBEAT_PREFIX = "presence/heartbeat/"
_ACK_SUFFIX = "/ack"

_T1_BADGE = {"1": "🟢ENTER", "2": "🔴EXIT"}


def _t1_badge(t1: object) -> str:
    key = str(t1)
    return _T1_BADGE.get(key, f"?({key})")


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
                f"{device_id or '?'} {_t1_badge(d.get('t1_status'))} "
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
