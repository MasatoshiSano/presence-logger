"""共有データ型（純粋データ、I/O やロジックは持たない）。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MqttMsg:
    ts: float                       # 受信時刻（epoch秒、monitor 側で採番）
    topic: str
    kind: str                       # record/heartbeat/status/ack/other
    device_id: str | None
    event_id: str | None
    summary: str                    # 人が読む1行要約
    raw: str


@dataclass
class LogLine:
    """②MQTT生ログの表示用1行（連続heartbeatはまとめて1行になる）。"""
    ts: float
    text: str
    dim: bool                       # heartbeatまとめ行は目立たせない


@dataclass
class ChildStat:
    device_id: str
    record_count: int
    last_record_mk_date: str | None
    last_heartbeat_age: float | None    # 秒。None=一度も受信なし
    state: str                          # online/offline/stale/unknown


@dataclass
class InboxRow:
    event_id: str
    device_id: str | None
    mk_date: str
    status: str                     # received/sent
    retry_count: int
    last_error: str | None
    received_at_iso: str
    sent_at_iso: str | None
    sta_no1: str = ""
    sta_no2: str = ""
    sta_no3: str = ""
    t1_status: str = ""
    mk_date_committed: str | None = None


@dataclass
class DeviceAgg:
    """record_inbox の device_id 別集計（DBが真実の「どの子が何件送ったか」）。"""
    device_id: str
    count: int
    last_mk_date: str | None


@dataclass
class InboxView:
    rows: list[InboxRow]
    received: int                   # 滞留（未送信）件数
    sent: int
    total: int
    devices: list[DeviceAgg] = field(default_factory=list)
    failed: int = 0                 # 諦めた件数（主キー重複など、再送しても解消しない）


@dataclass
class OracleRow:
    mk_date: str
    sta_no1: str
    sta_no2: str
    sta_no3: str
    t1_status: str
    upcmpflg: str


@dataclass
class OracleResult:
    rows: list[OracleRow] = field(default_factory=list)
    count: str | None = None
    ora_code: str = ""
    error_message: str = ""

    @property
    def ok(self) -> bool:
        return not self.ora_code and not self.error_message


@dataclass
class LinkedRow:
    """③record_inbox 行に、MQTT で観測できたかの段階情報を付与したもの。"""
    inbox: InboxRow
    mqtt_seen: bool                 # 同じ event_id を MQTT 生ログで見たか
    stage: str                      # 到達段階ラベル（例: "MQTT→inbox→Oracle✓"）
