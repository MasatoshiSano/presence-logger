from datetime import UTC, datetime

from services.bridge.src.time_correction import JST, correct_event_wall, format_mk_date_jst


def test_correct_event_wall_subtracts_monotonic_delta():
    sync_wall = datetime(2026, 4, 27, 17, 23, 51, tzinfo=JST)
    sync_mono_ns = 13_000_000_000
    event_mono_ns = 6_200_000_000      # 6.8 s before sync
    out = correct_event_wall(
        sync_wall=sync_wall, sync_monotonic_ns=sync_mono_ns, event_monotonic_ns=event_mono_ns
    )
    assert out == datetime(2026, 4, 27, 17, 23, 44, 200_000, tzinfo=JST)


def test_format_mk_date_jst_strips_tz_after_conversion():
    dt_utc = datetime(2026, 4, 27, 8, 23, 45, tzinfo=UTC)  # 17:23:45 JST
    assert format_mk_date_jst(dt_utc) == "20260427172345"


def test_correct_event_wall_handles_event_after_sync():
    # Event happens 2s AFTER sync was acquired (i.e., we already have wall clock).
    sync_wall = datetime(2026, 4, 27, 17, 23, 51, tzinfo=JST)
    sync_mono_ns = 13_000_000_000
    event_mono_ns = 15_000_000_000     # +2 s
    out = correct_event_wall(
        sync_wall=sync_wall, sync_monotonic_ns=sync_mono_ns, event_monotonic_ns=event_mono_ns
    )
    assert out == datetime(2026, 4, 27, 17, 23, 53, tzinfo=JST)
