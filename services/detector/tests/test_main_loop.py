from unittest.mock import MagicMock

import services.detector.src.main as main_mod
from services.detector.src.buffer import BufferRepository
from services.detector.src.fsm import FSMConfig, Observation, PresenceFSM
from services.detector.src.main import RuntimeContext, process_observation


def _ctx(*, fsm, buffer, mqtt, time_source, hostname="rpi-test", device_cfg=None):
    if device_cfg is None:
        device_cfg = {
            "device_id": hostname,
            "station": {"sta_no1": "001", "sta_no2": "A", "sta_no3": "01"},
        }
    return RuntimeContext(
        device_cfg=device_cfg,
        fsm=fsm,
        buffer=buffer,
        mqtt=mqtt,
        time_source=time_source,
        topic_event="presence/event",
        retry_policy=main_mod.BackoffPolicy(initial=5, multiplier=3, cap=600),
    )


def test_process_observation_no_transition_does_not_publish(tmp_path):
    fsm = PresenceFSM(config=FSMConfig(enter_seconds=3.0, exit_seconds=3.0))
    buf = BufferRepository(tmp_path / "x.db")
    buf.init()
    mqtt = MagicMock()
    ts = MagicMock()
    ts.is_synced.return_value = True
    ts.now.return_value.isoformat.return_value = "2026-04-27T12:00:00+09:00"
    process_observation(
        _ctx(fsm=fsm, buffer=buf, mqtt=mqtt, time_source=ts),
        Observation(present=False, score=0.0, monotonic_ns=0),
    )
    assert mqtt.publish_event.call_count == 0
    assert buf.count() == 0


def test_process_observation_transition_persists_and_publishes(tmp_path, monkeypatch):
    fsm = PresenceFSM(config=FSMConfig(enter_seconds=3.0, exit_seconds=3.0))
    buf = BufferRepository(tmp_path / "x.db")
    buf.init()
    mqtt = MagicMock()
    ts = MagicMock()
    ts.is_synced.return_value = True
    from datetime import datetime, timedelta, timezone
    ts.now.return_value = datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone(timedelta(hours=9)))
    monkeypatch.setattr(
        "uuid.uuid4",
        lambda: type(
            "U", (), {"hex": "deadbeef" * 4, "__str__": lambda self: "0192b6d2-fixed"}
        )(),
    )

    ctx = _ctx(fsm=fsm, buffer=buf, mqtt=mqtt, time_source=ts)
    process_observation(ctx, Observation(present=True, score=0.8, monotonic_ns=0))
    process_observation(ctx, Observation(present=True, score=0.9, monotonic_ns=3_000_000_000))

    assert mqtt.publish_event.call_count == 1
    topic, payload = mqtt.publish_event.call_args.args[0], mqtt.publish_event.call_args.args[1]
    assert topic == "presence/event"
    assert payload["event"] == "ENTER"
    assert payload["event_time"] == "20260427120000"
    assert payload["wall_clock_synced"] is True
    assert payload["device_id"] == "rpi-test"
    assert payload["schema_version"] == 1
    assert buf.count() == 1
