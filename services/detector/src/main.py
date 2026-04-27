from __future__ import annotations

import logging
import os
import signal
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from services.detector.src import config as cfg_mod
from services.detector.src.buffer import BufferRepository, PendingEvent
from services.detector.src.camera import Camera
from services.detector.src.fsm import FSMConfig, Observation, PresenceFSM
from services.detector.src.inference import PersonDetector
from services.detector.src.logging_setup import setup_logging
from services.detector.src.mqtt_client import DetectorMqttClient
from services.detector.src.retry import BackoffPolicy, next_retry_at
from services.detector.src.time_source import TimeSource, format_iso_with_tz, format_mk_date

_log = logging.getLogger("detector.main")
HEALTH_FILE = "/tmp/detector.healthy"      # noqa: S108
DEFAULT_DETECTOR_YAML = "/etc/presence-logger/detector.yaml"
DEFAULT_DEVICE_YAML = "/etc/presence-logger/device.yaml"


@dataclass
class RuntimeContext:
    device_cfg: dict
    fsm: PresenceFSM
    buffer: BufferRepository
    mqtt: DetectorMqttClient
    time_source: TimeSource
    topic_event: str
    retry_policy: BackoffPolicy


def process_observation(ctx: RuntimeContext, obs: Observation) -> None:
    """Single FSM step. If a transition fires, persist + publish."""
    transition = ctx.fsm.observe(obs)
    if transition is None:
        return
    _emit_transition(ctx, transition)


def _emit_transition(ctx: RuntimeContext, transition) -> None:
    event_id = str(uuid.uuid4())
    synced = ctx.time_source.is_synced()
    now = ctx.time_source.now()
    payload = {
        "event_id": event_id,
        "event": transition.event_type,
        "event_time": format_mk_date(now) if synced else None,
        "event_time_iso": format_iso_with_tz(now) if synced else None,
        "monotonic_ns": transition.confirmed_at_monotonic_ns,
        "wall_clock_synced": synced,
        "device_id": ctx.device_cfg["device_id"],
        "score": transition.latest_score,
        "schema_version": 1,
    }
    pending = PendingEvent(
        event_id=event_id,
        event_type=transition.event_type,
        mk_date=payload["event_time"],
        monotonic_ns=transition.confirmed_at_monotonic_ns,
        wall_synced=synced,
        score=transition.latest_score,
        status="pending",
        created_at_iso=format_iso_with_tz(now),
        retry_count=0,
        next_retry_at_iso=None,
        last_publish_at_iso=None,
    )
    ctx.buffer.insert_pending(pending)
    ctx.mqtt.publish_event(ctx.topic_event, payload, qos=2)
    ctx.buffer.mark_sent(event_id)
    _log.info("transition", extra={
        "event": "transition",
        "from": transition.from_state,
        "to": transition.to_state,
        "event_type": transition.event_type,
        "event_id": event_id,
        "candidate_duration_ms": transition.candidate_duration_ms,
        "latest_score": transition.latest_score,
    })


def retry_pending(ctx: RuntimeContext) -> None:
    """Re-publish events that are pending or sent (no ACK yet) and due."""
    now = ctx.time_source.now()
    now_iso = format_iso_with_tz(now)
    for status in ("pending", "sent"):
        for row in ctx.buffer.iter_due_for_retry(now_iso=now_iso, status=status):
            payload = _build_resend_payload(ctx, row)
            ctx.mqtt.publish_event(ctx.topic_event, payload, qos=2)
            attempt = row.retry_count + 1
            ctx.buffer.update_retry_metadata(
                row.event_id,
                retry_count=attempt,
                next_retry_at_iso=format_iso_with_tz(
                    next_retry_at(now, attempt=attempt, policy=ctx.retry_policy)
                ),
            )


def _build_resend_payload(ctx: RuntimeContext, row: PendingEvent) -> dict:
    return {
        "event_id": row.event_id,
        "event": row.event_type,
        "event_time": row.mk_date,
        "event_time_iso": row.created_at_iso if row.wall_synced else None,
        "monotonic_ns": row.monotonic_ns,
        "wall_clock_synced": row.wall_synced,
        "device_id": ctx.device_cfg["device_id"],
        "score": row.score or 0.0,
        "schema_version": 1,
    }


def main() -> int:    # pragma: no cover (integration entry point)
    detector_yaml = Path(os.environ.get("DETECTOR_YAML", DEFAULT_DETECTOR_YAML))
    device_yaml = Path(os.environ.get("DEVICE_YAML", DEFAULT_DEVICE_YAML))
    detector_cfg = cfg_mod.load_detector_config(detector_yaml)
    device_cfg = cfg_mod.load_device_config(device_yaml)

    setup_logging(
        process="detector",
        device_id=device_cfg["device_id"],
        log_dir="/var/log/presence-logger",
        level=os.environ.get("LOG_LEVEL", "INFO"),
    )
    _log.info("startup", extra={"event": "startup", "config_path": str(detector_yaml)})

    camera = Camera(
        device=detector_cfg["camera"]["device"],
        width=detector_cfg["camera"]["width"],
        height=detector_cfg["camera"]["height"],
        warmup_frames=detector_cfg["camera"]["warmup_frames"],
    )
    camera.open()

    detector = PersonDetector.from_model_path(
        model_path=detector_cfg["inference"]["model_path"],
        score_threshold=detector_cfg["inference"]["score_threshold"],
        target_category=detector_cfg["inference"]["category"],
    )

    fsm = PresenceFSM(config=FSMConfig(
        enter_seconds=detector_cfg["debounce"]["enter_seconds"],
        exit_seconds=detector_cfg["debounce"]["exit_seconds"],
    ))
    buffer = BufferRepository(detector_cfg["buffer"]["path"])
    buffer.init()

    mqtt = DetectorMqttClient(client_id_prefix=detector_cfg["mqtt"]["client_id_prefix"])
    mqtt.connect_and_loop(
        host=os.environ.get("MQTT_HOST", detector_cfg["mqtt"]["host"]),
        port=detector_cfg["mqtt"]["port"],
    )

    def _on_ack(event_id: str, mk_date_committed: str) -> None:
        buffer.mark_acked(event_id)
        _log.info("ack_received", extra={
            "event": "ack_received",
            "event_id": event_id,
            "mk_date_committed": mk_date_committed,
        })

    mqtt.subscribe_ack(detector_cfg["mqtt"]["topic_ack"], _on_ack)

    time_source = TimeSource()
    ctx = RuntimeContext(
        device_cfg=device_cfg,
        fsm=fsm,
        buffer=buffer,
        mqtt=mqtt,
        time_source=time_source,
        topic_event=detector_cfg["mqtt"]["topic_event"],
        retry_policy=BackoffPolicy(
            initial=detector_cfg["retry"]["initial_delay_seconds"],
            multiplier=detector_cfg["retry"]["multiplier"],
            cap=detector_cfg["retry"]["max_delay_seconds"],
        ),
    )

    target_fps = detector_cfg["inference"]["target_fps"]
    period = 1.0 / target_fps
    last_health = 0.0
    last_retry_scan = 0.0
    last_stats = 0.0
    running = True

    def _stop(*_a):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    while running:
        loop_start = time.monotonic()

        frame = camera.read()
        if frame is None and camera.consecutive_failures >= 10:
            t = fsm.force_exit(monotonic_ns=time_source.monotonic_ns(), reason="camera_lost")
            if t is not None:
                _emit_transition(ctx, t)
            _log.error("camera_failure", extra={
                "event": "camera_failure",
                "consecutive_failures": camera.consecutive_failures,
            })

        if frame is not None:
            r = detector.detect(frame)
            obs = Observation(
                present=r.has_person,
                score=r.top_score,
                monotonic_ns=time_source.monotonic_ns(),
            )
            process_observation(ctx, obs)

        now = time.monotonic()
        if now - last_retry_scan >= 5.0:
            retry_pending(ctx)
            last_retry_scan = now
        if now - last_health >= 5.0:
            Path(HEALTH_FILE).touch()
            last_health = now
        if now - last_stats >= 60.0:
            _log.info("periodic", extra={
                "event": "periodic",
                "fps_target": target_fps,
                "buffer_pending": buffer.count(),
                "camera_consecutive_errors": camera.consecutive_failures,
            })
            last_stats = now

        elapsed = time.monotonic() - loop_start
        if elapsed < period:
            time.sleep(period - elapsed)

    camera.close()
    mqtt.disconnect()
    return 0


if __name__ == "__main__":     # pragma: no cover
    raise SystemExit(main())
