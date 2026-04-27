from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from services.bridge.src.oracle_client import MergeResult


@dataclass
class FakeOracle:
    canned: list[MergeResult] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def execute_merge_for_profile(self, **kwargs):
        self.calls.append(kwargs)
        if self.canned:
            return self.canned.pop(0)
        return MergeResult(rows_affected=1, ora_code=None, error_message="")


@dataclass
class FakeMqtt:
    acks: list[dict[str, Any]] = field(default_factory=list)

    def publish_ack(self, topic: str, *, event_id: str, mk_date_committed: str,
                    committed_at_iso: str) -> None:
        self.acks.append({
            "topic": topic, "event_id": event_id,
            "mk_date_committed": mk_date_committed, "committed_at_iso": committed_at_iso,
        })


@dataclass
class FakeNetwork:
    ssid: str | None = "factory_a_wifi"
    cached_ssid: str | None = None
    def __post_init__(self): self.cached_ssid = self.ssid
    def get_current_ssid(self) -> str | None:
        return self.ssid


@dataclass
class FakeTimeWatcher:
    is_synced: bool = True
    baseline: Any = None

    def __post_init__(self):
        if self.is_synced and self.baseline is None:
            from services.bridge.src.time_watcher import SyncBaseline
            self.baseline = SyncBaseline(
                sync_wall=datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone(timedelta(hours=9))),
                sync_monotonic_ns=2_000_000_000,
            )

    def poll(self) -> None:
        pass
