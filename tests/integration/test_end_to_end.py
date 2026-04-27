import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from services.bridge.src.inbox import InboxRepository, InboxEvent
from services.bridge.src.profile_resolver import ProfileResolver
from services.bridge.src.circuit_breaker import CircuitBreaker
from services.bridge.src.sender import Sender, SenderDeps
from services.bridge.src.oracle_client import MergeResult
from tests.integration.fakes import FakeOracle, FakeMqtt, FakeNetwork, FakeTimeWatcher


def _profiles():
    return {
        "factory_a_wifi": {
            "description": "A",
            "sntp": {"servers": ["ntp.a"]},
            "oracle": {
                "client_mode": "thin", "auth_mode": "basic",
                "host": "h", "port": 1521, "service_name": "S",
                "user": "u", "password": "p", "table_name": "HF1RCM01",
            },
        }
    }


def _ingest(inbox: InboxRepository, payload: dict, *, ssid: str = "factory_a_wifi") -> None:
    e = InboxEvent(
        event_id=payload["event_id"],
        event_type=payload["event"],
        mk_date=payload.get("event_time"),
        monotonic_ns=int(payload["monotonic_ns"]),
        wall_synced=bool(payload["wall_clock_synced"]),
        device_id=payload["device_id"],
        score=payload.get("score"),
        raw_payload=json.dumps(payload),
        status="received",
        ssid_at_receive=ssid,
        profile_at_send=None,
        mk_date_committed=None,
        received_at_iso=datetime.now(timezone.utc).isoformat(),
        sent_at_iso=None,
        retry_count=0,
        next_retry_at_iso=None,
        last_error=None,
    )
    inbox.insert_received(e)


def _make_sender(tmp_path: Path, *, oracle: FakeOracle, mqtt: FakeMqtt,
                  network: FakeNetwork, time_watcher: FakeTimeWatcher) -> tuple[Sender, InboxRepository]:
    inbox = InboxRepository(tmp_path / "inbox.db"); inbox.init()
    resolver = ProfileResolver(profiles=_profiles(), unknown_policy="hold")
    breaker = CircuitBreaker(half_open_after_seconds=900, permanent_codes={942})
    sender = Sender(deps=SenderDeps(
        inbox=inbox, resolver=resolver, breaker=breaker,
        network=network, time_watcher=time_watcher,
        oracle=oracle, mqtt=mqtt,
        device_cfg={"device_id": "rpi", "station": {"sta_no1": "001", "sta_no2": "A", "sta_no3": "01"}},
        topic_ack="presence/event/ack",
    ))
    return sender, inbox


def test_e2e_normal_enter_then_exit_writes_two_rows(tmp_path: Path):
    oracle = FakeOracle()
    mqtt = FakeMqtt()
    sender, inbox = _make_sender(tmp_path, oracle=oracle, mqtt=mqtt,
                                  network=FakeNetwork(), time_watcher=FakeTimeWatcher())
    _ingest(inbox, {
        "event_id": "e1", "event": "ENTER", "event_time": "20260427120000",
        "event_time_iso": "2026-04-27T12:00:00+09:00",
        "monotonic_ns": 1, "wall_clock_synced": True,
        "device_id": "rpi", "score": 0.9, "schema_version": 1,
    })
    _ingest(inbox, {
        "event_id": "e2", "event": "EXIT", "event_time": "20260427120010",
        "event_time_iso": "2026-04-27T12:00:10+09:00",
        "monotonic_ns": 2, "wall_clock_synced": True,
        "device_id": "rpi", "score": 0.0, "schema_version": 1,
    })
    sender.run_once(now=datetime(2026, 4, 27, 12, 0, 11, tzinfo=timezone.utc))
    assert len(oracle.calls) == 2
    statuses = [c["t1_status"] for c in oracle.calls]
    assert statuses == [1, 2]
    assert {a["event_id"] for a in mqtt.acks} == {"e1", "e2"}


def test_e2e_oracle_down_then_up_recovers(tmp_path: Path):
    oracle = FakeOracle(canned=[
        MergeResult(rows_affected=0, ora_code=12541, error_message="ORA-12541: TNS:no listener"),
        MergeResult(rows_affected=1, ora_code=None, error_message=""),
    ])
    mqtt = FakeMqtt()
    sender, inbox = _make_sender(tmp_path, oracle=oracle, mqtt=mqtt,
                                  network=FakeNetwork(), time_watcher=FakeTimeWatcher())
    _ingest(inbox, {
        "event_id": "e1", "event": "ENTER", "event_time": "20260427120000",
        "event_time_iso": "2026-04-27T12:00:00+09:00",
        "monotonic_ns": 1, "wall_clock_synced": True,
        "device_id": "rpi", "score": 0.9, "schema_version": 1,
    })
    # First run: failure, retry scheduled.
    sender.run_once(now=datetime(2026, 4, 27, 12, 0, 1, tzinfo=timezone.utc))
    assert len(oracle.calls) == 1
    assert mqtt.acks == []
    row = inbox.get("e1")
    assert row.retry_count == 1 and row.last_error and "12541" in row.last_error

    # Second run after retry window passes: success.
    sender.run_once(now=datetime(2026, 4, 27, 12, 0, 30, tzinfo=timezone.utc))
    assert len(oracle.calls) == 2
    assert len(mqtt.acks) == 1


def test_e2e_unknown_ssid_holds_then_flushes(tmp_path: Path):
    oracle = FakeOracle()
    mqtt = FakeMqtt()
    network = FakeNetwork(ssid=None)
    sender, inbox = _make_sender(tmp_path, oracle=oracle, mqtt=mqtt,
                                  network=network, time_watcher=FakeTimeWatcher())
    _ingest(inbox, {
        "event_id": "e1", "event": "ENTER", "event_time": "20260427120000",
        "event_time_iso": "2026-04-27T12:00:00+09:00",
        "monotonic_ns": 1, "wall_clock_synced": True,
        "device_id": "rpi", "score": 0.9, "schema_version": 1,
    }, ssid="guest_wifi")
    sender.run_once(now=datetime(2026, 4, 27, 12, 0, 1, tzinfo=timezone.utc))
    assert oracle.calls == []
    # SSID returns to known.
    network.ssid = "factory_a_wifi"
    sender.run_once(now=datetime(2026, 4, 27, 12, 0, 5, tzinfo=timezone.utc))
    assert len(oracle.calls) == 1


def test_e2e_unsynced_event_then_sync_correction(tmp_path: Path):
    from services.bridge.src.time_watcher import SyncBaseline
    # Initial: unsynced, baseline=None.
    tw = FakeTimeWatcher(is_synced=False, baseline=None)
    oracle = FakeOracle()
    mqtt = FakeMqtt()
    sender, inbox = _make_sender(tmp_path, oracle=oracle, mqtt=mqtt,
                                  network=FakeNetwork(), time_watcher=tw)
    _ingest(inbox, {
        "event_id": "e1", "event": "ENTER", "event_time": None,
        "event_time_iso": None,
        "monotonic_ns": 6_200_000_000, "wall_clock_synced": False,
        "device_id": "rpi", "score": 0.9, "schema_version": 1,
    })
    sender.run_once(now=datetime(2026, 4, 27, 12, 0, 1, tzinfo=timezone.utc))
    assert oracle.calls == []  # held until sync

    # Sync arrives at 17:23:51 JST with monotonic 13_000_000_000.
    tw.is_synced = True
    tw.baseline = SyncBaseline(
        sync_wall=datetime(2026, 4, 27, 17, 23, 51, tzinfo=timezone(timedelta(hours=9))),
        sync_monotonic_ns=13_000_000_000,
    )
    sender.run_once(now=datetime(2026, 4, 27, 12, 0, 30, tzinfo=timezone.utc))
    assert len(oracle.calls) == 1
    # 13_000_000_000 - 6_200_000_000 = 6_800_000_000 ns = 6.8s before 17:23:51 -> 17:23:44.2 -> '20260427172344'
    assert oracle.calls[0]["mk_date"] == "20260427172344"
    assert mqtt.acks[0]["mk_date_committed"] == "20260427172344"


def test_e2e_circuit_breaker_opens_on_permanent_error(tmp_path: Path):
    oracle = FakeOracle(canned=[
        MergeResult(rows_affected=0, ora_code=942, error_message="ORA-00942: table or view does not exist"),
    ])
    mqtt = FakeMqtt()
    sender, inbox = _make_sender(tmp_path, oracle=oracle, mqtt=mqtt,
                                  network=FakeNetwork(), time_watcher=FakeTimeWatcher())
    _ingest(inbox, {
        "event_id": "e1", "event": "ENTER", "event_time": "20260427120000",
        "event_time_iso": "2026-04-27T12:00:00+09:00",
        "monotonic_ns": 1, "wall_clock_synced": True,
        "device_id": "rpi", "score": 0.9, "schema_version": 1,
    })
    sender.run_once(now=datetime(2026, 4, 27, 12, 0, 1, tzinfo=timezone.utc))
    # Subsequent run within 15 minutes is blocked by the breaker.
    _ingest(inbox, {
        "event_id": "e2", "event": "EXIT", "event_time": "20260427120010",
        "event_time_iso": "2026-04-27T12:00:10+09:00",
        "monotonic_ns": 2, "wall_clock_synced": True,
        "device_id": "rpi", "score": 0.0, "schema_version": 1,
    })
    sender.run_once(now=datetime(2026, 4, 27, 12, 5, 0, tzinfo=timezone.utc))
    assert len(oracle.calls) == 1  # second event was not even attempted


def test_e2e_idempotent_replay_does_not_duplicate(tmp_path: Path):
    oracle = FakeOracle()
    mqtt = FakeMqtt()
    sender, inbox = _make_sender(tmp_path, oracle=oracle, mqtt=mqtt,
                                  network=FakeNetwork(), time_watcher=FakeTimeWatcher())
    payload = {
        "event_id": "e1", "event": "ENTER", "event_time": "20260427120000",
        "event_time_iso": "2026-04-27T12:00:00+09:00",
        "monotonic_ns": 1, "wall_clock_synced": True,
        "device_id": "rpi", "score": 0.9, "schema_version": 1,
    }
    _ingest(inbox, payload)
    _ingest(inbox, payload)   # detector replay
    sender.run_once(now=datetime(2026, 4, 27, 12, 0, 1, tzinfo=timezone.utc))
    assert len(oracle.calls) == 1   # only one MERGE despite duplicate insert attempts
