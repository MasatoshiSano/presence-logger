# pipeline-monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single-screen curses TUI on the hub Pi that shows, side by side, which child sent what over MQTT, the raw MQTT traffic, the MQTT→Oracle handoff (`record_inbox`), and the rows actually in Oracle — so an operator can trace one `event_id` across all four stages.

**Architecture:** One host-resident Python process. A background `mosquitto_sub` subprocess feeds a ring buffer of `presence/#` messages; a main loop reads four sources every ~1s (MQTT buffer, `record_inbox` SQLite read-only, Oracle via the JDBC sidecar every 15s / on `r`) and redraws four curses panes. Pure logic (summarize, roll-up, parse, annotate) is split from I/O and curses so it is unit-testable.

**Tech Stack:** Python 3.13 stdlib only (`curses`, `sqlite3`, `subprocess`, `json`, `urllib`) + `PyYAML` (already used). MQTT via the `mosquitto_sub` CLI (`mosquitto-clients`). Oracle via `docker exec presence-oracle-jdbc … /select_recent` (same mechanism as `show-recent-records.sh`).

## Global Constraints

- **Read-only on production data.** `record_inbox` MUST be opened with `sqlite3.connect("file:<path>?mode=ro", uri=True)`. The monitor never writes to `bridge_record_buf.db`.
- **No new container or daemon.** Runs as a plain host process launched from a desktop launcher, coexisting with `記録モニタ` (`watch-records.sh`) and `直近30件` (`show-recent-records.sh`), which are NOT modified.
- **Package location:** `desktop/presence-tools/pipeline_monitor/` (underscore — importable). Wrapper and launcher siblings under `desktop/presence-tools/` and `desktop/launchers/`.
- **MQTT broker:** `10.42.0.1:1883`, anonymous (AP gateway). Topic root `presence/#`.
- **record_inbox path:** `/var/lib/presence-logger/bridge_record_buf.db` (host bind-mount). Schema: table `record_inbox(event_id, mk_date, sta_no1, sta_no2, sta_no3, t1_status, device_id, raw_payload, status IN ('received','sent'), received_at_iso, sent_at_iso, mk_date_committed, retry_count, next_retry_at_iso, last_error)`.
- **record payload (JSON):** keys `event_id, mk_date(14-digit), sta_no1, sta_no2, sta_no3, t1_status(int), device_id, schema_version`.
- **status topic:** `presence/status/<device_id>`, retained text `"online"`/`"offline"`. **heartbeat topic:** `presence/heartbeat/<device_id>`, JSON dict (fields not fixed). **record topic:** `presence/record`. **ack topics:** `presence/record/ack`, `presence/event/ack`.
- **T1_STATUS legend:** `1 → 🟢ENTER`, `2 → 🔴EXIT` (Oracle pane matches existing `_render_recent.py`). Note: child CSV may carry other codes (e.g. `3`); render unknown codes as `?(<n>)`, never crash.
- **Style:** ruff config in `pyproject.toml` (line-length 100, py313, selected rules incl. `T20`,`S`,`N`,`UP`,`B`,`PT`). Comments/labels in Japanese to match existing operator tools. Run `ruff check` before each commit.
- **Test config:** `pyproject.toml` `[tool.pytest.ini_options] testpaths` is explicit; `tests/desktop` MUST be added there. Import-mode is `importlib`; a `tests/desktop/conftest.py` adds `desktop/presence-tools` to `sys.path` so `import pipeline_monitor` works.

---

### Task 1: Package scaffold + MQTT message summarizer (`mqtt_summarize`)

Pure function turning one raw MQTT `(topic, payload)` into a typed, human-readable `MqttMsg`. This task also lays down the package + test wiring every later task reuses.

**Files:**
- Create: `desktop/presence-tools/pipeline_monitor/__init__.py` (empty)
- Create: `desktop/presence-tools/pipeline_monitor/model.py`
- Create: `desktop/presence-tools/pipeline_monitor/mqtt_tail.py` (summarizer only in this task)
- Create: `tests/desktop/__init__.py` (empty)
- Create: `tests/desktop/conftest.py`
- Create: `tests/desktop/test_mqtt_summarize.py`
- Modify: `pyproject.toml` (add `tests/desktop` to `testpaths`)

**Interfaces:**
- Produces:
  - `model.MqttMsg` dataclass: `ts: float, topic: str, kind: str, device_id: str | None, event_id: str | None, summary: str, raw: str` where `kind ∈ {"record","heartbeat","status","ack","other"}`.
  - `mqtt_tail.mqtt_summarize(topic: str, payload: str, *, now: float) -> MqttMsg`

- [ ] **Step 1: Create the package + test wiring**

Create `desktop/presence-tools/pipeline_monitor/__init__.py`:
```python
```
(empty file)

Create `tests/desktop/__init__.py`:
```python
```
(empty file)

Create `tests/desktop/conftest.py`:
```python
import sys
from pathlib import Path

# The tool package lives under a hyphenated dir (not a dotted-importable path),
# so put its parent on sys.path to allow `import pipeline_monitor`.
_TOOLS = Path(__file__).resolve().parents[2] / "desktop" / "presence-tools"
sys.path.insert(0, str(_TOOLS))
```

Modify `pyproject.toml` — change the `testpaths` line to include the new dir:
```toml
testpaths = ["services/detector/tests", "services/bridge/tests", "tests/integration", "tests/desktop"]
```

- [ ] **Step 2: Create the data model**

Create `desktop/presence-tools/pipeline_monitor/model.py`:
```python
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


@dataclass
class InboxView:
    rows: list[InboxRow]
    received: int                   # 滞留（未送信）件数
    sent: int
    total: int


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
```

- [ ] **Step 3: Write the failing test**

Create `tests/desktop/test_mqtt_summarize.py`:
```python
from pipeline_monitor.mqtt_tail import mqtt_summarize

NOW = 1_700_000_000.0


def test_record_message_extracts_ids_and_summary():
    payload = (
        '{"event_id":"abc123def456","mk_date":"20260717092300",'
        '"sta_no1":"100","sta_no2":"200","sta_no3":"300","t1_status":1,'
        '"device_id":"zero2","schema_version":1}'
    )
    m = mqtt_summarize("presence/record", payload, now=NOW)
    assert m.kind == "record"
    assert m.device_id == "zero2"
    assert m.event_id == "abc123def456"
    assert m.ts == NOW
    assert "zero2" in m.summary
    assert "20260717092300" in m.summary or "2026-07-17" in m.summary
    assert "🟢" in m.summary  # t1_status=1 -> ENTER


def test_status_message_online_offline():
    on = mqtt_summarize("presence/status/pi-b", "online", now=NOW)
    assert on.kind == "status"
    assert on.device_id == "pi-b"
    assert "online" in on.summary
    off = mqtt_summarize("presence/status/pi-b", "offline", now=NOW)
    assert "offline" in off.summary


def test_heartbeat_message_device_from_topic():
    m = mqtt_summarize("presence/heartbeat/zero2", '{"uptime_s":42}', now=NOW)
    assert m.kind == "heartbeat"
    assert m.device_id == "zero2"


def test_ack_message():
    m = mqtt_summarize("presence/record/ack", '{"event_id":"abc"}', now=NOW)
    assert m.kind == "ack"
    assert m.event_id == "abc"


def test_malformed_record_payload_does_not_crash():
    m = mqtt_summarize("presence/record", "{not json", now=NOW)
    assert m.kind == "record"
    assert m.event_id is None          # couldn't parse
    assert m.raw == "{not json"        # raw preserved


def test_unknown_topic_is_other():
    m = mqtt_summarize("presence/misc/thing", "hi", now=NOW)
    assert m.kind == "other"
```

- [ ] **Step 4: Run test to verify it fails**

Run: `python -m pytest tests/desktop/test_mqtt_summarize.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline_monitor.mqtt_tail'`

- [ ] **Step 5: Implement `mqtt_summarize`**

Create `desktop/presence-tools/pipeline_monitor/mqtt_tail.py`:
```python
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
```

- [ ] **Step 6: Run tests + lint**

Run: `python -m pytest tests/desktop/test_mqtt_summarize.py -v && ruff check desktop/presence-tools/pipeline_monitor tests/desktop`
Expected: all tests PASS, ruff reports no errors.

- [ ] **Step 7: Commit**

```bash
git add desktop/presence-tools/pipeline_monitor tests/desktop pyproject.toml
git commit -m "feat(monitor): pipeline_monitor package + mqtt_summarize"
```

---

### Task 2: Per-child roll-up (`child_rollup`)

Aggregate the MQTT buffer into one `ChildStat` per device (pane ①).

**Files:**
- Modify: `desktop/presence-tools/pipeline_monitor/rollup.py` (Create)
- Test: `tests/desktop/test_child_rollup.py` (Create)

**Interfaces:**
- Consumes: `model.MqttMsg`, `model.ChildStat`.
- Produces: `rollup.child_rollup(msgs: list[MqttMsg], *, now: float, heartbeat_timeout: float) -> list[ChildStat]` (sorted by `device_id`).

- [ ] **Step 1: Write the failing test**

Create `tests/desktop/test_child_rollup.py`:
```python
from pipeline_monitor.model import MqttMsg
from pipeline_monitor.rollup import child_rollup

TIMEOUT = 60.0


def _rec(dev, mk, ts, eid="e"):
    return MqttMsg(ts=ts, topic="presence/record", kind="record",
                   device_id=dev, event_id=eid, summary="", raw="")


def _hb(dev, ts):
    return MqttMsg(ts=ts, topic=f"presence/heartbeat/{dev}", kind="heartbeat",
                   device_id=dev, event_id=None, summary="", raw="")


def _status(dev, state, ts):
    return MqttMsg(ts=ts, topic=f"presence/status/{dev}", kind="status",
                   device_id=dev, event_id=None, summary="", raw=state)


def test_counts_records_and_latest_mk_per_device():
    now = 1000.0
    msgs = [
        _rec("zero2", "20260717090000", 900.0),
        _rec("zero2", "20260717091000", 950.0),
        _rec("pi-b", "20260717080000", 400.0),
    ]
    stats = {s.device_id: s for s in child_rollup(msgs, now=now, heartbeat_timeout=TIMEOUT)}
    assert stats["zero2"].record_count == 2
    assert stats["zero2"].last_record_mk_date == "20260717091000"  # latest
    assert stats["pi-b"].record_count == 1


def test_state_online_when_recent_heartbeat():
    now = 1000.0
    stats = child_rollup([_hb("zero2", 980.0)], now=now, heartbeat_timeout=TIMEOUT)
    assert stats[0].state == "online"
    assert 0 <= stats[0].last_heartbeat_age <= 30


def test_state_stale_when_heartbeat_old_and_no_offline():
    now = 1000.0
    stats = child_rollup([_hb("zero2", 500.0)], now=now, heartbeat_timeout=TIMEOUT)
    assert stats[0].state == "stale"


def test_state_offline_when_last_will_is_newest():
    now = 1000.0
    msgs = [_hb("zero2", 900.0), _status("zero2", "offline", 950.0)]
    stats = child_rollup(msgs, now=now, heartbeat_timeout=TIMEOUT)
    assert stats[0].state == "offline"


def test_result_sorted_by_device_id():
    now = 1000.0
    msgs = [_rec("zero2", "20260717090000", 900.0), _rec("aaa", "20260717090000", 900.0)]
    stats = child_rollup(msgs, now=now, heartbeat_timeout=TIMEOUT)
    assert [s.device_id for s in stats] == ["aaa", "zero2"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/desktop/test_child_rollup.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline_monitor.rollup'`

- [ ] **Step 3: Implement `child_rollup`**

Create `desktop/presence-tools/pipeline_monitor/rollup.py`:
```python
"""MQTT メッセージ列 → 子デバイス別サマリ（純関数）。

状態判定は services/bridge の LivenessTracker と同じ考え方:
  online  : 直近の生存信号(heartbeat or status=online)が timeout 以内
  stale   : 生存信号が timeout より古く、offline は来ていない
  offline : Last-Will(offline) が最新の信号
  unknown : 生存信号なし
"""
from __future__ import annotations

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
                import json
                mk = str(json.loads(m.raw).get("mk_date"))
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
                a.last_online_at = m.ts
            elif state == "offline":
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
```

- [ ] **Step 4: Run tests + lint**

Run: `python -m pytest tests/desktop/test_child_rollup.py -v && ruff check desktop/presence-tools/pipeline_monitor`
Expected: all PASS, ruff clean. (If ruff flags the inline `import json`, move `import json` to module top and remove the inline import.)

- [ ] **Step 5: Commit**

```bash
git add desktop/presence-tools/pipeline_monitor/rollup.py tests/desktop/test_child_rollup.py
git commit -m "feat(monitor): per-child roll-up from MQTT stream"
```

---

### Task 3: record_inbox reader (`RecordInboxReader`)

Read the bridge SQLite buffer read-only: recent rows + received/sent counts (pane ③).

**Files:**
- Create: `desktop/presence-tools/pipeline_monitor/inbox_reader.py`
- Test: `tests/desktop/test_inbox_reader.py`

**Interfaces:**
- Consumes: `model.InboxRow`, `model.InboxView`.
- Produces: `inbox_reader.RecordInboxReader(db_path: str)` with `.read(limit: int = 30) -> InboxView`. Raises `FileNotFoundError` if the DB file is missing (caller catches and shows `⚠`).

- [ ] **Step 1: Write the failing test**

Create `tests/desktop/test_inbox_reader.py`:
```python
import sqlite3

import pytest

from pipeline_monitor.inbox_reader import RecordInboxReader

_SCHEMA = """
CREATE TABLE record_inbox (
  event_id TEXT PRIMARY KEY, mk_date TEXT NOT NULL,
  sta_no1 TEXT, sta_no2 TEXT, sta_no3 TEXT, t1_status INTEGER,
  device_id TEXT, raw_payload TEXT NOT NULL,
  status TEXT NOT NULL, received_at_iso TEXT NOT NULL, sent_at_iso TEXT,
  mk_date_committed TEXT, retry_count INTEGER NOT NULL DEFAULT 0,
  next_retry_at_iso TEXT, last_error TEXT
);
"""


def _make_db(path, rows):
    c = sqlite3.connect(path)
    c.executescript(_SCHEMA)
    c.executemany(
        "INSERT INTO record_inbox (event_id, mk_date, sta_no1, sta_no2, sta_no3, "
        "t1_status, device_id, raw_payload, status, received_at_iso, sent_at_iso, "
        "retry_count, last_error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    c.commit()
    c.close()


def test_read_counts_and_orders_recent_first(tmp_path):
    db = tmp_path / "buf.db"
    _make_db(db, [
        ("e1", "20260717090000", "1", "2", "3", 1, "zero2", "{}", "sent",
         "2026-07-17T09:00:00Z", "2026-07-17T09:00:01Z", 0, None),
        ("e2", "20260717091000", "1", "2", "3", 2, "zero2", "{}", "received",
         "2026-07-17T09:10:00Z", None, 3, "ORA-12514"),
    ])
    view = RecordInboxReader(str(db)).read(limit=30)
    assert view.total == 2
    assert view.sent == 1
    assert view.received == 1
    assert view.rows[0].event_id == "e2"       # most recent first
    assert view.rows[0].status == "received"
    assert view.rows[0].retry_count == 3
    assert view.rows[0].last_error == "ORA-12514"


def test_missing_db_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        RecordInboxReader(str(tmp_path / "nope.db")).read()


def test_read_is_readonly(tmp_path):
    db = tmp_path / "buf.db"
    _make_db(db, [("e1", "20260717090000", "1", "2", "3", 1, "z", "{}",
                   "sent", "2026-07-17T09:00:00Z", "2026-07-17T09:00:01Z", 0, None)])
    reader = RecordInboxReader(str(db))
    reader.read()
    # opening ?mode=ro must reject writes
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as ro:
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("DELETE FROM record_inbox")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/desktop/test_inbox_reader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline_monitor.inbox_reader'`

- [ ] **Step 3: Implement `RecordInboxReader`**

Create `desktop/presence-tools/pipeline_monitor/inbox_reader.py`:
```python
"""bridge の record_inbox(SQLite) を読み取り専用で覗く。書き込みは絶対にしない。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from pipeline_monitor.model import InboxRow, InboxView


class RecordInboxReader:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def read(self, limit: int = 30) -> InboxView:
        if not Path(self.db_path).exists():
            raise FileNotFoundError(self.db_path)
        # WAL 中でも安全に読める read-only オープン。
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            counts = conn.execute(
                "SELECT status, COUNT(*) AS n FROM record_inbox GROUP BY status"
            ).fetchall()
            received = sent = 0
            for r in counts:
                if r["status"] == "received":
                    received = r["n"]
                elif r["status"] == "sent":
                    sent = r["n"]
            cur = conn.execute(
                "SELECT event_id, device_id, mk_date, status, retry_count, "
                "last_error, received_at_iso, sent_at_iso "
                "FROM record_inbox ORDER BY received_at_iso DESC LIMIT ?",
                (limit,),
            )
            rows = [
                InboxRow(
                    event_id=r["event_id"], device_id=r["device_id"],
                    mk_date=r["mk_date"], status=r["status"],
                    retry_count=r["retry_count"], last_error=r["last_error"],
                    received_at_iso=r["received_at_iso"], sent_at_iso=r["sent_at_iso"],
                )
                for r in cur.fetchall()
            ]
            return InboxView(rows=rows, received=received, sent=sent, total=received + sent)
        finally:
            conn.close()
```

- [ ] **Step 4: Run tests + lint**

Run: `python -m pytest tests/desktop/test_inbox_reader.py -v && ruff check desktop/presence-tools/pipeline_monitor`
Expected: all PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add desktop/presence-tools/pipeline_monitor/inbox_reader.py tests/desktop/test_inbox_reader.py
git commit -m "feat(monitor): read-only record_inbox reader"
```

---

### Task 4: Oracle sidecar response parser (`parse_select_recent`)

Parse the sidecar `/select_recent` key=value text into `OracleResult` (pane ④). Fetching (docker exec) is added in Task 8's wiring; only the pure parser is tested here.

**Files:**
- Create: `desktop/presence-tools/pipeline_monitor/oracle_reader.py`
- Test: `tests/desktop/test_oracle_reader.py`

**Interfaces:**
- Consumes: `model.OracleRow`, `model.OracleResult`.
- Produces: `oracle_reader.parse_select_recent(text: str) -> OracleResult`.

- [ ] **Step 1: Write the failing test**

Create `tests/desktop/test_oracle_reader.py`:
```python
from pipeline_monitor.oracle_reader import parse_select_recent


def test_parse_ok_rows_most_recent_first():
    text = (
        "count=2\n"
        "ora_code=\n"
        "error_message=\n"
        "row=20260717092300,100,200,300,1,0\n"
        "row=20260717091000,100,200,300,2,0\n"
    )
    res = parse_select_recent(text)
    assert res.ok
    assert res.count == "2"
    assert len(res.rows) == 2
    assert res.rows[0].mk_date == "20260717092300"
    assert res.rows[0].t1_status == "1"
    assert res.rows[0].upcmpflg == "0"


def test_parse_error_response():
    text = "count=0\nora_code=12514\nerror_message=TNS listener\n"
    res = parse_select_recent(text)
    assert not res.ok
    assert res.ora_code == "12514"
    assert "TNS" in res.error_message


def test_parse_empty_result():
    res = parse_select_recent("count=0\nora_code=\nerror_message=\n")
    assert res.ok
    assert res.rows == []
    assert res.count == "0"


def test_row_with_missing_upcmpflg_does_not_crash():
    res = parse_select_recent("row=20260717092300,100,200,300,1\n")
    assert res.rows[0].upcmpflg == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/desktop/test_oracle_reader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline_monitor.oracle_reader'`

- [ ] **Step 3: Implement `parse_select_recent`**

Create `desktop/presence-tools/pipeline_monitor/oracle_reader.py`:
```python
"""oracle-jdbc サイドカー /select_recent 応答(key=value テキスト)の解析（純関数）。

応答フォーマット（既存 _render_recent.py と同一）:
    count=N
    ora_code=            (空 or ORA番号)
    error_message=
    row=MK_DATE,STA_NO1,STA_NO2,STA_NO3,T1_STATUS,UPCMPFLG
    ...(最新順 / DESC)
"""
from __future__ import annotations

from pipeline_monitor.model import OracleResult, OracleRow


def parse_select_recent(text: str) -> OracleResult:
    res = OracleResult()
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if line.startswith("count="):
            res.count = line[len("count="):]
        elif line.startswith("ora_code="):
            res.ora_code = line[len("ora_code="):]
        elif line.startswith("error_message="):
            res.error_message = line[len("error_message="):]
        elif line.startswith("row="):
            parts = line[len("row="):].split(",", 5)
            parts += [""] * (6 - len(parts))   # 欠けた列は空で埋める
            res.rows.append(OracleRow(
                mk_date=parts[0], sta_no1=parts[1], sta_no2=parts[2],
                sta_no3=parts[3], t1_status=parts[4], upcmpflg=parts[5],
            ))
    return res
```

- [ ] **Step 4: Run tests + lint**

Run: `python -m pytest tests/desktop/test_oracle_reader.py -v && ruff check desktop/presence-tools/pipeline_monitor`
Expected: all PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add desktop/presence-tools/pipeline_monitor/oracle_reader.py tests/desktop/test_oracle_reader.py
git commit -m "feat(monitor): parse oracle-jdbc select_recent response"
```

---

### Task 5: Stage linker (`annotate_stages`)

Cross-reference `record_inbox` rows against the MQTT buffer's `event_id`s to label each row's pipeline stage (the trace feature, spec §6).

**Files:**
- Create: `desktop/presence-tools/pipeline_monitor/linker.py`
- Test: `tests/desktop/test_linker.py`

**Interfaces:**
- Consumes: `model.InboxRow`, `model.LinkedRow`.
- Produces: `linker.annotate_stages(inbox_rows: list[InboxRow], mqtt_event_ids: set[str]) -> list[LinkedRow]`.

- [ ] **Step 1: Write the failing test**

Create `tests/desktop/test_linker.py`:
```python
from pipeline_monitor.linker import annotate_stages
from pipeline_monitor.model import InboxRow


def _row(eid, status):
    return InboxRow(event_id=eid, device_id="zero2", mk_date="20260717090000",
                    status=status, retry_count=0, last_error=None,
                    received_at_iso="2026-07-17T09:00:00Z", sent_at_iso=None)


def test_sent_row_seen_on_mqtt_is_full_pipeline():
    linked = annotate_stages([_row("e1", "sent")], {"e1"})
    assert linked[0].mqtt_seen is True
    assert "Oracle" in linked[0].stage and "✓" in linked[0].stage


def test_received_row_is_stuck_before_oracle():
    linked = annotate_stages([_row("e2", "received")], {"e2"})
    assert linked[0].mqtt_seen is True
    assert "inbox" in linked[0].stage
    assert "✓" not in linked[0].stage        # not yet in Oracle


def test_row_not_in_mqtt_buffer():
    # arrived before the monitor started subscribing -> mqtt_seen False, still valid
    linked = annotate_stages([_row("e3", "sent")], set())
    assert linked[0].mqtt_seen is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/desktop/test_linker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline_monitor.linker'`

- [ ] **Step 3: Implement `annotate_stages`**

Create `desktop/presence-tools/pipeline_monitor/linker.py`:
```python
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
```

- [ ] **Step 4: Run tests + lint**

Run: `python -m pytest tests/desktop/test_linker.py -v && ruff check desktop/presence-tools/pipeline_monitor`
Expected: all PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add desktop/presence-tools/pipeline_monitor/linker.py tests/desktop/test_linker.py
git commit -m "feat(monitor): annotate record_inbox rows with pipeline stage"
```

---

### Task 6: MQTT tail subprocess (`MqttTail`)

Manage a background `mosquitto_sub -v` process, feed each line through `mqtt_summarize`, keep a bounded ring buffer, and auto-restart on death. The line-ingestion logic is unit-tested by feeding lines directly; the subprocess spawn is exercised in Task 9's live check.

**Files:**
- Modify: `desktop/presence-tools/pipeline_monitor/mqtt_tail.py` (append `MqttTail`)
- Test: `tests/desktop/test_mqtt_tail.py`

**Interfaces:**
- Consumes: `mqtt_summarize`, `model.MqttMsg`.
- Produces:
  - `mqtt_tail.MqttTail(host: str, port: int, topic: str = "presence/#", maxlen: int = 500, clock=time.time)`
  - `.ingest_line(line: str) -> None` — split `-v` output `"<topic> <payload>"`, summarize, append.
  - `.messages() -> list[MqttMsg]` — snapshot oldest→newest.
  - `.event_ids() -> set[str]` — all non-None event_ids currently buffered.
  - `.start() -> None` / `.stop() -> None` — spawn/kill the reader thread + subprocess (not unit-tested).

- [ ] **Step 1: Write the failing test**

Create `tests/desktop/test_mqtt_tail.py`:
```python
from pipeline_monitor.mqtt_tail import MqttTail


def _tail(**kw):
    # fixed clock so ts is deterministic
    return MqttTail("h", 1883, clock=lambda: 1000.0, **kw)


def test_ingest_v_line_splits_topic_and_payload():
    t = _tail()
    t.ingest_line('presence/status/zero2 online')
    msgs = t.messages()
    assert len(msgs) == 1
    assert msgs[0].kind == "status"
    assert msgs[0].device_id == "zero2"


def test_ingest_record_line_with_spaces_in_json():
    t = _tail()
    line = 'presence/record {"event_id": "abc", "device_id": "zero2", "t1_status": 1, "mk_date": "20260717090000", "sta_no1":"1","sta_no2":"2","sta_no3":"3","schema_version":1}'
    t.ingest_line(line)
    assert t.messages()[0].event_id == "abc"
    assert "abc" in t.event_ids()


def test_ring_buffer_bounds_length():
    t = _tail(maxlen=2)
    for i in range(5):
        t.ingest_line(f'presence/status/dev{i} online')
    msgs = t.messages()
    assert len(msgs) == 2
    assert msgs[-1].device_id == "dev4"     # newest kept


def test_blank_line_ignored():
    t = _tail()
    t.ingest_line("")
    t.ingest_line("   ")
    assert t.messages() == []


def test_event_ids_excludes_none():
    t = _tail()
    t.ingest_line('presence/heartbeat/zero2 {"uptime_s":1}')  # no event_id
    assert t.event_ids() == set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/desktop/test_mqtt_tail.py -v`
Expected: FAIL — `AttributeError` / `ImportError: cannot import name 'MqttTail'`

- [ ] **Step 3: Implement `MqttTail`**

Append to `desktop/presence-tools/pipeline_monitor/mqtt_tail.py`:
```python
import shutil
import subprocess  # noqa: S404
import threading
import time
from collections import deque
from collections.abc import Callable


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
            raise RuntimeError("mosquitto_sub が見つかりません（mosquitto-clients を入れてください）")
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
```

- [ ] **Step 4: Run tests + lint**

Run: `python -m pytest tests/desktop/test_mqtt_tail.py -v && ruff check desktop/presence-tools/pipeline_monitor`
Expected: all PASS, ruff clean. (`# noqa: S404/S603` cover subprocess lint warnings; keep them.)

- [ ] **Step 5: Commit**

```bash
git add desktop/presence-tools/pipeline_monitor/mqtt_tail.py tests/desktop/test_mqtt_tail.py
git commit -m "feat(monitor): mosquitto_sub tail with ring buffer + auto-restart"
```

---

### Task 7: Oracle fetch method (`OracleRecentReader.fetch`)

Wrap the `docker exec … wget /select_recent` call and hand its output to `parse_select_recent`. The command build is unit-tested; the actual docker call is exercised in Task 9.

**Files:**
- Modify: `desktop/presence-tools/pipeline_monitor/oracle_reader.py` (append `OracleRecentReader`)
- Test: `tests/desktop/test_oracle_fetch.py`

**Interfaces:**
- Consumes: `parse_select_recent`.
- Produces:
  - `oracle_reader.OracleQuery` dataclass: `host, port, service, user, table, sta_no1, sta_no2, sta_no3, limit`.
  - `oracle_reader.build_post_body(q: OracleQuery, password: str) -> str` (url-encoded).
  - `oracle_reader.OracleRecentReader(jdbc_container: str, sidecar_url: str)` with `.fetch(q, password, runner=subprocess-based) -> OracleResult`, where `runner: Callable[[list[str], str], str]` runs a command with stdin and returns stdout (injectable for tests).

- [ ] **Step 1: Write the failing test**

Create `tests/desktop/test_oracle_fetch.py`:
```python
from pipeline_monitor.oracle_reader import (
    OracleQuery,
    OracleRecentReader,
    build_post_body,
)

Q = OracleQuery(host="10.166.5.93", port="1521", service="HHC001", user="u",
                table="HF1RCM01", sta_no1="100", sta_no2="200", sta_no3="300",
                limit=30)


def test_build_post_body_is_urlencoded_and_has_jdbc_url():
    body = build_post_body(Q, "secret&pw")
    assert "url=jdbc%3Aoracle%3Athin%3A%40" in body
    assert "table_name=HF1RCM01" in body
    assert "sta_no1=100" in body
    assert "limit=30" in body
    assert "secret%26pw" in body          # password url-encoded


def test_fetch_parses_runner_output():
    captured = {}

    def fake_runner(cmd, stdin):
        captured["cmd"] = cmd
        captured["stdin"] = stdin
        return "count=1\nora_code=\nerror_message=\nrow=20260717090000,100,200,300,1,0\n"

    reader = OracleRecentReader("presence-oracle-jdbc", "http://127.0.0.1:8086")
    res = reader.fetch(Q, "pw", runner=fake_runner)
    assert res.ok
    assert res.rows[0].mk_date == "20260717090000"
    assert "presence-oracle-jdbc" in captured["cmd"]
    assert "/select_recent" in " ".join(captured["cmd"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/desktop/test_oracle_fetch.py -v`
Expected: FAIL — `ImportError: cannot import name 'OracleQuery'`

- [ ] **Step 3: Implement fetch layer**

Append to `desktop/presence-tools/pipeline_monitor/oracle_reader.py`:
```python
import subprocess  # noqa: S404
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class OracleQuery:
    host: str
    port: str
    service: str
    user: str
    table: str
    sta_no1: str
    sta_no2: str
    sta_no3: str
    limit: int = 30


def build_post_body(q: OracleQuery, password: str) -> str:
    return urllib.parse.urlencode({
        "url": f"jdbc:oracle:thin:@{q.host}:{q.port}/{q.service}",
        "user": q.user,
        "password": password,
        "table_name": q.table,
        "sta_no1": q.sta_no1,
        "sta_no2": q.sta_no2,
        "sta_no3": q.sta_no3,
        "limit": str(q.limit),
    })


def _default_runner(cmd: list[str], stdin: str) -> str:
    # サイドカーは presence-net 内のみ待受なので docker exec 経由で叩く。
    proc = subprocess.run(  # noqa: S603
        cmd, input=stdin, capture_output=True, text=True, timeout=45, check=False,
    )
    return proc.stdout


class OracleRecentReader:
    def __init__(self, jdbc_container: str, sidecar_url: str):
        self._container = jdbc_container
        self._url = sidecar_url

    def fetch(
        self,
        q: OracleQuery,
        password: str,
        runner: Callable[[list[str], str], str] = _default_runner,
    ) -> OracleResult:
        body = build_post_body(q, password)
        cmd = [
            "docker", "exec", "-i", self._container, "wget", "-q", "--timeout=40",
            "--header=Content-Type: application/x-www-form-urlencoded",
            f"--post-data={body}", "-O", "-", f"{self._url}/select_recent",
        ]
        return parse_select_recent(runner(cmd, ""))
```

Note: `OracleResult`/`OracleRow`/`parse_select_recent` are already defined at the top of this file (Task 4) — do not redefine them.

- [ ] **Step 4: Run tests + lint**

Run: `python -m pytest tests/desktop/test_oracle_fetch.py -v && ruff check desktop/presence-tools/pipeline_monitor`
Expected: all PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add desktop/presence-tools/pipeline_monitor/oracle_reader.py tests/desktop/test_oracle_fetch.py
git commit -m "feat(monitor): oracle fetch via docker-exec sidecar call"
```

---

### Task 8: Config loader + curses dashboard + entrypoint

Wire everything: load non-secret Oracle connection info from `profiles.yaml`, get the password from the bridge container env, drive the four panes, handle `r`/`q`/resize. The config loader is unit-tested; the curses `Dashboard` is manual (spec §9).

**Files:**
- Create: `desktop/presence-tools/pipeline_monitor/config.py`
- Create: `desktop/presence-tools/pipeline_monitor/dashboard.py`
- Create: `desktop/presence-tools/pipeline_monitor/__main__.py`
- Test: `tests/desktop/test_config.py`

**Interfaces:**
- Consumes: all prior modules.
- Produces:
  - `config.load_oracle_query(profiles_yaml: str, profile_name: str, limit: int) -> OracleQuery` (raises `ValueError` if station numbers absent).
  - `dashboard.run(stdscr, deps)` — curses main loop (no test).
  - `python -m pipeline_monitor` entrypoint.

- [ ] **Step 1: Write the failing test (config loader)**

Create `tests/desktop/test_config.py`:
```python
import pytest

from pipeline_monitor.config import load_oracle_query

_YAML = """
profiles:
  HIME-H-REAP:
    oracle:
      host: 10.166.5.93
      port: 1521
      service_name: HHC001
      user: HHCUSER
      table_name: HF1RCM01
    station:
      sta_no1: "100"
      sta_no2: "200"
      sta_no3: "300"
"""

_YAML_NO_STATION = """
profiles:
  HIME-H-REAP:
    oracle: {host: h, service_name: s, user: u, table_name: t}
"""


def test_load_oracle_query(tmp_path):
    p = tmp_path / "profiles.yaml"
    p.write_text(_YAML)
    q = load_oracle_query(str(p), "HIME-H-REAP", limit=30)
    assert q.host == "10.166.5.93"
    assert q.service == "HHC001"
    assert q.table == "HF1RCM01"
    assert q.sta_no1 == "100"
    assert q.limit == 30


def test_missing_station_raises(tmp_path):
    p = tmp_path / "profiles.yaml"
    p.write_text(_YAML_NO_STATION)
    with pytest.raises(ValueError, match="station"):
        load_oracle_query(str(p), "HIME-H-REAP", limit=30)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/desktop/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline_monitor.config'`

- [ ] **Step 3: Implement the config loader**

Create `desktop/presence-tools/pipeline_monitor/config.py`:
```python
"""profiles.yaml から非秘密の Oracle 接続情報を読む（既存ツールと同方式）。"""
from __future__ import annotations

import yaml

from pipeline_monitor.oracle_reader import OracleQuery


def load_oracle_query(profiles_yaml: str, profile_name: str, limit: int) -> OracleQuery:
    with open(profiles_yaml) as f:
        data = yaml.safe_load(f) or {}
    profile = (data.get("profiles") or {}).get(profile_name) or {}
    oracle = profile.get("oracle") or {}
    station = profile.get("station") or {}
    s1, s2, s3 = station.get("sta_no1"), station.get("sta_no2"), station.get("sta_no3")
    if not (s1 and s2 and s3):
        raise ValueError(
            f"{profile_name} の station(sta_no1/2/3) が profiles.yaml にありません"
        )
    return OracleQuery(
        host=str(oracle.get("host", "")),
        port=str(oracle.get("port", "1521")),
        service=str(oracle.get("service_name", "")),
        user=str(oracle.get("user", "")),
        table=str(oracle.get("table_name", "HF1RCM01")),
        sta_no1=str(s1), sta_no2=str(s2), sta_no3=str(s3), limit=limit,
    )
```

- [ ] **Step 4: Run config test + lint**

Run: `python -m pytest tests/desktop/test_config.py -v && ruff check desktop/presence-tools/pipeline_monitor`
Expected: PASS, ruff clean.

- [ ] **Step 5: Implement the curses dashboard**

Create `desktop/presence-tools/pipeline_monitor/dashboard.py`:
```python
"""4ペイン curses ダッシュボード。描画とキー処理のみ。ロジックは各 reader が持つ。

ペイン配置:
   ┌ ①子Pi別受信 ─────┬ ②MQTT生ログ ─┐
   ├─────────────────┼─────────────┤
   └ ③record_inbox ──┴ ④Oracle ────┘
"""
from __future__ import annotations

import curses
import time
from dataclasses import dataclass
from typing import Callable

from pipeline_monitor.config import load_oracle_query
from pipeline_monitor.inbox_reader import RecordInboxReader
from pipeline_monitor.linker import annotate_stages
from pipeline_monitor.mqtt_tail import MqttTail
from pipeline_monitor.oracle_reader import OracleRecentReader, OracleResult
from pipeline_monitor.rollup import child_rollup

ORACLE_REFRESH_S = 15.0
HEARTBEAT_TIMEOUT_S = 60.0


@dataclass
class Deps:
    tail: MqttTail
    inbox: RecordInboxReader
    oracle: OracleRecentReader
    oracle_query_loader: Callable[[], object]     # -> OracleQuery
    password_getter: Callable[[], str]
    ssid_getter: Callable[[], str]


def _safe(fn, default):
    try:
        return fn(), None
    except Exception as e:  # noqa: BLE001  ペインは落とさない
        return default, f"{type(e).__name__}: {e}"


def _addstr(win, y, x, text, attr=0):
    h, w = win.getmaxyx()
    if 0 <= y < h and 0 <= x < w:
        win.addnstr(y, x, text, max(0, w - x - 1), attr)


def _draw_children(win, msgs, now):
    win.erase()
    win.box()
    _addstr(win, 0, 2, " ①子Pi別 受信 ", curses.A_BOLD)
    stats = child_rollup(msgs, now=now, heartbeat_timeout=HEARTBEAT_TIMEOUT_S)
    _addstr(win, 1, 2, f"{'device':<12}{'rec':>4} {'状態':<8}{'hb':>6} 最新mk")
    for i, s in enumerate(stats, start=2):
        badge = {"online": "🟢", "offline": "🔴", "stale": "🟡"}.get(s.state, "⚪")
        hb = f"{int(s.last_heartbeat_age)}s" if s.last_heartbeat_age is not None else "-"
        mk = s.last_record_mk_date or "-"
        _addstr(win, i, 2, f"{s.device_id:<12}{s.record_count:>4} {badge}{s.state:<7}{hb:>6} {mk}")
    win.noutrefresh()


def _draw_mqtt(win, msgs):
    win.erase()
    win.box()
    _addstr(win, 0, 2, " ②MQTT生ログ (presence/#) ", curses.A_BOLD)
    h, _ = win.getmaxyx()
    visible = msgs[-(h - 2):] if len(msgs) > h - 2 else msgs
    for i, m in enumerate(visible, start=1):
        ts = time.strftime("%H:%M:%S", time.localtime(m.ts))
        _addstr(win, i, 2, f"{ts} {m.summary}")
    win.noutrefresh()


def _draw_inbox(win, msgs, inbox_reader):
    win.erase()
    win.box()
    _addstr(win, 0, 2, " ③MQTT→Oracle (record_inbox) ", curses.A_BOLD)
    view, err = _safe(inbox_reader.read, None)
    if err or view is None:
        _addstr(win, 1, 2, f"⚠ 取得失敗: {err}")
        win.noutrefresh()
        return
    linked = annotate_stages(view.rows, {m.event_id for m in msgs if m.event_id})
    _addstr(win, 1, 2, f"received(滞留)={view.received}  sent={view.sent}  合計={view.total}",
            curses.A_BOLD)
    h, _ = win.getmaxyx()
    for i, lk in enumerate(linked[: h - 3], start=2):
        r = lk.inbox
        err_s = f" !{r.last_error[:18]}" if r.last_error else ""
        _addstr(win, i, 2,
                f"{(r.device_id or '?'):<8} {r.event_id[:10]:<10} {lk.stage:<18} "
                f"r{r.retry_count}{err_s}")
    win.noutrefresh()


def _draw_oracle(win, result: OracleResult | None, err, ssid, last_at):
    win.erase()
    win.box()
    _addstr(win, 0, 2, " ④Oracle テーブル ([r]更新) ", curses.A_BOLD)
    if err or result is None:
        _addstr(win, 1, 2, f"⚠ 取得失敗: {err}")
        _addstr(win, 2, 2, f"SSID={ssid}（工場網でないと届きません）")
        win.noutrefresh()
        return
    if not result.ok:
        _addstr(win, 1, 2, f"⚠ ORA-{result.ora_code} {result.error_message[:30]}")
        win.noutrefresh()
        return
    _addstr(win, 1, 2, f"{'日時':<19} {'種別':<7} STA(1/2/3)   最終更新={time.strftime('%H:%M:%S', time.localtime(last_at))}")
    badge = {"1": "🟢ENTER", "2": "🔴EXIT"}
    h, _ = win.getmaxyx()
    for i, r in enumerate(result.rows[: h - 3], start=2):
        mk = r.mk_date
        disp = (f"{mk[0:4]}-{mk[4:6]}-{mk[6:8]} {mk[8:10]}:{mk[10:12]}:{mk[12:14]}"
                if len(mk) == 14 and mk.isdigit() else mk)
        b = badge.get(r.t1_status, f"?({r.t1_status})")
        _addstr(win, i, 2, f"{disp:<19} {b:<7} {r.sta_no1}/{r.sta_no2}/{r.sta_no3}")
    win.noutrefresh()


def run(stdscr, deps: Deps) -> None:
    curses.curs_set(0)
    stdscr.nodelay(True)
    deps.tail.start()
    oracle_result: OracleResult | None = None
    oracle_err: str | None = None
    oracle_last = 0.0

    def refresh_oracle():
        nonlocal oracle_result, oracle_err, oracle_last
        def _do():
            q = deps.oracle_query_loader()
            return deps.oracle.fetch(q, deps.password_getter())
        oracle_result, oracle_err = _safe(_do, None)
        oracle_last = time.time()

    try:
        while True:
            now = time.time()
            if now - oracle_last >= ORACLE_REFRESH_S:
                refresh_oracle()

            msgs = deps.tail.messages()
            h, w = stdscr.getmaxyx()
            mid_y, mid_x = h // 2, w // 2
            stdscr.erase()
            _addstr(stdscr, 0, 0,
                    " presence パイプライン監視  [r]Oracle即時 [q]終了 ", curses.A_REVERSE)
            tl = stdscr.derwin(mid_y - 1, mid_x, 1, 0)
            tr = stdscr.derwin(mid_y - 1, w - mid_x, 1, mid_x)
            bl = stdscr.derwin(h - mid_y - 1, mid_x, mid_y, 0)
            br = stdscr.derwin(h - mid_y - 1, w - mid_x, mid_y, mid_x)
            _draw_children(tl, msgs, now)
            _draw_mqtt(tr, msgs)
            _draw_inbox(bl, msgs, deps.inbox)
            _draw_oracle(br, oracle_result, oracle_err, deps.ssid_getter(), oracle_last)
            stdscr.noutrefresh()
            curses.doupdate()

            ch = stdscr.getch()
            if ch in (ord("q"), ord("Q")):
                break
            if ch in (ord("r"), ord("R")):
                refresh_oracle()
            time.sleep(1.0)
    finally:
        deps.tail.stop()
```

- [ ] **Step 6: Implement the entrypoint**

Create `desktop/presence-tools/pipeline_monitor/__main__.py`:
```python
"""python -m pipeline_monitor で起動する配線層。"""
from __future__ import annotations

import curses
import os
import subprocess  # noqa: S404

from pipeline_monitor.config import load_oracle_query
from pipeline_monitor.dashboard import Deps, run
from pipeline_monitor.inbox_reader import RecordInboxReader
from pipeline_monitor.mqtt_tail import MqttTail
from pipeline_monitor.oracle_reader import OracleRecentReader

MQTT_HOST = os.environ.get("MQTT_HOST", "10.42.0.1")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
INBOX_DB = os.environ.get(
    "RECORD_INBOX_DB", "/var/lib/presence-logger/bridge_record_buf.db")
PROFILES_YAML = os.environ.get("PROFILES_YAML", "/etc/presence-logger/profiles.yaml")
PROFILE_NAME = os.environ.get("PROFILE_NAME", "HIME-H-REAP")
BRIDGE_CONTAINER = os.environ.get("BRIDGE_CONTAINER", "presence-bridge")
JDBC_CONTAINER = os.environ.get("JDBC_CONTAINER", "presence-oracle-jdbc")
SIDECAR_URL = os.environ.get("SIDECAR_IN", "http://127.0.0.1:8086")
PW_ENV = os.environ.get("PW_ENV", "ORACLE_PASSWORD_HHC")
LIMIT = int(os.environ.get("ORACLE_LIMIT", "30"))


def _get_password() -> str:
    out = subprocess.run(  # noqa: S603
        ["docker", "exec", BRIDGE_CONTAINER, "printenv", PW_ENV],  # noqa: S607
        capture_output=True, text=True, check=False,
    )
    return out.stdout.strip()


def _get_ssid() -> str:
    out = subprocess.run(  # noqa: S603
        ["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"],  # noqa: S607
        capture_output=True, text=True, check=False,
    )
    for line in out.stdout.splitlines():
        if line.startswith("yes:"):
            return line.split(":", 1)[1]
    return "(不明)"


def main() -> int:
    deps = Deps(
        tail=MqttTail(MQTT_HOST, MQTT_PORT),
        inbox=RecordInboxReader(INBOX_DB),
        oracle=OracleRecentReader(JDBC_CONTAINER, SIDECAR_URL),
        oracle_query_loader=lambda: load_oracle_query(PROFILES_YAML, PROFILE_NAME, LIMIT),
        password_getter=_get_password,
        ssid_getter=_get_ssid,
    )
    curses.wrapper(run, deps)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 7: Compile-check + lint (no runtime yet)**

Run: `python -m py_compile desktop/presence-tools/pipeline_monitor/*.py && ruff check desktop/presence-tools/pipeline_monitor && python -m pytest tests/desktop -v`
Expected: no compile errors, ruff clean, all unit tests PASS.

- [ ] **Step 8: Commit**

```bash
git add desktop/presence-tools/pipeline_monitor/config.py \
        desktop/presence-tools/pipeline_monitor/dashboard.py \
        desktop/presence-tools/pipeline_monitor/__main__.py \
        tests/desktop/test_config.py
git commit -m "feat(monitor): config loader + curses 4-pane dashboard + entrypoint"
```

---

### Task 9: Wrapper script, desktop launcher, live verification & docs

Give operators a one-click launcher (matching existing tools) and verify the whole thing against the real hub.

**Files:**
- Create: `desktop/presence-tools/pipeline-monitor.sh`
- Create: `desktop/launchers/パイプライン監視.desktop`
- Modify: `desktop/README.md` (add a short section)

**Interfaces:** none (delivery + verification).

- [ ] **Step 1: Create the wrapper script**

Create `desktop/presence-tools/pipeline-monitor.sh`:
```bash
#!/usr/bin/env bash
# pipeline-monitor.sh
# 子Pi→MQTT→bridge(record_inbox)→Oracle を1画面で追う監視TUIを起動する。
# 既存の「記録モニタ」(自Piカメラ検知) とは別物: こちらは子Pi経路とDB段階が対象。
#   使い方:  bash pipeline-monitor.sh
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 前提チェック（欠けていても分かりやすく落ちるように）
if ! command -v mosquitto_sub >/dev/null 2>&1; then
    echo "mosquitto_sub がありません。次で導入してください:"
    echo "    sudo apt-get install -y mosquitto-clients"
    exit 1
fi

SSID="$(nmcli -t -f ACTIVE,SSID dev wifi 2>/dev/null | awk -F: '$1=="yes"{print $2; exit}')"
echo "===================================================================="
echo " presence パイプライン監視（子Pi→MQTT→Oracle）"
echo "   現在のSSID : ${SSID:-(不明)}"
echo "   ①子Pi別受信  ②MQTT生ログ  ③record_inbox  ④Oracleテーブル"
echo "   [r] Oracle即時更新   [q] 終了"
echo "   ※④は SSID が工場網(既定 HIME-H-REAP)のときだけ表示されます"
echo "===================================================================="

# パッケージの親を PYTHONPATH に載せて -m 実行。
PYTHONPATH="$DIR" exec python3 -m pipeline_monitor
```

- [ ] **Step 2: Make it executable + create the launcher**

Run: `chmod +x desktop/presence-tools/pipeline-monitor.sh`

Create `desktop/launchers/パイプライン監視.desktop` (mirror an existing launcher's structure; check `desktop/launchers/記録モニタ.desktop` for the exact `Exec`/`Terminal`/`Path` conventions and match them):
```ini
[Desktop Entry]
Type=Application
Name=パイプライン監視
Comment=子Pi→MQTT→Oracle を1画面で追う
Terminal=true
Exec=bash -lc 'cd "$HOME/projects/presence-logger" && bash desktop/presence-tools/pipeline-monitor.sh; exec bash'
Icon=utilities-system-monitor
Categories=Utility;
```

- [ ] **Step 3: Verify — unit suite green**

Run: `python -m pytest tests/desktop -v && ruff check desktop/presence-tools/pipeline_monitor tests/desktop`
Expected: all tests PASS, ruff clean.

- [ ] **Step 4: Verify — MQTT reachability & live tail**

Run: `timeout 5 mosquitto_sub -h 10.42.0.1 -p 1883 -t 'presence/#' -v || echo "(no messages in 5s — ok if no child is publishing)"`
Expected: prints child messages if any are flowing, or the fallback note. Confirms the broker is reachable from the host.

- [ ] **Step 5: Verify — record_inbox readable**

Run: `python3 -c "import sys; sys.path.insert(0,'desktop/presence-tools'); from pipeline_monitor.inbox_reader import RecordInboxReader; v=RecordInboxReader('/var/lib/presence-logger/bridge_record_buf.db').read(5); print('received',v.received,'sent',v.sent,'total',v.total)"`
Expected: prints counts without error (DB exists because bridge created it). If `FileNotFoundError`, note bridge hasn't received any record yet.

- [ ] **Step 6: Verify — full TUI launches**

Run: `bash desktop/presence-tools/pipeline-monitor.sh`
Expected: four bordered panes render; pane ① shows any children currently heartbeating, ② streams live MQTT, ③ shows inbox counts, ④ shows Oracle rows (if on the factory SSID) or a `⚠`/SSID note otherwise. Press `r` (Oracle refreshes), then `q` (clean exit, no leftover `mosquitto_sub`: verify with `pgrep -af mosquitto_sub` → nothing from this session).

- [ ] **Step 7: Document in desktop/README.md**

Add this section near the other tool descriptions in `desktop/README.md`:
```markdown
### パイプライン監視（pipeline-monitor）

子Pi → MQTT → bridge(record_inbox) → Oracle のデータの流れを1画面で追う curses TUI。
`記録モニタ`（自Piカメラ検知のログ流し）とは別物で、子Pi経路とDB段階の追跡が目的。

- ①子Pi別受信 / ②MQTT生ログ(presence/#) / ③record_inbox(received・sent・滞留) / ④Oracleテーブル
- 同じ event_id を ②→③→④ と辿れる。`[r]`でOracle即時更新、`[q]`で終了。
- 起動: デスクトップの「パイプライン監視」、または `bash desktop/presence-tools/pipeline-monitor.sh`
- 依存: `mosquitto-clients`（`mosquitto_sub`）。④は工場網SSID接続時のみ表示。
```

- [ ] **Step 8: Commit**

```bash
git add desktop/presence-tools/pipeline-monitor.sh "desktop/launchers/パイプライン監視.desktop" desktop/README.md
git commit -m "feat(monitor): launcher + wrapper + docs for pipeline-monitor"
```

---

## Self-Review

**Spec coverage:**
- §3 architecture (host process, 4 sources) → Tasks 6 (MQTT), 3 (inbox), 7 (Oracle), 8 (dashboard). ✓
- §4 component split → one module per unit across Tasks 1–8. ✓
- §5 four panes → `dashboard.py` `_draw_*` (Task 8); data from Tasks 2/3/4/7. ✓
- §5-② `presence/#` all topics → `MqttTail` default topic (Task 6) + wrapper (Task 9). ✓
- §5-④ 15s auto + `r` manual → `ORACLE_REFRESH_S` + key handling (Task 8). ✓
- §6 event_id trace → `annotate_stages` (Task 5), rendered in pane ③ (Task 8). ✓
- §7 error isolation → `_safe()` per pane + auto-restart in `MqttTail._run` (Tasks 6, 8). ✓
- §7 read-only inbox → `?mode=ro` + test (Task 3). ✓
- §8 placement/launcher, coexist with existing tools → Task 9. ✓
- §9 pure-logic unit tests, curses excluded → Tasks 1–8 tests; dashboard manual (Task 9 live checks). ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code; commands have expected output. The one `desktop/launchers/*.desktop` step references matching an existing launcher — that is a real, inspectable file, not a placeholder.

**Type consistency:** `MqttMsg`, `ChildStat`, `InboxRow`, `InboxView`, `OracleRow`, `OracleResult`, `LinkedRow` defined once in `model.py` (Task 1) and consumed unchanged. `OracleQuery` defined in `oracle_reader.py` (Task 7) and consumed by `config.py` (Task 8). Function names — `mqtt_summarize`, `child_rollup`, `RecordInboxReader.read`, `parse_select_recent`, `annotate_stages`, `MqttTail.ingest_line/messages/event_ids`, `build_post_body`, `OracleRecentReader.fetch`, `load_oracle_query` — are used identically in producers and consumers. ✓

**Open note for executor:** Task 2's `rollup.py` reads `mk_date` by re-parsing `MqttMsg.raw`; keep `import json` at module top (not inline) to satisfy ruff. Task 4 and Task 7 share `oracle_reader.py` — Task 7 appends, does not redefine the Task 4 symbols.
