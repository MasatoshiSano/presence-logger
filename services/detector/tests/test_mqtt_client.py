import json
from unittest.mock import MagicMock, patch

from services.detector.src.mqtt_client import DetectorMqttClient


def _msg(topic: str, payload: dict) -> MagicMock:
    m = MagicMock()
    m.topic = topic
    m.payload = json.dumps(payload).encode("utf-8")
    return m


def test_connect_starts_paho_loop():
    with patch("services.detector.src.mqtt_client.paho.Client") as paho_cls:
        client = paho_cls.return_value
        c = DetectorMqttClient(client_id_prefix="x")
        c.connect_and_loop(host="mosquitto", port=1883)
        client.connect.assert_called_once_with("mosquitto", 1883, keepalive=60)
        client.loop_start.assert_called_once()


def test_publish_event_serializes_payload_and_uses_qos2():
    with patch("services.detector.src.mqtt_client.paho.Client") as paho_cls:
        client = paho_cls.return_value
        client.publish.return_value = MagicMock(rc=0, mid=42)
        c = DetectorMqttClient(client_id_prefix="x")
        c.connect_and_loop(host="m", port=1883)
        info = c.publish_event("presence/event", {"event_id": "abc", "event": "ENTER"})
        client.publish.assert_called_once()
        args, kwargs = client.publish.call_args
        assert args[0] == "presence/event"
        assert json.loads(args[1]) == {"event_id": "abc", "event": "ENTER"}
        assert kwargs.get("qos") == 2
        assert info.mid == 42


def test_subscribe_ack_invokes_callback_on_message():
    received = []

    def on_ack(event_id: str, mk_date_committed: str) -> None:
        received.append((event_id, mk_date_committed))

    with patch("services.detector.src.mqtt_client.paho.Client") as paho_cls:
        client = paho_cls.return_value
        c = DetectorMqttClient(client_id_prefix="x")
        c.connect_and_loop(host="m", port=1883)
        c.subscribe_ack("presence/event/ack", on_ack)
        # Find the message handler that was registered, simulate a message:
        on_message = client.message_callback_add.call_args.args[1]
        on_message(client, None, _msg("presence/event/ack", {
            "event_id": "abc", "mk_date_committed": "20260427120000"
        }))
        assert received == [("abc", "20260427120000")]
