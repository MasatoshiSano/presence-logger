from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))


def correct_event_wall(*, sync_wall: datetime, sync_monotonic_ns: int,
                       event_monotonic_ns: int) -> datetime:
    """Given a sync baseline (wall, monotonic) pair, compute the wall clock of an event
    identified by its monotonic_ns timestamp."""
    delta_ns = sync_monotonic_ns - event_monotonic_ns
    delta = timedelta(microseconds=delta_ns / 1000)
    return sync_wall - delta


def format_mk_date_jst(dt: datetime) -> str:
    """Return MK_DATE 'YYYYMMDDhhmmss' in Asia/Tokyo regardless of input TZ."""
    dt_jst = dt.astimezone(JST)
    return dt_jst.strftime("%Y%m%d%H%M%S")
