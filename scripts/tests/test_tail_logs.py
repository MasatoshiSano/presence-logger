from pathlib import Path


def test_tail_logs_follows_process_logs_not_child_mqtt():
    text = Path("scripts/tail-logs.sh").read_text(encoding="utf-8")
    after_exec = text.split("exec", 1)[1]
    assert "bridge.log" in after_exec
    assert "detector.log" in after_exec
    assert "*.log" not in after_exec
    assert "child-mqtt.log" in text
