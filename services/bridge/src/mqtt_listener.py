import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

import paho.mqtt.client as paho

_log = logging.getLogger("bridge.mqtt")

REQUIRED_PAYLOAD_KEYS = (
    "event_id", "event", "monotonic_ns", "wall_clock_synced",
    "device_id", "schema_version",
)


@dataclass(frozen=True)
class EventPayload:
    event_id: str
    event_type: str
    mk_date: str | None
    event_time_iso: str | None
    monotonic_ns: int
    wall_clock_synced: bool
    device_id: str
    score: float | None
    schema_version: int


def parse_event_payload(raw: bytes) -> EventPayload:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"invalid JSON payload: {e}") from e
    missing = [k for k in REQUIRED_PAYLOAD_KEYS if k not in data]
    if missing:
        raise ValueError(f"payload missing required keys: {missing}")
    return EventPayload(
        event_id=data["event_id"],
        event_type=data["event"],
        mk_date=data.get("event_time"),
        event_time_iso=data.get("event_time_iso"),
        monotonic_ns=int(data["monotonic_ns"]),
        wall_clock_synced=bool(data["wall_clock_synced"]),
        device_id=data["device_id"],
        score=data.get("score"),
        schema_version=int(data["schema_version"]),
    )


class BridgeMqttClient:
    def __init__(self, *, client_id: str):
        self._client_id = client_id
        self._client: paho.Client | None = None

    def connect_and_loop(self, *, host: str, port: int, keepalive: int = 60) -> None:
        client = paho.Client(client_id=self._client_id, protocol=paho.MQTTv5)
        client.reconnect_delay_set(min_delay=1, max_delay=60)
        client.connect(host, port, keepalive=keepalive)
        client.loop_start()
        self._client = client

    def subscribe_event(
        self, topic: str, handler: Callable[[EventPayload, bytes], None]
    ) -> None:
        if self._client is None:
            raise RuntimeError("mqtt client not connected")

        def _on_message(_client, _userdata, msg) -> None:
            try:
                payload = parse_event_payload(msg.payload)
            except ValueError as e:
                _log.warning(
                    "event_parse_failed",
                    extra={
                        "event": "event_parse_failed",
                        "error": {"type": type(e).__name__, "message": str(e)},
                    },
                )
                return
            handler(payload, msg.payload)

        self._client.subscribe(topic, qos=2)
        self._client.message_callback_add(topic, _on_message)

    def publish_ack(self, topic: str, *, event_id: str, mk_date_committed: str,
                    committed_at_iso: str) -> None:
        if self._client is None:
            raise RuntimeError("mqtt client not connected")
        body = json.dumps({
            "event_id": event_id,
            "mk_date_committed": mk_date_committed,
            "committed_at_iso": committed_at_iso,
            "schema_version": 1,
        })
        self._client.publish(topic, body, qos=2)

    def disconnect(self) -> None:
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
