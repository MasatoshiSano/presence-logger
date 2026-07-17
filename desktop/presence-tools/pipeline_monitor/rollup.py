"""MQTT メッセージ列 → 子デバイス別サマリ（純関数）。

状態判定は services/bridge の LivenessTracker と同じ考え方:
  online  : 直近の生存信号(heartbeat or status=online)が timeout 以内
  stale   : 生存信号が timeout より古く、offline は来ていない
  offline : Last-Will(offline) が最新の信号
  unknown : 生存信号なし
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from pipeline_monitor.model import ChildStat, MqttMsg


@dataclass
class _Acc:
    record_count: int = 0
    last_record_mk: str | None = None
    last_hb_at: float | None = None
    last_online_at: float | None = None
    last_offline_at: float | None = None


def _state(a: _Acc, now: float, timeout: float) -> tuple[str, float | None]:
    alive_candidates = [t for t in (a.last_hb_at, a.last_online_at) if t is not None]
    alive_at = max(alive_candidates) if alive_candidates else None
    if a.last_offline_at is not None and (alive_at is None or a.last_offline_at >= alive_at):
        return "offline", (now - a.last_hb_at if a.last_hb_at is not None else None)
    if alive_at is not None:
        age = now - alive_at
        return ("online" if age <= timeout else "stale"), (
            now - a.last_hb_at if a.last_hb_at is not None else None
        )
    return "unknown", None


def child_rollup(
    msgs: list[MqttMsg], *, now: float, heartbeat_timeout: float
) -> list[ChildStat]:
    accs: dict[str, _Acc] = {}
    for m in msgs:
        if not m.device_id:
            continue
        a = accs.setdefault(m.device_id, _Acc())
        if m.kind == "record":
            a.record_count += 1
            mk = None
            # mk_date は summary ではなく raw から取らず、record は event_id 経由で
            # 判別済み。mk は summary に含むが厳密比較のため raw を再解析する。
            try:
                raw_mk = json.loads(m.raw).get("mk_date")
                mk = str(raw_mk) if raw_mk is not None else None
            except (ValueError, TypeError, AttributeError):
                mk = None
            if mk and (a.last_record_mk is None or mk > a.last_record_mk):
                a.last_record_mk = mk
        elif m.kind == "heartbeat":
            if a.last_hb_at is None or m.ts > a.last_hb_at:
                a.last_hb_at = m.ts
        elif m.kind == "status":
            state = m.raw.strip()
            if state == "online":
                if a.last_online_at is None or m.ts > a.last_online_at:
                    a.last_online_at = m.ts
            elif state == "offline":
                if a.last_offline_at is None or m.ts > a.last_offline_at:
                    a.last_offline_at = m.ts

    out: list[ChildStat] = []
    for dev, a in accs.items():
        state, hb_age = _state(a, now, heartbeat_timeout)
        out.append(ChildStat(
            device_id=dev,
            record_count=a.record_count,
            last_record_mk_date=a.last_record_mk,
            last_heartbeat_age=(round(hb_age, 1) if hb_age is not None else None),
            state=state,
        ))
    out.sort(key=lambda s: s.device_id)
    return out
