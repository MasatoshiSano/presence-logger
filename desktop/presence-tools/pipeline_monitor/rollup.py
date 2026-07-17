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

from pipeline_monitor.model import ChildStat, DeviceAgg, MqttMsg


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


def merged_children(
    inbox_devices: list[DeviceAgg],
    msgs: list[MqttMsg],
    *,
    now: float,
    heartbeat_timeout: float,
) -> list[ChildStat]:
    """「どの子から何が来たか」を record_inbox(DB) の件数を真実として作り、
    MQTT の heartbeat/status があれば online/offline を重ねる。

    DB は全履歴を持つので、監視起動後に新規送信が無くても子が消えない。
    DB に無いが MQTT で生きている子（記録前の heartbeat のみ等）も拾う。
    """
    live = {s.device_id: s for s in child_rollup(
        msgs, now=now, heartbeat_timeout=heartbeat_timeout)}
    out: list[ChildStat] = []
    seen: set[str] = set()
    for d in inbox_devices:
        seen.add(d.device_id)
        lv = live.get(d.device_id)
        out.append(ChildStat(
            device_id=d.device_id,
            record_count=d.count,                 # DBの総件数が真実
            last_record_mk_date=d.last_mk_date,
            last_heartbeat_age=lv.last_heartbeat_age if lv else None,
            state=lv.state if lv else "unknown",  # heartbeat 無しは unknown
        ))
    # DB未登録だが MQTT で観測できた子（記録より先に heartbeat 等）も出す。
    for dev, lv in live.items():
        if dev not in seen:
            out.append(lv)
    out.sort(key=lambda s: s.device_id)
    return out
