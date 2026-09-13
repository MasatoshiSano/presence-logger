from pathlib import Path

from services.bridge.src.disk_reclaim import (
    CRITICAL_FREE_BYTES,
    KEEP_SENT_CRITICAL,
    KEEP_SENT_NORMAL,
    KEEP_SENT_WARN,
    WARN_FREE_BYTES,
    keep_sent_for_free,
    rotated_log_paths,
    unlink_rotated_logs,
)


def test_keep_sent_never_stops_logging_just_drops_confirmed():
    assert keep_sent_for_free(None) == KEEP_SENT_NORMAL
    assert keep_sent_for_free(WARN_FREE_BYTES) == KEEP_SENT_NORMAL
    assert keep_sent_for_free(WARN_FREE_BYTES - 1) == KEEP_SENT_WARN
    assert keep_sent_for_free(CRITICAL_FREE_BYTES) == KEEP_SENT_WARN
    assert keep_sent_for_free(CRITICAL_FREE_BYTES - 1) == KEEP_SENT_CRITICAL


def test_unlink_rotated_logs_leaves_current_file(tmp_path: Path):
    (tmp_path / "child-mqtt.log").write_text("keep\n", encoding="utf-8")
    (tmp_path / "child-mqtt.log.1").write_text("old\n", encoding="utf-8")
    (tmp_path / "bridge.log.2").write_text("old\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("no\n", encoding="utf-8")
    names = {p.name for p in rotated_log_paths(tmp_path)}
    assert names == {"child-mqtt.log.1", "bridge.log.2"}
    removed = unlink_rotated_logs(tmp_path)
    assert set(removed) == names
    assert (tmp_path / "child-mqtt.log").exists()
    assert not (tmp_path / "child-mqtt.log.1").exists()
    assert (tmp_path / "notes.txt").exists()
