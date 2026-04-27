from datetime import UTC, datetime, timedelta

from services.bridge.src.circuit_breaker import CircuitBreaker, is_permanent_error


def test_initial_state_is_closed():
    cb = CircuitBreaker(half_open_after_seconds=900, permanent_codes={942, 1017})
    assert cb.state_for("p1") == "closed"


def test_record_failure_with_permanent_code_opens_circuit():
    cb = CircuitBreaker(half_open_after_seconds=900, permanent_codes={942})
    now = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)
    cb.record_failure("p1", ora_code=942, now=now)
    assert cb.state_for("p1", now=now) == "open"


def test_record_failure_with_transient_code_does_not_open():
    cb = CircuitBreaker(half_open_after_seconds=900, permanent_codes={942})
    now = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)
    cb.record_failure("p1", ora_code=12541, now=now)
    assert cb.state_for("p1", now=now) == "closed"


def test_open_circuit_transitions_to_half_open_after_timeout():
    cb = CircuitBreaker(half_open_after_seconds=900, permanent_codes={942})
    open_at = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)
    cb.record_failure("p1", ora_code=942, now=open_at)
    later = open_at + timedelta(seconds=901)
    assert cb.state_for("p1", now=later) == "half_open"


def test_half_open_success_closes_circuit():
    cb = CircuitBreaker(half_open_after_seconds=900, permanent_codes={942})
    open_at = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)
    cb.record_failure("p1", ora_code=942, now=open_at)
    later = open_at + timedelta(seconds=901)
    cb.record_success("p1", now=later)
    assert cb.state_for("p1", now=later) == "closed"


def test_half_open_failure_reopens_circuit():
    cb = CircuitBreaker(half_open_after_seconds=900, permanent_codes={942})
    open_at = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)
    cb.record_failure("p1", ora_code=942, now=open_at)
    later = open_at + timedelta(seconds=901)
    cb.record_failure("p1", ora_code=942, now=later)
    much_later = later + timedelta(seconds=300)
    assert cb.state_for("p1", now=much_later) == "open"


def test_is_permanent_error_helper():
    assert is_permanent_error(942, permanent_codes={942, 1017}) is True
    assert is_permanent_error(12541, permanent_codes={942, 1017}) is False
