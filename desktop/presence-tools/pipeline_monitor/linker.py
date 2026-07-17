"""record_inbox 行に段階ラベルを付ける（純関数）。

bridge は Oracle が受理して初めて status を 'sent' にするため、
'sent' == Oracle 到達とみなせる（サイドカーの SELECT N件に依存しない）。
"""
from __future__ import annotations

from pipeline_monitor.model import InboxRow, LinkedRow


def _stage(status: str, mqtt_seen: bool) -> str:
    head = "MQTT→" if mqtt_seen else ""
    if status == "sent":
        return f"{head}inbox→Oracle✓"
    return f"{head}inbox(滞留)"


def annotate_stages(
    inbox_rows: list[InboxRow], mqtt_event_ids: set[str]
) -> list[LinkedRow]:
    out: list[LinkedRow] = []
    for row in inbox_rows:
        seen = row.event_id in mqtt_event_ids
        out.append(LinkedRow(inbox=row, mqtt_seen=seen, stage=_stage(row.status, seen)))
    return out
