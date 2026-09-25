import importlib.util
import json
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

CHILD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CHILD))

from ack_delivery import (  # noqa: E402
    AckSession,
    DeliveryResult,
    DeliveryStore,
    SendOutcome,
    deliver_file,
)

REC = {"event_id": "one", "mk_date": "20260923120000", "device_id": "child"}

OLD_SCHEMA = """
CREATE TABLE IF NOT EXISTS delivery (
  destination      TEXT NOT NULL,
  event_id         TEXT NOT NULL,
  mk_date          TEXT NOT NULL,
  payload          TEXT NOT NULL,
  status           TEXT NOT NULL CHECK(status IN ('pending','acked','legacy_unverified')),
  last_attempt_at  REAL,
  acked_at         TEXT,
  PRIMARY KEY (destination, event_id)
);
"""


class Client:
    def __init__(self, ack=True, nack=False):
        self.ack = ack
        self.nack = nack
        self.published = []
        self.subscribe_calls = []

    def subscribe(self, topics, qos=0):
        self.subscription = topics
        self.subscribe_calls.append(topics)
        return 0, 7

    def publish(self, topic, payload, qos):
        self.published.append(json.loads(payload))
        data = self.published[-1]
        if self.nack:
            self.on_message(self, None, SimpleNamespace(
                topic=topic + "/nack", retain=False,
                payload=json.dumps({
                    "event_id": data["event_id"],
                    "reason": "ORA-00001: unique constraint violated",
                    "failed_at_iso": "2026-09-25T00:00:00Z",
                    "schema_version": 1,
                }).encode()))
        elif self.ack:
            self.on_message(self, None, SimpleNamespace(
                topic=topic + "/ack", retain=False,
                payload=json.dumps({"event_id": data["event_id"],
                                    "mk_date_committed": data["mk_date"],
                                    "committed_at": "2026-09-23T12:00:00Z"}).encode()))
        return SimpleNamespace(rc=0)


def session(tmp_path, ack=True, nack=False, nack_enabled=True, db_name="ack.db"):
    store = DeliveryStore(tmp_path / db_name, "host:1883/presence/record")
    client = Client(ack, nack)
    transport = AckSession(client, "presence/record", store, timeout=0.001)
    client.on_connect(client, None, {}, 0)
    client.on_subscribe(client, None, 7, [2, 2 if nack_enabled else 0x80])
    return store, client, transport


def nack_message(topic, event_id, *, reason="x", failed_at="t", retain=False):
    return SimpleNamespace(topic=topic, retain=retain, payload=json.dumps({
        "event_id": event_id, "reason": reason, "failed_at_iso": failed_at,
    }).encode())


def load_csv_module():
    spec = importlib.util.spec_from_file_location("csv_sender", CHILD / "child-csv-to-mqtt.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_old_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    conn.execute(
        "INSERT INTO delivery VALUES (?,?,?,?,?,?,?)",
        ("host:1883/presence/record", "one", "20260923120000", json.dumps(REC),
         "pending", 123.0, None),
    )
    conn.execute(
        "INSERT INTO delivery VALUES (?,?,?,?,?,?,?)",
        ("host:1883/presence/record", "two", "20260923120000",
         json.dumps(dict(REC, event_id="two")), "acked", None, "2026-09-23T12:00:00Z"),
    )
    conn.execute(
        "INSERT INTO delivery VALUES (?,?,?,?,?,?,?)",
        ("host:1883/presence/record", "three", "20260923120000",
         json.dumps(dict(REC, event_id="three")), "legacy_unverified", None, None),
    )
    conn.commit()
    conn.close()


# --- 購読 ---


def test_connect_subscribes_ack_and_nack_in_one_call(tmp_path):
    store = DeliveryStore(tmp_path / "ack.db", "host:1883/presence/record")
    client = Client()
    AckSession(client, "presence/record", store, timeout=0.001)
    client.on_connect(client, None, {}, 0)
    assert client.subscribe_calls == [
        [("presence/record/ack", 2), ("presence/record/nack", 2)]
    ]


def test_suback_with_both_granted_enables_send_and_nack(tmp_path):
    store, client, transport = session(tmp_path, nack_enabled=True)
    assert transport.send(REC) == SendOutcome.ACKED
    other = dict(REC, event_id="two")
    store.save_pending(other, 0.0)
    client.on_message(client, None, nack_message("presence/record/nack", "two"))
    assert store.status(other) == "nacked"


def test_nack_subscription_refused_falls_back_to_ack_only(tmp_path):
    store, client, transport = session(tmp_path, nack_enabled=False)
    assert transport.send(REC) == SendOutcome.ACKED
    other = dict(REC, event_id="two")
    store.save_pending(other, 0.0)
    client.on_message(client, None, nack_message("presence/record/nack", "two"))
    assert store.status(other) == "pending"


def test_ack_subscription_refused_blocks_send(tmp_path):
    store = DeliveryStore(tmp_path / "ack.db", "host:1883/presence/record")
    client = Client()
    transport = AckSession(client, "presence/record", store, timeout=0.001)
    client.on_connect(client, None, {}, 0)
    client.on_subscribe(client, None, 7, [0x80, 2])
    assert transport.send(REC) == SendOutcome.PENDING
    assert client.published == []


def test_resubscribe_on_reconnect_covers_nack(tmp_path):
    store, client, transport = session(tmp_path)
    client.on_disconnect(client, None, 1)
    assert transport.send(REC) == SendOutcome.PENDING
    assert client.published == []
    client.on_connect(client, None, {}, 0)
    assert transport.send(REC) == SendOutcome.PENDING
    client.on_subscribe(client, None, 7, [2, 2])
    assert transport.send(REC) == SendOutcome.ACKED


# --- nack 受信 ---


def test_nack_marks_pending_row_nacked_with_reason(tmp_path):
    store, client, transport = session(tmp_path, ack=False)
    transport.send(REC)
    client.on_message(client, None, nack_message(
        "presence/record/nack", REC["event_id"],
        reason="ORA-00001", failed_at="2026-09-25T00:00:00Z"))
    row = store.get(REC["event_id"])
    assert row.status == "nacked"
    assert row.nack_reason == "ORA-00001"
    assert row.nacked_at == "2026-09-25T00:00:00Z"


def test_send_returns_nacked_when_nack_arrives_during_wait(tmp_path):
    store = DeliveryStore(tmp_path / "ack.db", "host:1883/presence/record")
    client = Client(nack=True)
    transport = AckSession(client, "presence/record", store, timeout=5.0)
    client.on_connect(client, None, {}, 0)
    client.on_subscribe(client, None, 7, [2, 2])
    started = time.monotonic()
    assert transport.send(REC) == SendOutcome.NACKED
    assert time.monotonic() - started < 1.0


def test_nacked_row_is_never_republished(tmp_path):
    store, client, transport = session(tmp_path, nack=True)
    assert transport.send(REC) == SendOutcome.NACKED
    assert len(client.published) == 1
    assert transport.send(REC, now=10_000.0) == SendOutcome.NACKED
    assert len(client.published) == 1


def test_nack_for_unknown_event_id_is_ignored(tmp_path):
    store, client, transport = session(tmp_path)
    client.on_message(client, None, nack_message("presence/record/nack", "someone-elses-id"))
    assert store.get("someone-elses-id") is None


def test_nack_does_not_override_acked(tmp_path):
    store, client, transport = session(tmp_path)
    assert transport.send(REC) == SendOutcome.ACKED
    client.on_message(client, None, nack_message("presence/record/nack", REC["event_id"]))
    assert store.status(REC) == "acked"


def test_nack_does_not_override_legacy(tmp_path):
    store, client, transport = session(tmp_path)
    store.mark_legacy(REC)
    client.on_message(client, None, nack_message("presence/record/nack", REC["event_id"]))
    assert store.status(REC) == "legacy_unverified"


def test_retained_nack_is_ignored(tmp_path):
    store, client, transport = session(tmp_path, ack=False)
    transport.send(REC)
    client.on_message(client, None, nack_message(
        "presence/record/nack", REC["event_id"], retain=True))
    assert store.status(REC) == "pending"


def test_malformed_nack_payload_is_ignored(tmp_path):
    store, client, transport = session(tmp_path, ack=False)
    transport.send(REC)
    client.on_message(client, None, SimpleNamespace(
        topic="presence/record/nack", retain=False, payload=b"not-json"))
    assert store.status(REC) == "pending"
    client.on_message(client, None, SimpleNamespace(
        topic="presence/record/nack", retain=False,
        payload=json.dumps({"reason": "x"}).encode()))
    assert store.status(REC) == "pending"


def test_nack_on_ack_topic_name_mismatch_ignored(tmp_path):
    store, client, transport = session(tmp_path, ack=False)
    transport.send(REC)
    client.on_message(client, None, nack_message("some/other/topic", REC["event_id"]))
    assert store.status(REC) == "pending"


def test_nacked_row_content_mismatch_still_raises(tmp_path):
    store, client, transport = session(tmp_path, nack=True)
    assert transport.send(REC) == SendOutcome.NACKED
    with pytest.raises(ValueError, match="content"):
        transport.send(dict(REC, device_id="other"))


def test_nacked_status_survives_reopen(tmp_path):
    store, client, transport = session(tmp_path, nack=True)
    assert transport.send(REC) == SendOutcome.NACKED
    reopened = DeliveryStore(tmp_path / "ack.db", store.destination)
    assert reopened.status(REC) == "nacked"


def test_ack_after_nack_is_ignored(tmp_path):
    store, client, transport = session(tmp_path, nack=True)
    assert transport.send(REC) == SendOutcome.NACKED
    client.on_message(client, None, SimpleNamespace(
        topic="presence/record/ack", retain=False,
        payload=json.dumps({"event_id": REC["event_id"], "mk_date_committed": REC["mk_date"],
                             "committed_at": "2026-09-23T12:00:00Z"}).encode()))
    assert store.status(REC) == "nacked"


# --- 移行 ---


def test_old_schema_db_is_migrated_preserving_rows(tmp_path):
    db_path = tmp_path / "ack.db"
    make_old_db(db_path)
    store = DeliveryStore(db_path, "host:1883/presence/record")
    assert store.get("one").status == "pending"
    assert store.get("one").last_attempt_at == 123.0
    assert store.get("two").status == "acked"
    assert store.get("two").acked_at == "2026-09-23T12:00:00Z"
    assert store.get("three").status == "legacy_unverified"
    assert store.mark_nacked("one", reason="x", failed_at="t") is True
    assert store.get("one").status == "nacked"


def test_migration_is_idempotent(tmp_path):
    db_path = tmp_path / "ack.db"
    make_old_db(db_path)
    DeliveryStore(db_path, "host:1883/presence/record")
    store2 = DeliveryStore(db_path, "host:1883/presence/record")
    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM delivery").fetchone()[0]
    conn.close()
    assert count == 3
    assert store2.get("one").status == "pending"


# --- ファイル単位 ---


def test_file_with_only_acked_rows_goes_to_sent(tmp_path):
    store, client, transport = session(tmp_path)
    path = tmp_path / "rows.csv"
    path.write_text("valid\n")
    result = deliver_file(path, transport, lambda line: REC)
    assert result.nacked == 0
    assert result.complete is True
    module = load_csv_module()
    sent = tmp_path / "sent"
    nacked_dir = tmp_path / "nacked"
    target = module.archive_to_destination(path, result, store, sent, nacked_dir)
    assert target.parent == sent
    assert not nacked_dir.exists()


def test_file_with_nacked_row_goes_to_nacked_dir_not_sent(tmp_path):
    store, client, transport = session(tmp_path, nack=True)
    path = tmp_path / "rows.csv"
    path.write_text("valid\n")
    result = deliver_file(path, transport, lambda line: REC)
    assert result.complete is True
    assert result.nacked == 1
    module = load_csv_module()
    sent = tmp_path / "sent"
    nacked_dir = tmp_path / "nacked"
    target = module.archive_to_destination(path, result, store, sent, nacked_dir)
    assert target.parent == nacked_dir
    assert not sent.exists()


def test_nacked_file_writes_sidecar_listing_failed_rows(tmp_path):
    store, client, transport = session(tmp_path, nack=True)
    path = tmp_path / "rows.csv"
    path.write_text("valid\n")
    result = deliver_file(path, transport, lambda line: REC)
    module = load_csv_module()
    nacked_dir = tmp_path / "nacked"
    target = module.archive_to_destination(path, result, store, tmp_path / "sent", nacked_dir)
    sidecar = nacked_dir / f"{target.name}.nack.jsonl"
    entries = [json.loads(line) for line in sidecar.read_text().splitlines()]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["event_id"] == REC["event_id"]
    assert entry["mk_date"] == REC["mk_date"]
    assert entry["line_no"] == 1
    assert entry["reason"]


def test_file_with_pending_and_nacked_stays_in_outbox(tmp_path):
    store, client, transport = session(tmp_path, ack=False)
    path = tmp_path / "rows.csv"
    path.write_text("valid\n")
    result = deliver_file(path, transport, lambda line: REC)
    assert result.complete is False
    assert result.pending == 1
    module = load_csv_module()
    target = module.archive_to_destination(
        path, result, store, tmp_path / "sent", tmp_path / "nacked")
    assert target is None
    assert path.exists()


def test_nacked_archive_never_overwrites_and_sidecar_follows_name(tmp_path):
    store, client, transport = session(tmp_path, nack=True)
    path = tmp_path / "rows.csv"
    path.write_text("valid\n")
    nacked_dir = tmp_path / "nacked"
    nacked_dir.mkdir()
    (nacked_dir / "rows.csv").write_text("old")
    result = deliver_file(path, transport, lambda line: REC)
    module = load_csv_module()
    target = module.archive_to_destination(path, result, store, tmp_path / "sent", nacked_dir)
    assert target.name != "rows.csv"
    sidecar = nacked_dir / f"{target.name}.nack.jsonl"
    assert sidecar.exists()
    assert not (nacked_dir / "rows.csv.nack.jsonl").exists()


def test_summary_line_reports_nacked_count(tmp_path):
    module = load_csv_module()
    result = DeliveryResult(acked=2, pending=0, invalid=0, nacked=1, complete=True)
    line = module.format_summary("rows.csv", result)
    assert "nacked=1" in line


# --- 後方互換（既存テスト群そのもの。戻り値の書き換えのみで通ること） ---


def test_publish_completion_is_not_oracle_ack_and_retry_is_durable(tmp_path):
    store, client, transport = session(tmp_path, ack=False)
    assert transport.send(REC) == SendOutcome.PENDING
    assert store.status(REC) == "pending"
    reopened = DeliveryStore(tmp_path / "ack.db", store.destination)
    assert reopened.status(REC) == "pending"
    assert transport.send(REC) == SendOutcome.PENDING
    assert len(client.published) == 1


def test_ack_is_persisted_and_duplicate_not_published(tmp_path):
    store, client, transport = session(tmp_path)
    assert transport.send(REC) == SendOutcome.ACKED
    assert store.status(REC) == "acked"
    assert transport.send(REC) == SendOutcome.ACKED
    assert len(client.published) == 1
    other = DeliveryStore(tmp_path / "ack.db", "different-host")
    assert other.status(REC) is None


@pytest.mark.parametrize("change", [
    {"event_id": "other"}, {"mk_date_committed": "wrong"},
])
def test_wrong_ack_never_completes(tmp_path, change):
    store, client, transport = session(tmp_path, ack=False)
    def publish(topic, payload, qos):
        ack = {"event_id": REC["event_id"], "mk_date_committed": REC["mk_date"]}
        ack.update(change)
        client.on_message(client, None, SimpleNamespace(
            topic=topic + "/ack", retain=False, payload=json.dumps(ack).encode()))
        return SimpleNamespace(rc=0)
    client.publish = publish
    assert transport.send(REC) == SendOutcome.PENDING
    assert store.status(REC) == "pending"


def test_multiline_invalid_and_partial_file_stays_pending(tmp_path):
    _, _, transport = session(tmp_path)
    path = tmp_path / "rows.csv"
    path.write_text("valid\ninvalid\npartial")
    result = deliver_file(path, transport, lambda line: REC if line == "valid" else None)
    assert result.acked == 1
    assert result.complete is False
    assert result.invalid == 1


def test_updated_file_cannot_be_completed(tmp_path):
    _, client, transport = session(tmp_path)
    path = tmp_path / "rows.csv"
    path.write_text("valid\n")
    original = client.publish
    def publish(*args, **kwargs):
        with path.open("a") as output:
            output.write("partial")
        return original(*args, **kwargs)
    client.publish = publish
    assert deliver_file(path, transport, lambda line: REC).complete is False


def test_legacy_is_unverified_not_acked_or_replayed(tmp_path):
    store, client, transport = session(tmp_path)
    store.mark_legacy(REC)
    assert transport.send(REC) == SendOutcome.PENDING
    assert store.status(REC) == "legacy_unverified"
    assert client.published == []


def test_same_id_changed_content_cannot_reuse_receipt(tmp_path):
    _, _, transport = session(tmp_path)
    assert transport.send(REC) == SendOutcome.ACKED
    with pytest.raises(ValueError, match="content"):
        transport.send(dict(REC, device_id="other"))


def test_cli_archive_never_overwrites(tmp_path):
    module = load_csv_module()
    source = tmp_path / "rows.csv"
    source.write_text("new")
    archive = tmp_path / "sent"
    archive.mkdir()
    (archive / source.name).write_text("old")
    module.archive_file(source, archive)
    assert (archive / "rows.csv").read_text() == "old"
    assert sorted(p.read_text() for p in archive.iterdir()) == ["new", "old"]
