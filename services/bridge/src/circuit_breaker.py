from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

CircuitState = Literal["closed", "open", "half_open"]


@dataclass
class _ProfileEntry:
    state: CircuitState = "closed"
    opened_at: datetime | None = None
    last_ora_code: int | None = None


def is_permanent_error(ora_code: int | None, *, permanent_codes: set[int]) -> bool:
    return ora_code is not None and ora_code in permanent_codes


class CircuitBreaker:
    def __init__(self, *, half_open_after_seconds: int, permanent_codes: set[int]):
        self._timeout = timedelta(seconds=half_open_after_seconds)
        self._permanent = permanent_codes
        self._entries: dict[str, _ProfileEntry] = {}

    def _entry(self, profile: str) -> _ProfileEntry:
        return self._entries.setdefault(profile, _ProfileEntry())

    def state_for(self, profile: str, *, now: datetime | None = None) -> CircuitState:
        e = self._entry(profile)
        if e.state == "open" and e.opened_at is not None and now is not None:
            if now - e.opened_at >= self._timeout:
                e.state = "half_open"
        return e.state

    def record_failure(self, profile: str, *, ora_code: int | None, now: datetime) -> None:
        e = self._entry(profile)
        if not is_permanent_error(ora_code, permanent_codes=self._permanent):
            return
        e.state = "open"
        e.opened_at = now
        e.last_ora_code = ora_code

    def record_success(self, profile: str, *, now: datetime) -> None:  # noqa: ARG002
        e = self._entry(profile)
        e.state = "closed"
        e.opened_at = None
        e.last_ora_code = None
