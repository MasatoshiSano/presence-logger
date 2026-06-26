from dataclasses import dataclass
from datetime import UTC, datetime

from services.bridge.src.circuit_breaker import CircuitBreaker
from services.bridge.src.profile_resolver import ProfileResolver
from services.bridge.src.record_inbox import RecordInboxEvent, RecordInboxRepository
from services.bridge.src.record_sender import RecordSender, RecordSenderDeps

NOW = datetime(2026, 6, 10, 18, 0, 0, tzinfo=UTC)
PROFILE = {"oracle": {"client_mode": "jdbc"}}


@dataclass
class _Result:
    ora_code: int | None
    rows_affected: int = 1
    error_message: str | None = None


class _FakeOracle:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def execute_merge_for_profile(self, **kw):
        self.calls.append(kw)
        return self.result


class _FakeNet:
    def __init__(self, ssid):
        self.cached_ssid = ssid


class _FakeMqtt:
    def __init__(self):
        self.acks = []

    def publish_ack(self, topic, **kw):
        self.acks.append(kw)


def _seed(tmp_path, **kw):
    r = RecordInboxRepository(tmp_path / "rec.db")
    r.init()
    r.insert_received(RecordInboxEvent(
        event_id="r1", mk_date="20260610173000", sta_no1="100", sta_no2="200",
        sta_no3="300", t1_status=3, device_id="child-01", raw_payload="{}",
        status="received", received_at_iso="2026-06-10T17:30:00+00:00",
        sent_at_iso=None, mk_date_committed=None, retry_count=0,
        next_retry_at_iso=None, last_error=None,
    ))
    return r


def _deps(tmp_path, *, ssid, oracle, mqtt):
    return RecordSenderDeps(
        record_inbox=_seed(tmp_path),
        resolver=ProfileResolver(profiles={"HIME-H-REAP": PROFILE}, unknown_policy="drop"),
        breaker=CircuitBreaker(half_open_after_seconds=60, permanent_codes=set()),
        network=_FakeNet(ssid),
        oracle=oracle,
        mqtt=mqtt,
        topic_ack="presence/record/ack",
    )


def test_success_merges_with_record_values_and_acks(tmp_path):
    oracle = _FakeOracle(_Result(ora_code=None))
    mqtt = _FakeMqtt()
    d = _deps(tmp_path, ssid="HIME-H-REAP", oracle=oracle, mqtt=mqtt)
    RecordSender(deps=d).run_once(now=NOW)
    # merged with the RECORD's own values (not profile station / ENTER-EXIT)
    assert oracle.calls[0]["sta_no1"] == "100"
    assert oracle.calls[0]["t1_status"] == 3
    assert oracle.calls[0]["mk_date"] == "20260610173000"
    # marked sent -> no longer due
    assert list(d.record_inbox.iter_received_due(now_iso=NOW.isoformat())) == []
    assert mqtt.acks[0]["event_id"] == "r1"


def test_failure_defers_and_no_ack(tmp_path):
    oracle = _FakeOracle(_Result(ora_code=12541, error_message="no listener"))
    mqtt = _FakeMqtt()
    d = _deps(tmp_path, ssid="HIME-H-REAP", oracle=oracle, mqtt=mqtt)
    RecordSender(deps=d).run_once(now=NOW)
    assert mqtt.acks == []
    # still received but deferred to a future retry time
    assert list(d.record_inbox.iter_received_due(now_iso=NOW.isoformat())) == []


def test_skips_when_not_on_known_ssid(tmp_path):
    oracle = _FakeOracle(_Result(ora_code=None))
    d = _deps(tmp_path, ssid="UFI_103134", oracle=oracle, mqtt=_FakeMqtt())
    RecordSender(deps=d).run_once(now=NOW)
    assert oracle.calls == []  # never attempted off-profile
    assert d.record_inbox.count() == 1
