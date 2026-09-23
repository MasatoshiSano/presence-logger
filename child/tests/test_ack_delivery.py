import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

CHILD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CHILD))

from ack_delivery import AckSession, DeliveryStore, deliver_file  # noqa: E402

REC = {"event_id": "one", "mk_date": "20260923120000", "device_id": "child"}


class Client:
    def __init__(self, ack=True):
        self.ack = ack
        self.published = []

    def subscribe(self, topic, qos):
        self.subscription = (topic, qos)
        return 0, 7

    def publish(self, topic, payload, qos):
        self.published.append(json.loads(payload))
        if self.ack:
            data = self.published[-1]
            self.on_message(self, None, SimpleNamespace(
                topic=topic + "/ack", retain=False,
                payload=json.dumps({"event_id": data["event_id"],
                                    "mk_date_committed": data["mk_date"],
                                    "committed_at": "2026-09-23T12:00:00Z"}).encode()))
        return SimpleNamespace(rc=0)


def session(tmp_path, ack=True):
    store = DeliveryStore(tmp_path / "ack.db", "host:1883/presence/record")
    client = Client(ack)
    transport = AckSession(client, "presence/record", store, timeout=0.001)
    client.on_connect(client, None, {}, 0)
    client.on_subscribe(client, None, 7, [2])
    return store, client, transport


def test_publish_completion_is_not_oracle_ack_and_retry_is_durable(tmp_path):
    store, client, transport = session(tmp_path, ack=False)
    assert transport.send(REC) is False
    assert store.status(REC) == "pending"
    reopened = DeliveryStore(tmp_path / "ack.db", store.destination)
    assert reopened.status(REC) == "pending"
    assert transport.send(REC) is False
    assert len(client.published) == 1


def test_ack_is_persisted_and_duplicate_not_published(tmp_path):
    store, client, transport = session(tmp_path)
    assert transport.send(REC) is True
    assert store.status(REC) == "acked"
    assert transport.send(REC) is True
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
    assert transport.send(REC) is False
    assert store.status(REC) == "pending"


def test_wait_for_suback_and_resubscribe_on_reconnect(tmp_path):
    store, client, transport = session(tmp_path)
    client.on_disconnect(client, None, 1)
    assert transport.send(REC) is False
    assert client.published == []
    client.on_connect(client, None, {}, 0)
    assert transport.send(REC) is False
    client.on_subscribe(client, None, 7, [2])
    assert transport.send(REC) is True


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
    assert transport.send(REC) is False
    assert store.status(REC) == "legacy_unverified"
    assert client.published == []


def test_same_id_changed_content_cannot_reuse_receipt(tmp_path):
    _, _, transport = session(tmp_path)
    assert transport.send(REC)
    with pytest.raises(ValueError, match="content"):
        transport.send(dict(REC, device_id="other"))


def test_cli_archive_never_overwrites(tmp_path):
    spec = importlib.util.spec_from_file_location("csv_sender", CHILD / "child-csv-to-mqtt.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = tmp_path / "rows.csv"
    source.write_text("new")
    archive = tmp_path / "sent"
    archive.mkdir()
    (archive / source.name).write_text("old")
    module.archive_file(source, archive)
    assert (archive / "rows.csv").read_text() == "old"
    assert sorted(p.read_text() for p in archive.iterdir()) == ["new", "old"]
