from services.detector.src.fsm import FSMConfig, Observation, PresenceFSM, Transition

CFG = FSMConfig(enter_seconds=3.0, exit_seconds=3.0)


def test_initial_state_is_absent():
    fsm = PresenceFSM(config=CFG)
    assert fsm.state == "ABSENT"


def test_observation_below_debounce_no_transition():
    fsm = PresenceFSM(config=CFG)
    fsm.observe(Observation(present=True, score=0.9, monotonic_ns=0))
    out = fsm.observe(Observation(present=True, score=0.9, monotonic_ns=2_500_000_000))  # 2.5s
    assert out is None
    assert fsm.state == "ABSENT"


def test_observation_meets_debounce_emits_enter():
    fsm = PresenceFSM(config=CFG)
    fsm.observe(Observation(present=True, score=0.9, monotonic_ns=0))
    out = fsm.observe(Observation(present=True, score=0.9, monotonic_ns=3_000_000_000))  # 3.0s
    assert isinstance(out, Transition)
    assert out.from_state == "ABSENT"
    assert out.to_state == "PRESENT"
    assert out.event_type == "ENTER"
    assert out.candidate_duration_ms == 3000
    assert fsm.state == "PRESENT"


def test_candidate_cancel_on_flip():
    fsm = PresenceFSM(config=CFG)
    fsm.observe(Observation(present=True, score=0.9, monotonic_ns=0))
    fsm.observe(Observation(present=False, score=0.0, monotonic_ns=1_000_000_000))   # flip cancels
    out = fsm.observe(Observation(present=True, score=0.9, monotonic_ns=2_000_000_000))
    # Now the new candidate started at 2s; 2s later (i.e. at 4s) we still haven't met 3s.
    out2 = fsm.observe(Observation(present=True, score=0.9, monotonic_ns=4_000_000_000))
    assert out is None
    assert out2 is None
    assert fsm.state == "ABSENT"


def test_exit_after_present():
    fsm = PresenceFSM(config=CFG)
    fsm.observe(Observation(present=True, score=0.9, monotonic_ns=0))
    fsm.observe(Observation(present=True, score=0.9, monotonic_ns=3_000_000_000))
    assert fsm.state == "PRESENT"
    fsm.observe(Observation(present=False, score=0.0, monotonic_ns=4_000_000_000))
    out = fsm.observe(Observation(present=False, score=0.0, monotonic_ns=7_000_000_000))
    assert out is not None
    assert out.event_type == "EXIT"
    assert fsm.state == "ABSENT"


def test_force_exit_resets_to_absent():
    fsm = PresenceFSM(config=CFG)
    fsm.observe(Observation(present=True, score=0.9, monotonic_ns=0))
    fsm.observe(Observation(present=True, score=0.9, monotonic_ns=3_000_000_000))
    out = fsm.force_exit(monotonic_ns=10_000_000_000, reason="camera_lost")
    assert out is not None
    assert out.event_type == "EXIT"
    assert out.reason == "camera_lost"
    assert fsm.state == "ABSENT"


def test_force_exit_when_already_absent_returns_none():
    fsm = PresenceFSM(config=CFG)
    out = fsm.force_exit(monotonic_ns=0, reason="camera_lost")
    assert out is None


def test_independent_enter_and_exit_thresholds():
    cfg = FSMConfig(enter_seconds=5.0, exit_seconds=1.0)
    fsm = PresenceFSM(config=cfg)
    fsm.observe(Observation(present=True, score=0.9, monotonic_ns=0))
    out = fsm.observe(Observation(present=True, score=0.9, monotonic_ns=4_999_000_000))
    assert out is None
    out = fsm.observe(Observation(present=True, score=0.9, monotonic_ns=5_000_000_000))
    assert out is not None
    assert out.event_type == "ENTER"
    fsm.observe(Observation(present=False, score=0.0, monotonic_ns=6_000_000_000))
    out = fsm.observe(Observation(present=False, score=0.0, monotonic_ns=7_000_000_000))
    assert out is not None
    assert out.event_type == "EXIT"
