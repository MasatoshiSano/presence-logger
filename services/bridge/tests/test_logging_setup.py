import json
import logging
from pathlib import Path

from services.bridge.src.logging_setup import setup_logging


def test_bridge_setup_logging_emits_process_bridge(tmp_path: Path):
    setup_logging(process="bridge", device_id="rpi-test", log_dir=str(tmp_path), level="INFO")
    log = logging.getLogger("bridge.test")
    log.info("hello", extra={"event": "ping"})
    last_line = (tmp_path / "bridge.log").read_text(encoding="utf-8").strip().splitlines()[-1]
    line = json.loads(last_line)
    assert line["process"] == "bridge"
    assert line["event"] == "ping"
    assert line["device_id"] == "rpi-test"
