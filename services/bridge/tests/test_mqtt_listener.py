import json
from unittest.mock import MagicMock, patch

import pytest

from services.bridge.src.mqtt_listener import (
    BridgeMqttClient,
    EventPayload,
    parse_event_payload,
)


def _msg(topic: str, payload: dict) -> MagicMock:
    m = MagicMock()
    m.topic = topic
    m.payload = json.dumps(payload).encode("utf-8")
    return m


def test_parse_event_payload_extracts_fields():
    p = {
        "event_id": "abc", "event": "ENTER", "event_time": "20260427120000",
        "event_time_iso": "2026-04-27T12:00:00+09:00",
        "monotonic_ns": 1, "wall_clock_synced": True,
        "device_id": "rpi-1", "score": 0.9, "schema_version": 1,
    }
    out = parse_event_payload(json.dumps(p).encode("utf-8"))
    assert isinstance(out, EventPayload)
    assert out.event_id == "abc"
    assert out.event_type == "ENTER"
    assert out.wall_clock_synced is True


def test_parse_event_payload_rejects_missing_required():
    p = {"event_id": "abc"}
    with pytest.raises(ValueError, match="event"):
        parse_event_payload(json.dumps(p).encode("utf-8"))


def test_parse_event_payload_rejects_invalid_json():
    with pytest.raises(ValueError, match="JSON"):
        parse_event_payload(b"not json")


def test_subscribe_event_invokes_callback_with_parsed_payload():
    received = []
    with patch("services.bridge.src.mqtt_listener.paho.Client") as paho_cls:
        client = paho_cls.return_value
        c = BridgeMqttClient(client_id="bridge-test")
        c.connect_and_loop(host="m", port=1883)
        c.subscribe_event("presence/event", lambda payload, raw: received.append((payload, raw)))
        on_message = client.message_callback_add.call_args.args[1]
        good = {
            "event_id": "abc", "event": "ENTER", "event_time": "20260427120000",
            "event_time_iso": "2026-04-27T12:00:00+09:00",
            "monotonic_ns": 1, "wall_clock_synced": True, "device_id": "x",
            "score": 0.9, "schema_version": 1,
        }
        on_message(client, None, _msg("presence/event", good))
    assert len(received) == 1
    assert received[0][0].event_id == "abc"


def test_subscribe_event_logs_and_drops_malformed_messages():
    with patch("services.bridge.src.mqtt_listener.paho.Client") as paho_cls:
        client = paho_cls.return_value
        called = []
        c = BridgeMqttClient(client_id="bridge-test")
        c.connect_and_loop(host="m", port=1883)
        c.subscribe_event("presence/event", lambda *a: called.append(a))
        on_message = client.message_callback_add.call_args.args[1]
        bad_msg = MagicMock()
        bad_msg.topic = "presence/event"
        bad_msg.payload = b"not json"
        on_message(client, None, bad_msg)
    assert called == []  # malformed messages are dropped, not delivered to handler


def test_publish_ack_serializes_payload_with_qos2():
    with patch("services.bridge.src.mqtt_listener.paho.Client") as paho_cls:
        client = paho_cls.return_value
        c = BridgeMqttClient(client_id="bridge-test")
        c.connect_and_loop(host="m", port=1883)
        c.publish_ack(
            "presence/event/ack",
            event_id="abc",
            mk_date_committed="20260427120000",
            committed_at_iso="2026-04-27T12:00:00.123+09:00",
        )
        client.publish.assert_called_once()
        args, kwargs = client.publish.call_args
        body = json.loads(args[1])
        assert body == {
            "event_id": "abc", "mk_date_committed": "20260427120000",
            "committed_at_iso": "2026-04-27T12:00:00.123+09:00",
            "schema_version": 1,
        }
        assert kwargs.get("qos") == 2
