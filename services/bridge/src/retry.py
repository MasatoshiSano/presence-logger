from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class BackoffPolicy:
    initial: float
    multiplier: float
    cap: float


def next_retry_at(now: datetime, *, attempt: int, policy: BackoffPolicy) -> datetime:
    if attempt < 1:
        raise ValueError(f"attempt must be >= 1, got {attempt}")
    raw = policy.initial * (policy.multiplier ** (attempt - 1))
    delay = min(raw, policy.cap)
    return now + timedelta(seconds=delay)
