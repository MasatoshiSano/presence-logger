from pipeline_monitor.linker import annotate_stages
from pipeline_monitor.model import InboxRow


def _row(eid, status):
    return InboxRow(event_id=eid, device_id="zero2", mk_date="20260717090000",
                    status=status, retry_count=0, last_error=None,
                    received_at_iso="2026-07-17T09:00:00Z", sent_at_iso=None)


def test_sent_row_seen_on_mqtt_is_full_pipeline():
    linked = annotate_stages([_row("e1", "sent")], {"e1"})
    assert linked[0].mqtt_seen is True
    assert "Oracle" in linked[0].stage
    assert "✓" in linked[0].stage


def test_received_row_is_stuck_before_oracle():
    linked = annotate_stages([_row("e2", "received")], {"e2"})
    assert linked[0].mqtt_seen is True
    assert "inbox" in linked[0].stage
    assert "✓" not in linked[0].stage        # not yet in Oracle


def test_row_not_in_mqtt_buffer():
    # arrived before the monitor started subscribing -> mqtt_seen False, still valid
    linked = annotate_stages([_row("e3", "sent")], set())
    assert linked[0].mqtt_seen is False


def test_failed_row_shows_giveup_not_pending():
    linked = annotate_stages([_row("e4", "failed")], {"e4"})
    assert "諦め" in linked[0].stage
    assert "✓" not in linked[0].stage
    assert "滞留" not in linked[0].stage  # 滞留(まだ狙える)とは区別する
