from datetime import UTC, datetime, timedelta

import pytest

from services.detector.src.retry import BackoffPolicy, next_retry_at


def test_first_retry_uses_initial_delay():
    now = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)
    policy = BackoffPolicy(initial=5.0, multiplier=3.0, cap=600.0)
    assert next_retry_at(now, attempt=1, policy=policy) == now + timedelta(seconds=5)


def test_second_retry_multiplies():
    now = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)
    policy = BackoffPolicy(initial=5.0, multiplier=3.0, cap=600.0)
    assert next_retry_at(now, attempt=2, policy=policy) == now + timedelta(seconds=15)


def test_grows_5_15_45_135_405_then_caps():
    now = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)
    policy = BackoffPolicy(initial=5.0, multiplier=3.0, cap=600.0)
    expected = [5, 15, 45, 135, 405, 600, 600, 600]
    actual = [
        (next_retry_at(now, attempt=i, policy=policy) - now).total_seconds()
        for i in range(1, 9)
    ]
    assert actual == expected


def test_zero_or_negative_attempt_raises():
    now = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)
    policy = BackoffPolicy(initial=5.0, multiplier=3.0, cap=600.0)
    with pytest.raises(ValueError, match="attempt must be >= 1"):
        next_retry_at(now, attempt=0, policy=policy)
