from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

from services.bridge.src.circuit_breaker import CircuitBreaker
from services.bridge.src.inbox import InboxEvent, InboxRepository
from services.bridge.src.oracle_client import MergeResult
from services.bridge.src.profile_resolver import ProfileResolver
from services.bridge.src.sender import Sender, SenderDeps
from services.bridge.src.time_watcher import SyncBaseline


def _make_event(event_id="e1", *, wall_synced=True, mk_date="20260427120000"):
    return InboxEvent(
        event_id=event_id,
        event_type="ENTER",
        mk_date=mk_date,
        monotonic_ns=1_000_000_000,
        wall_synced=wall_synced,
        device_id="rpi-test",
        score=0.9,
        raw_payload="{}",
        status="received",
        ssid_at_receive="factory_a_wifi",
        profile_at_send=None,
        mk_date_committed=None,
        received_at_iso="2026-04-27T12:00:00+00:00",
        sent_at_iso=None,
        retry_count=0,
        next_retry_at_iso=None,
        last_error=None,
    )


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


def _build_sender(
    tmp_path: Path,
    *,
    network_ssid="factory_a_wifi",
    synced=True,
    merge_result=None,
    baseline=None,
):
    inbox = InboxRepository(tmp_path / "i.db")
    inbox.init()
    resolver = ProfileResolver(profiles=_profiles(), unknown_policy="hold")
    breaker = CircuitBreaker(half_open_after_seconds=900, permanent_codes={942})
    network = MagicMock()
    network.get_current_ssid.return_value = network_ssid
    time_watcher = MagicMock()
    time_watcher.is_synced = synced
    if baseline is not None:
        time_watcher.baseline = baseline
    elif synced:
        time_watcher.baseline = SyncBaseline(
            sync_wall=datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone(timedelta(hours=9))),
            sync_monotonic_ns=2_000_000_000,
        )
    else:
        time_watcher.baseline = None
    oracle = MagicMock()
    oracle.execute_merge_for_profile.return_value = merge_result or MergeResult(
        rows_affected=1, ora_code=None, error_message="",
    )
    mqtt = MagicMock()
    deps = SenderDeps(
        inbox=inbox,
        resolver=resolver,
        breaker=breaker,
        network=network,
        time_watcher=time_watcher,
        oracle=oracle,
        mqtt=mqtt,
        device_cfg={
            "device_id": "rpi-test",
            "station": {"sta_no1": "001", "sta_no2": "A", "sta_no3": "01"},
        },
        topic_ack="presence/event/ack",
    )
    return Sender(deps=deps), deps


def test_sender_processes_event_and_publishes_ack(tmp_path: Path):
    sender, deps = _build_sender(tmp_path)
    deps.inbox.insert_received(_make_event("e1"))
    sender.run_once(now=datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC))
    deps.oracle.execute_merge_for_profile.assert_called_once()
    deps.mqtt.publish_ack.assert_called_once()
    assert deps.inbox.get("e1").status == "sent"


def test_sender_skips_when_no_ssid(tmp_path: Path):
    sender, deps = _build_sender(tmp_path, network_ssid=None)
    deps.inbox.insert_received(_make_event("e1"))
    sender.run_once(now=datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC))
    deps.oracle.execute_merge_for_profile.assert_not_called()
    deps.mqtt.publish_ack.assert_not_called()
    assert deps.inbox.get("e1").status == "received"


def test_sender_skips_when_sntp_not_synced(tmp_path: Path):
    sender, deps = _build_sender(tmp_path, synced=False, baseline=None)
    deps.inbox.insert_received(_make_event("e1", wall_synced=False, mk_date=None))
    sender.run_once(now=datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC))
    deps.oracle.execute_merge_for_profile.assert_not_called()
    assert deps.inbox.get("e1").status == "received"


def test_sender_corrects_mk_date_for_unsynced_event(tmp_path: Path):
    baseline = SyncBaseline(
        sync_wall=datetime(2026, 4, 27, 17, 23, 51, tzinfo=timezone(timedelta(hours=9))),
        sync_monotonic_ns=13_000_000_000,
    )
    sender, deps = _build_sender(tmp_path, baseline=baseline)
    deps.inbox.insert_received(_make_event("e1", wall_synced=False, mk_date=None))
    deps.inbox.get("e1")  # still received
    # Override the event's monotonic to 6_200_000_000 -> wall = 17:23:44.2 -> '20260427172344'
    e = deps.inbox.get("e1")
    e.monotonic_ns = 6_200_000_000
    deps.inbox.insert_received(e)  # re-insert is no-op (idempotent), so update directly
    import sqlite3
    with sqlite3.connect(deps.inbox.path) as c:
        c.execute(
            "UPDATE inbox SET monotonic_ns=? WHERE event_id=?",
            (6_200_000_000, "e1"),
        )
    sender.run_once(now=datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC))
    args, kwargs = deps.oracle.execute_merge_for_profile.call_args
    assert kwargs["mk_date"] == "20260427172344"


def test_sender_records_failure_and_schedules_retry(tmp_path: Path):
    sender, deps = _build_sender(
        tmp_path,
        merge_result=MergeResult(rows_affected=0, ora_code=12541, error_message="ORA-12541"),
    )
    deps.inbox.insert_received(_make_event("e1"))
    sender.run_once(now=datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC))
    row = deps.inbox.get("e1")
    assert row.status == "received"
    assert row.retry_count == 1
    assert row.last_error is not None
    assert "12541" in row.last_error


def test_sender_opens_circuit_on_permanent_error(tmp_path: Path):
    sender, deps = _build_sender(
        tmp_path,
        merge_result=MergeResult(rows_affected=0, ora_code=942, error_message="ORA-00942"),
    )
    deps.inbox.insert_received(_make_event("e1"))
    sender.run_once(now=datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC))
    assert (
        deps.breaker.state_for(
            "factory_a_wifi", now=datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)
        )
        == "open"
    )


def test_sender_skips_when_circuit_open(tmp_path: Path):
    sender, deps = _build_sender(tmp_path)
    deps.breaker.record_failure(
        "factory_a_wifi",
        ora_code=942,
        now=datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC),
    )
    deps.inbox.insert_received(_make_event("e1"))
    sender.run_once(now=datetime(2026, 4, 27, 12, 0, 1, tzinfo=UTC))
    deps.oracle.execute_merge_for_profile.assert_not_called()
