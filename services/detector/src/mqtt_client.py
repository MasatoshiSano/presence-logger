import json
import logging
import uuid
from collections.abc import Callable

import paho.mqtt.client as paho

_log = logging.getLogger("detector.mqtt")


class DetectorMqttClient:
    def __init__(self, *, client_id_prefix: str):
        self._client_id = f"{client_id_prefix}-{uuid.uuid4().hex[:8]}"
        self._client: paho.Client | None = None

    def connect_and_loop(
        self, *, host: str, port: int, keepalive: int = 60,
        will_topic: str | None = None, will_payload: str | None = None,
    ) -> None:
        client = paho.Client(client_id=self._client_id, protocol=paho.MQTTv311)
        client.reconnect_delay_set(min_delay=1, max_delay=60)
        # Last-Will: broker emits this (retained) if we drop ungracefully, so the
        # hub learns the device went offline without us being able to say so.
        if will_topic is not None:
            client.will_set(will_topic, payload=will_payload, qos=1, retain=True)
        client.connect(host, port, keepalive=keepalive)
        client.loop_start()
        self._client = client

    def publish_retained(self, topic: str, payload: str, *, qos: int = 1) -> None:
        """Publish a small retained string (used for online/offline status)."""
        if self._client is None:
            raise RuntimeError("mqtt client not connected")
        self._client.publish(topic, payload, qos=qos, retain=True)

    def publish_heartbeat(self, topic: str, payload: dict, *, qos: int = 0) -> None:
        if self._client is None:
            raise RuntimeError("mqtt client not connected")
        self._client.publish(topic, json.dumps(payload, ensure_ascii=False), qos=qos)

    def publish_event(self, topic: str, payload: dict, *, qos: int = 2) -> paho.MQTTMessageInfo:
        if self._client is None:
            raise RuntimeError("mqtt client not connected")
        body = json.dumps(payload, ensure_ascii=False)
        info = self._client.publish(topic, body, qos=qos)
        _log.info(
            "publish",
            extra={
                "event": "publish",
                "topic": topic,
                "qos": qos,
                "event_id": payload.get("event_id"),
                "payload_size_bytes": len(body),
                "mid": info.mid,
            },
        )
        return info

    def subscribe_ack(self, topic: str, callback: Callable[[str, str], None]) -> None:
        if self._client is None:
            raise RuntimeError("mqtt client not connected")

        def _on_message(_client, _userdata, msg) -> None:
            try:
                data = json.loads(msg.payload.decode("utf-8"))
            except Exception:
                _log.warning(
                    "ack_decode_failed",
                    extra={"event": "ack_decode_failed", "topic": msg.topic},
                )
                return
            event_id = data.get("event_id")
            mk = data.get("mk_date_committed")
            if event_id and mk:
                callback(event_id, mk)

        self._client.subscribe(topic, qos=2)
        self._client.message_callback_add(topic, _on_message)

    def disconnect(self) -> None:
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
