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
        # (topic_filter, qos) pairs replayed on every (re)connect. paho does NOT
        # resend SUBSCRIBE frames after an automatic reconnect, so without this a
        # broker restart or network blip would silently drop every subscription
        # until the process is restarted.
        self._subscriptions: list[tuple[str, int]] = []

    def connect_and_loop(self, *, host: str, port: int, keepalive: int = 60) -> None:
        client = paho.Client(client_id=self._client_id, protocol=paho.MQTTv311)
        client.reconnect_delay_set(min_delay=1, max_delay=60)
        client.on_connect = self._on_connect
        client.connect(host, port, keepalive=keepalive)
        client.loop_start()
        self._client = client

    def _on_connect(self, client, _userdata, _flags, rc) -> None:
        # Fired on the initial connect and on every automatic reconnect. Replaying
        # the full subscription set here (rather than only subscribing once at
        # wiring time) fixes both the reconnect-drop above and a subscribe-before-
        # CONNACK race on the first connect.
        if rc != 0:
            _log.warning(
                "mqtt_connect_failed",
                extra={"event": "mqtt_connect_failed", "rc": rc},
            )
            return
        for topic_filter, qos in self._subscriptions:
            client.subscribe(topic_filter, qos=qos)
        _log.info(
            "mqtt_connected",
            extra={"event": "mqtt_connected", "subscriptions": len(self._subscriptions)},
        )

    def _register_subscription(self, topic_filter: str, *, qos: int) -> None:
        self._subscriptions.append((topic_filter, qos))
        # Subscribe immediately in case we are already connected (a subscription
        # added after the first CONNACK would otherwise wait for the next
        # reconnect); on_connect replays the full list on every (re)connect.
        if self._client is not None:
            self._client.subscribe(topic_filter, qos=qos)

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

        # message callbacks persist on the paho client across reconnects, so they
        # are added once; only the SUBSCRIBE itself needs replaying.
        self._client.message_callback_add(topic, _on_message)
        self._register_subscription(topic, qos=2)

    def subscribe_text(
        self, topic_filter: str, handler: Callable[[str, bytes], None]
    ) -> None:
        """Subscribe to a topic filter and hand the raw (topic, payload) to handler.

        Used for liveness (status/heartbeat) where the device_id is in the topic
        and payloads are small strings/JSON. QoS 1 is enough; status is retained.
        """
        if self._client is None:
            raise RuntimeError("mqtt client not connected")

        def _on_message(_client, _userdata, msg) -> None:
            handler(msg.topic, msg.payload)

        self._client.message_callback_add(topic_filter, _on_message)
        self._register_subscription(topic_filter, qos=1)

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
