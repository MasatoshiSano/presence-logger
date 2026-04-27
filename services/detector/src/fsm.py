from dataclasses import dataclass
from typing import Literal

State = Literal["ABSENT", "PRESENT"]
EventType = Literal["ENTER", "EXIT"]


@dataclass(frozen=True)
class FSMConfig:
    enter_seconds: float
    exit_seconds: float


@dataclass(frozen=True)
class Observation:
    present: bool
    score: float
    monotonic_ns: int


@dataclass(frozen=True)
class Transition:
    from_state: State
    to_state: State
    event_type: EventType
    confirmed_at_monotonic_ns: int
    candidate_duration_ms: int
    latest_score: float
    reason: str | None = None


class PresenceFSM:
    def __init__(self, *, config: FSMConfig):
        self._config = config
        self._state: State = "ABSENT"
        self._candidate_state: State | None = None
        self._candidate_started_mono_ns: int | None = None
        self._latest_score: float = 0.0

    @property
    def state(self) -> State:
        return self._state

    def observe(self, obs: Observation) -> Transition | None:
        observed: State = "PRESENT" if obs.present else "ABSENT"
        self._latest_score = obs.score

        if observed == self._state:
            self._candidate_state = None
            self._candidate_started_mono_ns = None
            return None

        # observed != current state
        if self._candidate_state != observed:
            self._candidate_state = observed
            self._candidate_started_mono_ns = obs.monotonic_ns
            return None

        threshold_seconds = (
            self._config.enter_seconds if observed == "PRESENT" else self._config.exit_seconds
        )
        started_ns = (
            self._candidate_started_mono_ns
            if self._candidate_started_mono_ns is not None
            else obs.monotonic_ns
        )
        elapsed_ns = obs.monotonic_ns - started_ns
        if elapsed_ns >= int(threshold_seconds * 1_000_000_000):
            transition = Transition(
                from_state=self._state,
                to_state=observed,
                event_type="ENTER" if observed == "PRESENT" else "EXIT",
                confirmed_at_monotonic_ns=obs.monotonic_ns,
                candidate_duration_ms=int(elapsed_ns // 1_000_000),
                latest_score=obs.score,
            )
            self._state = observed
            self._candidate_state = None
            self._candidate_started_mono_ns = None
            return transition

        return None

    def force_exit(self, *, monotonic_ns: int, reason: str) -> Transition | None:
        if self._state != "PRESENT":
            return None
        transition = Transition(
            from_state="PRESENT",
            to_state="ABSENT",
            event_type="EXIT",
            confirmed_at_monotonic_ns=monotonic_ns,
            candidate_duration_ms=0,
            latest_score=self._latest_score,
            reason=reason,
        )
        self._state = "ABSENT"
        self._candidate_state = None
        self._candidate_started_mono_ns = None
        return transition
