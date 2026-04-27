# Presence Logger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Raspberry Pi 5 application that detects person presence from a USB camera and writes ENTER/EXIT events to an Oracle DB with exactly-once delivery, switching SNTP/Oracle endpoints by WiFi SSID.

**Architecture:** Three-container Docker Compose deployment (mosquitto + detector + bridge). Detector runs MediaPipe inference on USB camera frames, debounces state transitions, and publishes events via MQTT QoS=2. Bridge subscribes, persists to SQLite (idempotent), resolves SSID profile, MERGEs into Oracle (Thin/Thick × basic/wallet), and acks back to detector. All inter-container traffic stays in `presence-net` for K3s migration readiness.

**Tech Stack:** Python 3.11+, MediaPipe Object Detector (EfficientDet-Lite0), OpenCV, paho-mqtt, eclipse-mosquitto:2, python-oracledb (Thin/Thick), SQLite (WAL), pyyaml, python-json-logger, pytest, Docker Compose, systemd.

**Spec:** `docs/superpowers/specs/2026-04-27-presence-logger-design.md`

---

## Phase 0: Project Setup

### Task 0.1: Initialize git repo and base directory structure

**Files:**
- Create: `.gitignore`
- Create: `services/detector/src/__init__.py`
- Create: `services/bridge/src/__init__.py`
- Create: `services/detector/tests/__init__.py`
- Create: `services/bridge/tests/__init__.py`
- Create: `tests/integration/__init__.py`
- Create: `config/`, `docker/mosquitto/`, `scripts/`, `systemd/` directories

- [ ] **Step 1: Initialize git repository**

Run:
```bash
cd /home/pi/projects/presence-logger
git init -b main
git config user.email "presence-logger@local"
git config user.name "Presence Logger"
```

Expected: `Initialized empty Git repository in /home/pi/projects/presence-logger/.git/`

- [ ] **Step 2: Create directory structure**

Run:
```bash
mkdir -p services/detector/src services/detector/tests services/detector/models
mkdir -p services/bridge/src services/bridge/tests
mkdir -p tests/integration
mkdir -p config docker/mosquitto scripts systemd
touch services/detector/src/__init__.py
touch services/detector/tests/__init__.py
touch services/bridge/src/__init__.py
touch services/bridge/tests/__init__.py
touch tests/integration/__init__.py
```

- [ ] **Step 3: Write `.gitignore`**

Create `.gitignore`:
```gitignore
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
*.egg-info/
.pytest_cache/
.ruff_cache/
.mypy_cache/
.venv/
venv/

# Project artifacts
*.db
*.db-journal
*.db-wal
*.db-shm
*.tflite
/dist/
/build/

# Logs
*.log
logs/

# Secrets
secrets.env
.env
config/*.yaml
!config/*.yaml.example

# OS
.DS_Store
Thumbs.db

# IDE
.vscode/
.idea/
*.swp
```

- [ ] **Step 4: Verify structure**

Run:
```bash
find . -type d -not -path './.git*' | sort
```

Expected (excerpt):
```
./config
./docker/mosquitto
./scripts
./services/bridge/src
./services/bridge/tests
./services/detector/models
./services/detector/src
./services/detector/tests
./systemd
./tests/integration
```

- [ ] **Step 5: Commit**

```bash
git add .
git commit -m "chore: initial project skeleton"
```

---

### Task 0.2: Add Python tooling (pyproject.toml, ruff, pytest)

**Files:**
- Create: `pyproject.toml`
- Create: `services/detector/requirements.txt`
- Create: `services/bridge/requirements.txt`
- Create: `requirements-dev.txt`

- [ ] **Step 1: Write top-level `pyproject.toml`**

Create `pyproject.toml`:
```toml
[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "N", "UP", "B", "S", "C4", "T20", "PT"]
ignore = ["S101"]  # allow assert in tests

[tool.ruff.lint.per-file-ignores]
"**/tests/**" = ["S105", "S106"]
"tests/**" = ["S105", "S106"]

[tool.pytest.ini_options]
testpaths = ["services/detector/tests", "services/bridge/tests", "tests/integration"]
python_files = "test_*.py"
addopts = "-v --tb=short"
```

- [ ] **Step 2: Write `services/detector/requirements.txt`**

Create `services/detector/requirements.txt`:
```
opencv-python-headless==4.10.0.84
mediapipe==0.10.18
paho-mqtt==2.1.0
pyyaml==6.0.2
python-json-logger==2.0.7
```

- [ ] **Step 3: Write `services/bridge/requirements.txt`**

Create `services/bridge/requirements.txt`:
```
paho-mqtt==2.1.0
oracledb==2.5.1
pyyaml==6.0.2
python-json-logger==2.0.7
```

- [ ] **Step 4: Write `requirements-dev.txt`**

Create `requirements-dev.txt`:
```
pytest==8.3.4
pytest-mock==3.14.0
pytest-asyncio==0.25.0
ruff==0.8.4
freezegun==1.5.1
```

- [ ] **Step 5: Set up local virtualenv for development and tests**

Run:
```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements-dev.txt -r services/detector/requirements.txt -r services/bridge/requirements.txt
```

Expected: All packages installed without error.

- [ ] **Step 6: Verify pytest works**

Run: `.venv/bin/pytest --collect-only`

Expected: `no tests collected` (no tests yet, no error).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml requirements-dev.txt services/detector/requirements.txt services/bridge/requirements.txt
git commit -m "chore: add Python tooling and service requirements"
```

---

### Task 0.3: Create README skeleton

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write `README.md`**

Create `README.md`:
```markdown
# Presence Logger

Detects person presence from a USB camera on Raspberry Pi 5 and records ENTER/EXIT events
to Oracle DB with exactly-once delivery. SNTP server and Oracle endpoint switch automatically
based on connected WiFi SSID.

## Architecture

Three Docker containers:
- **mosquitto** — internal MQTT broker
- **detector** — camera capture + MediaPipe person detection + MQTT publish
- **bridge** — MQTT subscribe + SQLite buffer + Oracle MERGE + ACK

See `docs/superpowers/specs/2026-04-27-presence-logger-design.md` for the full design.

## Quick Start (development)

```bash
# Set up local Python env for tests
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt -r services/detector/requirements.txt -r services/bridge/requirements.txt

# Run tests
.venv/bin/pytest
```

## Production Install

See `scripts/install.sh` and `systemd/presence-logger.service`.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add README skeleton"
```

---

## Phase 0 complete

At this point you have:
- A fresh git repo on `main` branch
- Directory structure for two services (`detector`, `bridge`) + integration tests
- `.gitignore` excluding secrets, build artifacts, runtime config files (`config/*.yaml`)
- Python tooling (ruff, pytest) configured via `pyproject.toml`
- Per-service `requirements.txt` plus `requirements-dev.txt`
- Local virtualenv `.venv/` with all dependencies installed
- README skeleton

Run `git log --oneline` to confirm three commits exist:
```
chore: initial project skeleton
chore: add Python tooling and service requirements
docs: add README skeleton
```

---

## Phase 1: Shared Foundations

Each service has its own `config.py`, `logging_setup.py`, `time_source.py` modules following
the spec directory structure. Some code is duplicated between services intentionally — this
keeps services independently buildable and avoids cross-service Docker build context complexity.

### Task 1.1: Detector config loader (YAML + env var expansion)

**Files:**
- Create: `services/detector/src/config.py`
- Create: `services/detector/tests/test_config.py`

The detector reads two YAML files at startup: `device.yaml` and `detector.yaml`. The loader
must expand `${VAR}` placeholders against `os.environ` and validate required keys.

- [ ] **Step 1: Write failing test for basic YAML load**

Create `services/detector/tests/test_config.py`:
```python
import os
from pathlib import Path
import pytest
from services.detector.src.config import load_yaml, expand_env, ConfigError


def test_load_yaml_returns_dict(tmp_path: Path):
    p = tmp_path / "x.yaml"
    p.write_text("camera:\n  device: /dev/video0\n  width: 640\n")
    result = load_yaml(p)
    assert result == {"camera": {"device": "/dev/video0", "width": 640}}


def test_load_yaml_missing_file_raises(tmp_path: Path):
    with pytest.raises(ConfigError, match="not found"):
        load_yaml(tmp_path / "missing.yaml")


def test_expand_env_replaces_placeholder(monkeypatch):
    monkeypatch.setenv("MY_PASS", "secret123")
    assert expand_env({"password": "${MY_PASS}"}) == {"password": "secret123"}


def test_expand_env_recurses_into_nested_dict(monkeypatch):
    monkeypatch.setenv("DB_HOST", "10.0.0.1")
    src = {"oracle": {"host": "${DB_HOST}", "port": 1521}}
    assert expand_env(src) == {"oracle": {"host": "10.0.0.1", "port": 1521}}


def test_expand_env_unresolved_var_raises(monkeypatch):
    monkeypatch.delenv("NONEXISTENT", raising=False)
    with pytest.raises(ConfigError, match="NONEXISTENT"):
        expand_env({"x": "${NONEXISTENT}"})


def test_expand_env_literal_string_unchanged():
    assert expand_env({"x": "literal"}) == {"x": "literal"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest services/detector/tests/test_config.py -v`

Expected: `ModuleNotFoundError: No module named 'services.detector.src.config'`

- [ ] **Step 3: Implement `config.py`**

Create `services/detector/src/config.py`:
```python
import os
import re
from pathlib import Path
from typing import Any
import yaml

_ENV_RE = re.compile(r"\$\{([^}]+)\}")


class ConfigError(Exception):
    pass


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"top-level YAML must be a mapping in {path}")
    return data


def expand_env(value: Any) -> Any:
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            var = match.group(1)
            if var not in os.environ:
                raise ConfigError(f"environment variable not set: {var}")
            return os.environ[var]
        return _ENV_RE.sub(replace, value)
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    return value
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest services/detector/tests/test_config.py -v`

Expected: 5 passed.

- [ ] **Step 5: Add tests for required-key validation**

Append to `services/detector/tests/test_config.py`:
```python
from services.detector.src.config import load_detector_config, load_device_config


def test_load_detector_config_validates_required_keys(tmp_path: Path):
    p = tmp_path / "detector.yaml"
    p.write_text("camera:\n  device: /dev/video0\n")  # missing inference, debounce, mqtt, etc.
    with pytest.raises(ConfigError, match="missing required key"):
        load_detector_config(p)


def test_load_detector_config_returns_full_dict(tmp_path: Path):
    p = tmp_path / "detector.yaml"
    p.write_text("""
camera: {device: /dev/video0, width: 640, height: 480, warmup_frames: 5}
inference: {model_path: /opt/m.tflite, target_fps: 1.5, score_threshold: 0.5, category: person}
debounce: {enter_seconds: 3.0, exit_seconds: 3.0}
mqtt: {host: mosquitto, port: 1883, qos: 2, topic_event: presence/event, topic_ack: presence/event/ack, client_id_prefix: x}
retry: {initial_delay_seconds: 5, max_delay_seconds: 600, multiplier: 3}
buffer: {path: /tmp/x.db, max_rows: 100000}
""")
    cfg = load_detector_config(p)
    assert cfg["camera"]["device"] == "/dev/video0"
    assert cfg["debounce"]["enter_seconds"] == 3.0


def test_load_device_config_validates_station(tmp_path: Path):
    p = tmp_path / "device.yaml"
    p.write_text("device_id: foo\nstation:\n  sta_no1: '001'\n")  # missing sta_no2/3
    with pytest.raises(ConfigError, match="sta_no2"):
        load_device_config(p)


def test_load_device_config_resolves_hostname_when_null(tmp_path: Path, monkeypatch):
    p = tmp_path / "device.yaml"
    p.write_text("device_id: null\nstation: {sta_no1: '001', sta_no2: 'A', sta_no3: '01'}\n")
    hostname_file = tmp_path / "hostname"
    hostname_file.write_text("rpi5-test-01\n")
    monkeypatch.setattr("services.detector.src.config.HOSTNAME_FILE", str(hostname_file))
    cfg = load_device_config(p)
    assert cfg["device_id"] == "rpi5-test-01"


def test_load_device_config_keeps_explicit_device_id(tmp_path: Path):
    p = tmp_path / "device.yaml"
    p.write_text("device_id: my-explicit-id\nstation: {sta_no1: '001', sta_no2: 'A', sta_no3: '01'}\n")
    cfg = load_device_config(p)
    assert cfg["device_id"] == "my-explicit-id"
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `.venv/bin/pytest services/detector/tests/test_config.py -v`

Expected: `ImportError: cannot import name 'load_detector_config'` (or similar).

- [ ] **Step 7: Extend `config.py` with validators and hostname loader**

Append to `services/detector/src/config.py`:
```python
HOSTNAME_FILE = "/etc/host_hostname"

_DETECTOR_REQUIRED = {
    "camera": {"device", "width", "height", "warmup_frames"},
    "inference": {"model_path", "target_fps", "score_threshold", "category"},
    "debounce": {"enter_seconds", "exit_seconds"},
    "mqtt": {"host", "port", "qos", "topic_event", "topic_ack", "client_id_prefix"},
    "retry": {"initial_delay_seconds", "max_delay_seconds", "multiplier"},
    "buffer": {"path", "max_rows"},
}

_DEVICE_REQUIRED = {
    "station": {"sta_no1", "sta_no2", "sta_no3"},
}


def _validate_required(data: dict[str, Any], required: dict[str, set[str]], where: str) -> None:
    for section, keys in required.items():
        if section not in data:
            raise ConfigError(f"{where}: missing required section: {section}")
        if not isinstance(data[section], dict):
            raise ConfigError(f"{where}: section {section} must be a mapping")
        missing = keys - set(data[section].keys())
        if missing:
            raise ConfigError(f"{where}: missing required key(s) {sorted(missing)} in {section}")


def load_detector_config(path: Path) -> dict[str, Any]:
    data = expand_env(load_yaml(path))
    _validate_required(data, _DETECTOR_REQUIRED, str(path))
    return data


def _read_hostname_file() -> str:
    p = Path(HOSTNAME_FILE)
    if not p.exists():
        raise ConfigError(f"device_id is null but hostname file missing: {HOSTNAME_FILE}")
    return p.read_text(encoding="utf-8").strip()


def load_device_config(path: Path) -> dict[str, Any]:
    data = expand_env(load_yaml(path))
    _validate_required(data, _DEVICE_REQUIRED, str(path))
    if data.get("device_id") is None:
        data["device_id"] = _read_hostname_file()
    return data
```

- [ ] **Step 8: Run all tests**

Run: `.venv/bin/pytest services/detector/tests/test_config.py -v`

Expected: 9 passed.

- [ ] **Step 9: Lint**

Run: `.venv/bin/ruff check services/detector/src/config.py services/detector/tests/test_config.py`

Expected: `All checks passed!`

- [ ] **Step 10: Commit**

```bash
git add services/detector/src/config.py services/detector/tests/test_config.py
git commit -m "feat(detector): add YAML config loader with env expansion and validation"
```

---

### Task 1.2: Detector JSON logging setup

**Files:**
- Create: `services/detector/src/logging_setup.py`
- Create: `services/detector/tests/test_logging_setup.py`

The detector emits structured JSON lines with fixed common fields (`ts`, `level`, `logger`,
`process`, `pid`, `device_id`, `event`) plus event-specific fields. ISO 8601 timestamps with
TZ. Files rotate at 10 MB × 5.

- [ ] **Step 1: Write failing test for JSON formatter**

Create `services/detector/tests/test_logging_setup.py`:
```python
import json
import logging
import io
from services.detector.src.logging_setup import build_formatter, install_common_fields


def test_formatter_emits_iso_timestamp_with_tz():
    formatter = build_formatter()
    record = logging.LogRecord(
        name="detector.fsm", level=logging.INFO, pathname="x", lineno=1,
        msg="hello", args=(), exc_info=None,
    )
    install_common_fields(record, process="detector", device_id="rpi-01")
    output = formatter.format(record)
    parsed = json.loads(output)
    assert "ts" in parsed
    # ISO 8601 with offset, e.g. 2026-04-27T17:23:45.123+09:00
    assert "T" in parsed["ts"]
    assert ("+" in parsed["ts"]) or ("Z" in parsed["ts"])


def test_formatter_includes_required_common_fields():
    formatter = build_formatter()
    record = logging.LogRecord(
        name="detector.fsm", level=logging.INFO, pathname="x", lineno=1,
        msg="m", args=(), exc_info=None,
    )
    install_common_fields(record, process="detector", device_id="rpi-01")
    parsed = json.loads(formatter.format(record))
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "detector.fsm"
    assert parsed["process"] == "detector"
    assert parsed["device_id"] == "rpi-01"
    assert isinstance(parsed["pid"], int)


def test_formatter_includes_extra_fields():
    formatter = build_formatter()
    record = logging.LogRecord(
        name="detector.fsm", level=logging.INFO, pathname="x", lineno=1,
        msg="m", args=(), exc_info=None,
    )
    install_common_fields(record, process="detector", device_id="rpi-01")
    record.event = "transition"
    record.event_id = "abc-123"
    parsed = json.loads(formatter.format(record))
    assert parsed["event"] == "transition"
    assert parsed["event_id"] == "abc-123"
```

- [ ] **Step 2: Run test, expect failure**

Run: `.venv/bin/pytest services/detector/tests/test_logging_setup.py -v`

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `logging_setup.py`**

Create `services/detector/src/logging_setup.py`:
```python
import json
import logging
import os
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


_RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "taskName",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=_local_tz()).isoformat(timespec="milliseconds")
        out: dict[str, Any] = {
            "ts": ts,
            "level": record.levelname,
            "logger": record.name,
            "process": getattr(record, "_process", "unknown"),
            "device_id": getattr(record, "_device_id", "unknown"),
            "pid": os.getpid(),
        }
        # Include any extras attached to the record (non-reserved attributes).
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            out[key] = value
        if record.msg and "message" not in out:
            out["message"] = record.getMessage()
        if record.exc_info:
            out["error"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else "Exception",
                "message": str(record.exc_info[1]),
                "traceback": self.formatException(record.exc_info),
            }
        return json.dumps(out, ensure_ascii=False, default=str)


def _local_tz() -> timezone:
    # Use system's current local offset.
    return datetime.now().astimezone().tzinfo  # type: ignore[return-value]


def build_formatter() -> JsonFormatter:
    return JsonFormatter()


def install_common_fields(record: logging.LogRecord, *, process: str, device_id: str) -> None:
    record._process = process       # noqa: SLF001 (intentional sentinel attrs)
    record._device_id = device_id   # noqa: SLF001


def setup_logging(
    *,
    process: str,
    device_id: str,
    log_dir: str,
    level: str = "INFO",
) -> None:
    """Install root logger handlers (file + stdout) using the shared JSON format."""
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    formatter = build_formatter()

    file_handler = RotatingFileHandler(
        Path(log_dir) / f"{process}.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())
    root.addHandler(file_handler)
    root.addHandler(stdout_handler)

    # Inject process/device_id into every record via a Filter.
    class _CommonFieldsFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            install_common_fields(record, process=process, device_id=device_id)
            return True

    root.addFilter(_CommonFieldsFilter())
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest services/detector/tests/test_logging_setup.py -v`

Expected: 3 passed.

- [ ] **Step 5: Add test for `setup_logging` end-to-end**

Append to `services/detector/tests/test_logging_setup.py`:
```python
from pathlib import Path

def test_setup_logging_writes_to_file_and_rotates(tmp_path: Path):
    from services.detector.src.logging_setup import setup_logging
    setup_logging(process="detector", device_id="rpi-test", log_dir=str(tmp_path), level="INFO")
    log = logging.getLogger("detector.test")
    log.info("hello", extra={"event": "test_event", "value": 42})

    log_file = tmp_path / "detector.log"
    assert log_file.exists()
    contents = log_file.read_text(encoding="utf-8").strip()
    line = json.loads(contents.splitlines()[-1])
    assert line["event"] == "test_event"
    assert line["value"] == 42
    assert line["device_id"] == "rpi-test"
    assert line["logger"] == "detector.test"
```

- [ ] **Step 6: Run tests**

Run: `.venv/bin/pytest services/detector/tests/test_logging_setup.py -v`

Expected: 4 passed.

- [ ] **Step 7: Lint and commit**

```bash
.venv/bin/ruff check services/detector/src/logging_setup.py services/detector/tests/test_logging_setup.py
git add services/detector/src/logging_setup.py services/detector/tests/test_logging_setup.py
git commit -m "feat(detector): add JSON logging with rotation and common fields"
```

---

### Task 1.3: Detector time source (monotonic + wall clock + sync state)

**Files:**
- Create: `services/detector/src/time_source.py`
- Create: `services/detector/tests/test_time_source.py`

The detector needs three things from the clock layer:
1. `monotonic_ns()` — strictly increasing, never resets, used for backoff and time correction.
2. `wall_clock_iso()` — current `YYYYMMDDhhmmss` and ISO 8601 strings, only valid when SNTP is synced.
3. `is_synced()` — checks `timedatectl show -p NTPSynchronized --value` (returns "yes"/"no").

- [ ] **Step 1: Write failing test for `monotonic_ns`**

Create `services/detector/tests/test_time_source.py`:
```python
import subprocess
from unittest.mock import patch
import pytest
from services.detector.src.time_source import (
    TimeSource, format_mk_date, format_iso_with_tz,
)
from datetime import datetime, timezone, timedelta


def test_monotonic_ns_strictly_increasing():
    ts = TimeSource()
    a = ts.monotonic_ns()
    b = ts.monotonic_ns()
    assert b >= a
    assert isinstance(a, int)


def test_format_mk_date_returns_14_digits():
    dt = datetime(2026, 4, 27, 17, 23, 45, tzinfo=timezone(timedelta(hours=9)))
    assert format_mk_date(dt) == "20260427172345"


def test_format_iso_with_tz_includes_milliseconds_and_offset():
    dt = datetime(2026, 4, 27, 17, 23, 45, 123_000, tzinfo=timezone(timedelta(hours=9)))
    s = format_iso_with_tz(dt)
    assert s == "2026-04-27T17:23:45.123+09:00"


def test_is_synced_calls_timedatectl_yes():
    ts = TimeSource()
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="yes\n", stderr=""
        )
        assert ts.is_synced() is True


def test_is_synced_returns_false_when_no():
    ts = TimeSource()
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="no\n", stderr=""
        )
        assert ts.is_synced() is False


def test_is_synced_returns_false_on_subprocess_error():
    ts = TimeSource()
    with patch("subprocess.run", side_effect=FileNotFoundError):
        assert ts.is_synced() is False


def test_now_returns_aware_datetime():
    ts = TimeSource()
    now = ts.now()
    assert now.tzinfo is not None
```

- [ ] **Step 2: Run test, expect failure**

Run: `.venv/bin/pytest services/detector/tests/test_time_source.py -v`

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `time_source.py`**

Create `services/detector/src/time_source.py`:
```python
import subprocess
import time
from datetime import datetime


SYNC_COMMAND = ["timedatectl", "show", "-p", "NTPSynchronized", "--value"]


def format_mk_date(dt: datetime) -> str:
    return dt.strftime("%Y%m%d%H%M%S")


def format_iso_with_tz(dt: datetime) -> str:
    # ISO 8601 with milliseconds + offset. Python's isoformat() with timespec=milliseconds
    # gives the desired output when dt is timezone-aware.
    return dt.isoformat(timespec="milliseconds")


class TimeSource:
    """Wraps monotonic and wall-clock access plus SNTP sync polling."""

    def monotonic_ns(self) -> int:
        return time.monotonic_ns()

    def now(self) -> datetime:
        return datetime.now().astimezone()

    def is_synced(self) -> bool:
        try:
            r = subprocess.run(
                SYNC_COMMAND, capture_output=True, text=True, timeout=2.0, check=False
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False
        return r.stdout.strip().lower() == "yes"
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest services/detector/tests/test_time_source.py -v`

Expected: 7 passed.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check services/detector/src/time_source.py services/detector/tests/test_time_source.py
git add services/detector/src/time_source.py services/detector/tests/test_time_source.py
git commit -m "feat(detector): add time source (monotonic, wall, NTP sync check)"
```

---

### Task 1.4: Detector retry/backoff calculator

**Files:**
- Create: `services/detector/src/retry.py`
- Create: `services/detector/tests/test_retry.py`

Pure-function module that computes the next retry timestamp given a starting time, attempt
count, initial delay, multiplier, and cap. Used by the publish-retry loop and by the test
suite to verify scheduling.

- [ ] **Step 1: Write failing test**

Create `services/detector/tests/test_retry.py`:
```python
from datetime import datetime, timedelta, timezone
from services.detector.src.retry import next_retry_at, BackoffPolicy


def test_first_retry_uses_initial_delay():
    now = datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)
    policy = BackoffPolicy(initial=5.0, multiplier=3.0, cap=600.0)
    assert next_retry_at(now, attempt=1, policy=policy) == now + timedelta(seconds=5)


def test_second_retry_multiplies():
    now = datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)
    policy = BackoffPolicy(initial=5.0, multiplier=3.0, cap=600.0)
    assert next_retry_at(now, attempt=2, policy=policy) == now + timedelta(seconds=15)


def test_grows_5_15_45_135_405_then_caps():
    now = datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)
    policy = BackoffPolicy(initial=5.0, multiplier=3.0, cap=600.0)
    expected = [5, 15, 45, 135, 405, 600, 600, 600]
    actual = [
        (next_retry_at(now, attempt=i, policy=policy) - now).total_seconds()
        for i in range(1, 9)
    ]
    assert actual == expected


def test_zero_or_negative_attempt_raises():
    import pytest
    now = datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)
    policy = BackoffPolicy(initial=5.0, multiplier=3.0, cap=600.0)
    with pytest.raises(ValueError):
        next_retry_at(now, attempt=0, policy=policy)
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/bin/pytest services/detector/tests/test_retry.py -v`

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `retry.py`**

Create `services/detector/src/retry.py`:
```python
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class BackoffPolicy:
    initial: float       # seconds
    multiplier: float
    cap: float           # seconds, upper bound on a single delay


def next_retry_at(now: datetime, *, attempt: int, policy: BackoffPolicy) -> datetime:
    if attempt < 1:
        raise ValueError(f"attempt must be >= 1, got {attempt}")
    raw = policy.initial * (policy.multiplier ** (attempt - 1))
    delay = min(raw, policy.cap)
    return now + timedelta(seconds=delay)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest services/detector/tests/test_retry.py -v`

Expected: 4 passed.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check services/detector/src/retry.py services/detector/tests/test_retry.py
git add services/detector/src/retry.py services/detector/tests/test_retry.py
git commit -m "feat(detector): add exponential backoff calculator"
```

---

### Task 1.5: Bridge config loader (with profile parsing)

**Files:**
- Create: `services/bridge/src/config.py`
- Create: `services/bridge/tests/test_config.py`

The bridge reads four YAML files: `device.yaml`, `bridge.yaml`, `profiles.yaml`, plus the
shared envvar-backed secrets via `${VAR}`. The profile loader is more complex because it
must validate `client_mode`/`auth_mode` combinations and check wallet directory contents.

- [ ] **Step 1: Write failing tests for basic loader and profile validation**

Create `services/bridge/tests/test_config.py`:
```python
import os
from pathlib import Path
import pytest
from services.bridge.src.config import (
    load_yaml, expand_env, ConfigError,
    load_bridge_config, load_device_config, load_profiles_config,
    needs_thick_mode, list_all_sntp_servers,
)


def test_load_yaml_basic(tmp_path: Path):
    p = tmp_path / "x.yaml"
    p.write_text("a: 1\n")
    assert load_yaml(p) == {"a": 1}


def test_expand_env_basic(monkeypatch):
    monkeypatch.setenv("FOO", "bar")
    assert expand_env({"x": "${FOO}"}) == {"x": "bar"}


def test_load_profiles_basic_mode_valid(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ORACLE_PASSWORD_A", "p1")
    p = tmp_path / "profiles.yaml"
    p.write_text("""
profiles:
  factory_a_wifi:
    description: A
    sntp: {servers: [ntp.a]}
    oracle:
      client_mode: thin
      auth_mode: basic
      host: 10.0.0.1
      port: 1521
      service_name: PRDDB
      user: u
      password: ${ORACLE_PASSWORD_A}
      table_name: HF1RCM01
unknown_ssid_policy: hold
""")
    cfg = load_profiles_config(p)
    assert "factory_a_wifi" in cfg["profiles"]
    assert cfg["profiles"]["factory_a_wifi"]["oracle"]["password"] == "p1"
    assert cfg["unknown_ssid_policy"] == "hold"


def test_load_profiles_wallet_mode_requires_wallet_dir(tmp_path: Path):
    p = tmp_path / "profiles.yaml"
    p.write_text("""
profiles:
  factory_b_wifi:
    description: B
    sntp: {servers: [ntp.b]}
    oracle:
      client_mode: thin
      auth_mode: wallet
      dsn: myadb_high
      user: u
      password: pass
      table_name: HF1RCM01
unknown_ssid_policy: hold
""")
    with pytest.raises(ConfigError, match="wallet_dir"):
        load_profiles_config(p)


def test_load_profiles_basic_mode_requires_host(tmp_path: Path):
    p = tmp_path / "profiles.yaml"
    p.write_text("""
profiles:
  factory_a_wifi:
    description: A
    sntp: {servers: [ntp.a]}
    oracle:
      client_mode: thin
      auth_mode: basic
      port: 1521
      service_name: PRDDB
      user: u
      password: p
      table_name: HF1RCM01
unknown_ssid_policy: hold
""")
    with pytest.raises(ConfigError, match="host"):
        load_profiles_config(p)


def test_load_profiles_invalid_client_mode_rejected(tmp_path: Path):
    p = tmp_path / "profiles.yaml"
    p.write_text("""
profiles:
  x:
    description: X
    sntp: {servers: [ntp.x]}
    oracle:
      client_mode: heavy
      auth_mode: basic
      host: h
      port: 1521
      service_name: s
      user: u
      password: p
      table_name: HF1RCM01
unknown_ssid_policy: hold
""")
    with pytest.raises(ConfigError, match="client_mode"):
        load_profiles_config(p)


def test_load_profiles_invalid_unknown_ssid_policy(tmp_path: Path):
    p = tmp_path / "profiles.yaml"
    p.write_text("""
profiles: {}
unknown_ssid_policy: nope
""")
    with pytest.raises(ConfigError, match="unknown_ssid_policy"):
        load_profiles_config(p)


def test_needs_thick_mode_true_when_any_profile_thick():
    profiles = {
        "a": {"oracle": {"client_mode": "thin", "auth_mode": "basic"}},
        "b": {"oracle": {"client_mode": "thick", "auth_mode": "basic"}},
    }
    assert needs_thick_mode(profiles) is True


def test_needs_thick_mode_false_when_all_thin():
    profiles = {
        "a": {"oracle": {"client_mode": "thin", "auth_mode": "basic"}},
        "b": {"oracle": {"client_mode": "thin", "auth_mode": "wallet"}},
    }
    assert needs_thick_mode(profiles) is False


def test_list_all_sntp_servers_dedups_and_orders():
    profiles = {
        "a": {"sntp": {"servers": ["ntp.a", "ntp.shared"]}},
        "b": {"sntp": {"servers": ["ntp.b", "ntp.shared"]}},
    }
    out = list_all_sntp_servers(profiles)
    assert "ntp.a" in out and "ntp.b" in out and "ntp.shared" in out
    # No duplicates
    assert len(out) == len(set(out))
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/bin/pytest services/bridge/tests/test_config.py -v`

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `services/bridge/src/config.py`**

Create `services/bridge/src/config.py`:
```python
import os
import re
from pathlib import Path
from typing import Any
import yaml

_ENV_RE = re.compile(r"\$\{([^}]+)\}")
HOSTNAME_FILE = "/etc/host_hostname"

ALLOWED_CLIENT_MODES = {"thin", "thick"}
ALLOWED_AUTH_MODES = {"basic", "wallet"}
ALLOWED_UNKNOWN_POLICIES = {"hold", "use_last", "drop"}

_BASIC_REQUIRED = {"host", "port", "service_name", "user", "password"}
_WALLET_REQUIRED = {"dsn", "user", "password", "wallet_dir"}

_BRIDGE_REQUIRED = {
    "mqtt": {"host", "port", "qos", "topic_event", "topic_ack", "client_id"},
    "oracle": {"connect_timeout_seconds", "query_timeout_seconds", "pool_min", "pool_max", "instant_client_dir"},
    "network_watcher": {"poll_interval_seconds", "ssid_command"},
    "time_watcher": {"poll_interval_seconds", "sync_command"},
    "retry": {"initial_delay_seconds", "max_delay_seconds", "multiplier"},
    "circuit_breaker": {"permanent_ora_codes", "half_open_after_seconds"},
    "buffer": {"path", "max_rows"},
    "logging": {"level", "buffer_stats_interval_seconds"},
}
_DEVICE_REQUIRED = {"station": {"sta_no1", "sta_no2", "sta_no3"}}


class ConfigError(Exception):
    pass


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"top-level YAML must be a mapping in {path}")
    return data


def expand_env(value: Any) -> Any:
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            var = match.group(1)
            if var not in os.environ:
                raise ConfigError(f"environment variable not set: {var}")
            return os.environ[var]
        return _ENV_RE.sub(replace, value)
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    return value


def _validate_required(data: dict[str, Any], required: dict[str, set[str]], where: str) -> None:
    for section, keys in required.items():
        if section not in data:
            raise ConfigError(f"{where}: missing required section: {section}")
        if not isinstance(data[section], dict):
            raise ConfigError(f"{where}: section {section} must be a mapping")
        missing = keys - set(data[section].keys())
        if missing:
            raise ConfigError(f"{where}: missing required key(s) {sorted(missing)} in {section}")


def load_bridge_config(path: Path) -> dict[str, Any]:
    data = expand_env(load_yaml(path))
    _validate_required(data, _BRIDGE_REQUIRED, str(path))
    return data


def _read_hostname_file() -> str:
    p = Path(HOSTNAME_FILE)
    if not p.exists():
        raise ConfigError(f"device_id is null but hostname file missing: {HOSTNAME_FILE}")
    return p.read_text(encoding="utf-8").strip()


def load_device_config(path: Path) -> dict[str, Any]:
    data = expand_env(load_yaml(path))
    _validate_required(data, _DEVICE_REQUIRED, str(path))
    if data.get("device_id") is None:
        data["device_id"] = _read_hostname_file()
    return data


def _validate_oracle_section(name: str, oracle: dict[str, Any]) -> None:
    cm = oracle.get("client_mode")
    if cm not in ALLOWED_CLIENT_MODES:
        raise ConfigError(f"profile {name}: client_mode must be one of {sorted(ALLOWED_CLIENT_MODES)}, got {cm!r}")
    am = oracle.get("auth_mode")
    if am not in ALLOWED_AUTH_MODES:
        raise ConfigError(f"profile {name}: auth_mode must be one of {sorted(ALLOWED_AUTH_MODES)}, got {am!r}")
    required = _BASIC_REQUIRED if am == "basic" else _WALLET_REQUIRED
    missing = required - set(oracle.keys())
    if missing:
        raise ConfigError(f"profile {name} ({am}): missing required oracle key(s) {sorted(missing)}")


def load_profiles_config(path: Path) -> dict[str, Any]:
    data = expand_env(load_yaml(path))
    if "profiles" not in data or not isinstance(data["profiles"], dict):
        raise ConfigError(f"{path}: top-level must contain a 'profiles' mapping")
    policy = data.get("unknown_ssid_policy", "hold")
    if policy not in ALLOWED_UNKNOWN_POLICIES:
        raise ConfigError(f"unknown_ssid_policy must be one of {sorted(ALLOWED_UNKNOWN_POLICIES)}, got {policy!r}")
    data["unknown_ssid_policy"] = policy
    for name, profile in data["profiles"].items():
        if "oracle" not in profile or not isinstance(profile["oracle"], dict):
            raise ConfigError(f"profile {name}: missing 'oracle' section")
        if "sntp" not in profile or not isinstance(profile["sntp"], dict):
            raise ConfigError(f"profile {name}: missing 'sntp' section")
        if "servers" not in profile["sntp"] or not isinstance(profile["sntp"]["servers"], list):
            raise ConfigError(f"profile {name}: sntp.servers must be a list")
        _validate_oracle_section(name, profile["oracle"])
    return data


def needs_thick_mode(profiles: dict[str, Any]) -> bool:
    return any(p["oracle"].get("client_mode") == "thick" for p in profiles.values())


def list_all_sntp_servers(profiles: dict[str, Any]) -> list[str]:
    seen: list[str] = []
    for p in profiles.values():
        for s in p["sntp"]["servers"]:
            if s not in seen:
                seen.append(s)
    return seen
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest services/bridge/tests/test_config.py -v`

Expected: 10 passed.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check services/bridge/src/config.py services/bridge/tests/test_config.py
git add services/bridge/src/config.py services/bridge/tests/test_config.py
git commit -m "feat(bridge): add YAML config loader with profile validation"
```

---

### Task 1.6: Bridge JSON logging setup

**Files:**
- Create: `services/bridge/src/logging_setup.py`
- Create: `services/bridge/tests/test_logging_setup.py`

Mirror Task 1.2 for the bridge service. Same JSON format, same rotation, same common fields.
The code is intentionally duplicated so the bridge container can be built independently
without sharing a `common/` package across service Docker contexts.

- [ ] **Step 1: Copy detector logging setup as starting point**

Copy `services/detector/src/logging_setup.py` to `services/bridge/src/logging_setup.py`
verbatim (same module body — only the calling convention differs at import time).

Run:
```bash
cp services/detector/src/logging_setup.py services/bridge/src/logging_setup.py
```

- [ ] **Step 2: Write a smoke test for the bridge variant**

Create `services/bridge/tests/test_logging_setup.py`:
```python
import json
import logging
from pathlib import Path
from services.bridge.src.logging_setup import setup_logging


def test_bridge_setup_logging_emits_process_bridge(tmp_path: Path):
    setup_logging(process="bridge", device_id="rpi-test", log_dir=str(tmp_path), level="INFO")
    log = logging.getLogger("bridge.test")
    log.info("hello", extra={"event": "ping"})
    line = json.loads((tmp_path / "bridge.log").read_text(encoding="utf-8").strip().splitlines()[-1])
    assert line["process"] == "bridge"
    assert line["event"] == "ping"
    assert line["device_id"] == "rpi-test"
```

- [ ] **Step 3: Run test**

Run: `.venv/bin/pytest services/bridge/tests/test_logging_setup.py -v`

Expected: 1 passed.

- [ ] **Step 4: Lint and commit**

```bash
.venv/bin/ruff check services/bridge/src/logging_setup.py services/bridge/tests/test_logging_setup.py
git add services/bridge/src/logging_setup.py services/bridge/tests/test_logging_setup.py
git commit -m "feat(bridge): add JSON logging (mirrors detector setup)"
```

---

## Phase 1 complete

You now have:
- Detector: `config.py` (YAML + ${VAR} expansion + required-key validation + hostname fallback), `logging_setup.py` (JSON Lines + RotatingFileHandler + common fields filter), `time_source.py` (monotonic_ns + wall clock + NTP sync check), `retry.py` (BackoffPolicy + next_retry_at).
- Bridge: `config.py` (loads bridge.yaml/device.yaml/profiles.yaml; profile validation for client_mode × auth_mode; helpers `needs_thick_mode` and `list_all_sntp_servers`), `logging_setup.py`.

All modules have unit tests passing. `git log --oneline` should show six new commits since Phase 0:
```
feat(bridge): add JSON logging (mirrors detector setup)
feat(bridge): add YAML config loader with profile validation
feat(detector): add exponential backoff calculator
feat(detector): add time source (monotonic, wall, NTP sync check)
feat(detector): add JSON logging with rotation and common fields
feat(detector): add YAML config loader with env expansion and validation
```

---

## Phase 2: Detector Service

### Task 2.1: detector buffer (SQLite `pending_events` repository)

**Files:**
- Create: `services/detector/src/buffer.py`
- Create: `services/detector/tests/test_buffer.py`

CRUD layer for `detector_buf.db`. Schema and statuses follow spec section 6.2. Methods needed:
- `init(path)`: create file, run schema, set PRAGMAs.
- `insert_pending(event)`: insert new pending row.
- `mark_sent(event_id)` / `mark_acked(event_id)`: status transitions.
- `iter_due_for_retry(now, status)`: yield rows where `status == ?` and `next_retry_at_iso <= now`.
- `update_retry_metadata(event_id, retry_count, next_retry_at)`: bump on publish failure.
- `count()`: row count.
- `ring_evict(max_rows)`: delete oldest acked rows first, then sent, then pending if needed.

- [ ] **Step 1: Write failing tests**

Create `services/detector/tests/test_buffer.py`:
```python
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest
from services.detector.src.buffer import BufferRepository, PendingEvent


def _make_event(event_id: str, *, created_at: datetime, status: str = "pending",
                event_type: str = "ENTER", monotonic_ns: int = 0) -> PendingEvent:
    return PendingEvent(
        event_id=event_id,
        event_type=event_type,
        mk_date="20260427120000",
        monotonic_ns=monotonic_ns,
        wall_synced=True,
        score=0.9,
        status=status,
        created_at_iso=created_at.isoformat(timespec="milliseconds"),
        retry_count=0,
        next_retry_at_iso=None,
        last_publish_at_iso=None,
    )


def test_init_creates_db_with_pragmas(tmp_path: Path):
    repo = BufferRepository(tmp_path / "x.db")
    repo.init()
    with sqlite3.connect(tmp_path / "x.db") as c:
        mode = c.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"


def test_insert_pending_then_query_count(tmp_path: Path):
    repo = BufferRepository(tmp_path / "x.db")
    repo.init()
    repo.insert_pending(_make_event("e1", created_at=datetime.now(timezone.utc)))
    assert repo.count() == 1


def test_insert_pending_idempotent_on_event_id(tmp_path: Path):
    repo = BufferRepository(tmp_path / "x.db")
    repo.init()
    e = _make_event("e1", created_at=datetime.now(timezone.utc))
    repo.insert_pending(e)
    repo.insert_pending(e)  # second call should not raise nor duplicate
    assert repo.count() == 1


def test_mark_sent_then_acked(tmp_path: Path):
    repo = BufferRepository(tmp_path / "x.db")
    repo.init()
    repo.insert_pending(_make_event("e1", created_at=datetime.now(timezone.utc)))
    repo.mark_sent("e1")
    repo.mark_acked("e1")
    rows = list(repo.iter_due_for_retry(now_iso=datetime.now(timezone.utc).isoformat(), status="acked"))
    assert len(rows) == 1


def test_iter_due_for_retry_filters_by_time_and_status(tmp_path: Path):
    repo = BufferRepository(tmp_path / "x.db")
    repo.init()
    now = datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)
    e_due = _make_event("e_due", created_at=now)
    e_due.next_retry_at_iso = (now - timedelta(seconds=5)).isoformat()
    repo.insert_pending(e_due)
    e_future = _make_event("e_future", created_at=now)
    e_future.next_retry_at_iso = (now + timedelta(seconds=60)).isoformat()
    repo.insert_pending(e_future)
    due = [r.event_id for r in repo.iter_due_for_retry(now_iso=now.isoformat(), status="pending")]
    assert due == ["e_due"]


def test_update_retry_metadata_bumps_retry_count(tmp_path: Path):
    repo = BufferRepository(tmp_path / "x.db")
    repo.init()
    now = datetime.now(timezone.utc)
    repo.insert_pending(_make_event("e1", created_at=now))
    repo.update_retry_metadata("e1", retry_count=2, next_retry_at_iso="2026-04-27T12:00:30+00:00")
    row = repo.get("e1")
    assert row.retry_count == 2
    assert row.next_retry_at_iso == "2026-04-27T12:00:30+00:00"


def test_ring_evict_drops_acked_first(tmp_path: Path):
    repo = BufferRepository(tmp_path / "x.db")
    repo.init()
    now = datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)
    for i, status in enumerate(["acked", "acked", "sent", "pending"]):
        e = _make_event(f"e{i}", created_at=now + timedelta(seconds=i), status=status)
        repo.insert_pending(e)
        if status in ("sent", "acked"):
            repo.mark_sent(e.event_id)
        if status == "acked":
            repo.mark_acked(e.event_id)
    deleted = repo.ring_evict(max_rows=2)
    assert deleted == 2
    remaining = {r.event_id for r in repo.all_rows()}
    assert remaining == {"e2", "e3"}  # the two acked rows e0, e1 were dropped first


def test_ring_evict_falls_back_to_pending_when_only_pending_left(tmp_path: Path):
    repo = BufferRepository(tmp_path / "x.db")
    repo.init()
    now = datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)
    for i in range(3):
        repo.insert_pending(_make_event(f"e{i}", created_at=now + timedelta(seconds=i)))
    deleted = repo.ring_evict(max_rows=2)
    assert deleted == 1  # only the oldest pending dropped
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/bin/pytest services/detector/tests/test_buffer.py -v`

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `services/detector/src/buffer.py`**

Create `services/detector/src/buffer.py`:
```python
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterator, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_events (
  event_id            TEXT PRIMARY KEY,
  event_type          TEXT NOT NULL CHECK(event_type IN ('ENTER','EXIT')),
  mk_date             TEXT,
  monotonic_ns        INTEGER NOT NULL,
  wall_synced         INTEGER NOT NULL DEFAULT 0,
  score               REAL,
  status              TEXT NOT NULL CHECK(status IN ('pending','sent','acked')),
  created_at_iso      TEXT NOT NULL,
  retry_count         INTEGER NOT NULL DEFAULT 0,
  next_retry_at_iso   TEXT,
  last_publish_at_iso TEXT
);
CREATE INDEX IF NOT EXISTS idx_pending_events_status_retry
  ON pending_events(status, next_retry_at_iso);
CREATE INDEX IF NOT EXISTS idx_pending_events_created_at
  ON pending_events(created_at_iso);
"""

PRAGMAS = ["PRAGMA journal_mode = WAL", "PRAGMA synchronous = NORMAL"]


@dataclass
class PendingEvent:
    event_id: str
    event_type: str
    mk_date: Optional[str]
    monotonic_ns: int
    wall_synced: bool
    score: Optional[float]
    status: str
    created_at_iso: str
    retry_count: int
    next_retry_at_iso: Optional[str]
    last_publish_at_iso: Optional[str]


class BufferRepository:
    def __init__(self, path: Path | str):
        self.path = str(path)

    def init(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            for p in PRAGMAS:
                c.execute(p)
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def insert_pending(self, e: PendingEvent) -> None:
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO pending_events (event_id, event_type, mk_date, monotonic_ns,
                  wall_synced, score, status, created_at_iso, retry_count,
                  next_retry_at_iso, last_publish_at_iso)
                VALUES (:event_id, :event_type, :mk_date, :monotonic_ns,
                  :wall_synced, :score, :status, :created_at_iso, :retry_count,
                  :next_retry_at_iso, :last_publish_at_iso)
                ON CONFLICT(event_id) DO NOTHING
                """,
                {**asdict(e), "wall_synced": int(e.wall_synced)},
            )

    def mark_sent(self, event_id: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE pending_events SET status='sent' WHERE event_id=?", (event_id,))

    def mark_acked(self, event_id: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE pending_events SET status='acked' WHERE event_id=?", (event_id,))

    def update_retry_metadata(self, event_id: str, *, retry_count: int, next_retry_at_iso: str) -> None:
        with self._conn() as c:
            c.execute(
                """
                UPDATE pending_events
                SET retry_count=?, next_retry_at_iso=?, last_publish_at_iso=?
                WHERE event_id=?
                """,
                (retry_count, next_retry_at_iso, next_retry_at_iso, event_id),
            )

    def get(self, event_id: str) -> Optional[PendingEvent]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM pending_events WHERE event_id=?", (event_id,)).fetchone()
        return self._row_to_event(row) if row else None

    def iter_due_for_retry(self, *, now_iso: str, status: str) -> Iterator[PendingEvent]:
        with self._conn() as c:
            cur = c.execute(
                """
                SELECT * FROM pending_events
                WHERE status = ?
                  AND (next_retry_at_iso IS NULL OR next_retry_at_iso <= ?)
                ORDER BY created_at_iso ASC
                """,
                (status, now_iso),
            )
            for row in cur.fetchall():
                yield self._row_to_event(row)

    def all_rows(self) -> Iterator[PendingEvent]:
        with self._conn() as c:
            cur = c.execute("SELECT * FROM pending_events ORDER BY created_at_iso ASC")
            for row in cur.fetchall():
                yield self._row_to_event(row)

    def count(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM pending_events").fetchone()[0]

    def ring_evict(self, *, max_rows: int) -> int:
        """Delete oldest rows down to `max_rows`. Prefer acked, then sent, then pending."""
        deleted = 0
        with self._conn() as c:
            current = c.execute("SELECT COUNT(*) FROM pending_events").fetchone()[0]
            to_delete = max(0, current - max_rows)
            for status in ("acked", "sent", "pending"):
                if to_delete == 0:
                    break
                cur = c.execute(
                    """
                    SELECT event_id FROM pending_events
                    WHERE status=?
                    ORDER BY created_at_iso ASC
                    LIMIT ?
                    """,
                    (status, to_delete),
                )
                ids = [r[0] for r in cur.fetchall()]
                if ids:
                    c.executemany(
                        "DELETE FROM pending_events WHERE event_id=?",
                        [(i,) for i in ids],
                    )
                    deleted += len(ids)
                    to_delete -= len(ids)
        return deleted

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> PendingEvent:
        return PendingEvent(
            event_id=row["event_id"],
            event_type=row["event_type"],
            mk_date=row["mk_date"],
            monotonic_ns=row["monotonic_ns"],
            wall_synced=bool(row["wall_synced"]),
            score=row["score"],
            status=row["status"],
            created_at_iso=row["created_at_iso"],
            retry_count=row["retry_count"],
            next_retry_at_iso=row["next_retry_at_iso"],
            last_publish_at_iso=row["last_publish_at_iso"],
        )
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest services/detector/tests/test_buffer.py -v`

Expected: 8 passed.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check services/detector/src/buffer.py services/detector/tests/test_buffer.py
git add services/detector/src/buffer.py services/detector/tests/test_buffer.py
git commit -m "feat(detector): add SQLite buffer for pending events with ring eviction"
```

---

### Task 2.2: Detector FSM (debounce + state transitions)

**Files:**
- Create: `services/detector/src/fsm.py`
- Create: `services/detector/tests/test_fsm.py`

The FSM has two states: `ABSENT`, `PRESENT`. A transition is **proposed** when the observed
state differs from the current state. The proposal becomes a candidate and starts a timer
(monotonic). When the candidate is observed continuously for ≥ `enter_seconds` (or
`exit_seconds` for the opposite direction), it becomes confirmed and emits an event.
Any flip of observation cancels the candidate.

- [ ] **Step 1: Write failing tests**

Create `services/detector/tests/test_fsm.py`:
```python
import pytest
from services.detector.src.fsm import PresenceFSM, FSMConfig, Observation, Transition


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
    assert out is not None and out.event_type == "ENTER"
    fsm.observe(Observation(present=False, score=0.0, monotonic_ns=6_000_000_000))
    out = fsm.observe(Observation(present=False, score=0.0, monotonic_ns=7_000_000_000))
    assert out is not None and out.event_type == "EXIT"
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/bin/pytest services/detector/tests/test_fsm.py -v`

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `services/detector/src/fsm.py`**

Create `services/detector/src/fsm.py`:
```python
from dataclasses import dataclass
from typing import Literal, Optional

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
    reason: Optional[str] = None


class PresenceFSM:
    def __init__(self, *, config: FSMConfig):
        self._config = config
        self._state: State = "ABSENT"
        self._candidate_state: Optional[State] = None
        self._candidate_started_mono_ns: Optional[int] = None
        self._latest_score: float = 0.0

    @property
    def state(self) -> State:
        return self._state

    def observe(self, obs: Observation) -> Optional[Transition]:
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
        elapsed_ns = obs.monotonic_ns - (self._candidate_started_mono_ns or obs.monotonic_ns)
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

    def force_exit(self, *, monotonic_ns: int, reason: str) -> Optional[Transition]:
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
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest services/detector/tests/test_fsm.py -v`

Expected: 8 passed.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check services/detector/src/fsm.py services/detector/tests/test_fsm.py
git add services/detector/src/fsm.py services/detector/tests/test_fsm.py
git commit -m "feat(detector): add presence FSM with time-based debounce"
```

---

### Task 2.3: Camera wrapper

**Files:**
- Create: `services/detector/src/camera.py`
- Create: `services/detector/tests/test_camera.py`

Wraps `cv2.VideoCapture`. Provides `open()`, `read()` (returning a frame or `None`),
`close()`, and a `consecutive_failures` counter the FSM main loop uses to decide when to
force EXIT. Camera is not unit-testable end-to-end, but we can mock the OpenCV layer.

- [ ] **Step 1: Write failing tests with mocked cv2**

Create `services/detector/tests/test_camera.py`:
```python
from unittest.mock import patch, MagicMock
import numpy as np
import pytest
from services.detector.src.camera import Camera, CameraOpenError


def _make_cv2_mock(read_returns):
    cap = MagicMock()
    cap.isOpened.return_value = True
    cap.read.side_effect = read_returns
    return cap


def test_open_calls_videocapture_with_device():
    with patch("services.detector.src.camera.cv2") as cv2_mock:
        cv2_mock.VideoCapture.return_value.isOpened.return_value = True
        cam = Camera(device="/dev/video0", width=640, height=480, warmup_frames=0)
        cam.open()
        cv2_mock.VideoCapture.assert_called_once_with("/dev/video0")


def test_open_raises_when_isopened_false():
    with patch("services.detector.src.camera.cv2") as cv2_mock:
        cv2_mock.VideoCapture.return_value.isOpened.return_value = False
        cam = Camera(device="/dev/video0", width=640, height=480, warmup_frames=0)
        with pytest.raises(CameraOpenError):
            cam.open()


def test_warmup_consumes_n_frames():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cap = _make_cv2_mock([(True, frame)] * 5)
    with patch("services.detector.src.camera.cv2") as cv2_mock:
        cv2_mock.VideoCapture.return_value = cap
        cam = Camera(device="/dev/video0", width=640, height=480, warmup_frames=3)
        cam.open()
        assert cap.read.call_count == 3  # warmup frames consumed


def test_read_success_returns_frame_and_resets_failures():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cap = _make_cv2_mock([(False, None), (True, frame)])
    with patch("services.detector.src.camera.cv2") as cv2_mock:
        cv2_mock.VideoCapture.return_value = cap
        cam = Camera(device="/dev/video0", width=640, height=480, warmup_frames=0)
        cam.open()
        assert cam.read() is None
        assert cam.consecutive_failures == 1
        assert cam.read() is not None
        assert cam.consecutive_failures == 0


def test_close_releases_capture():
    cap = _make_cv2_mock([])
    with patch("services.detector.src.camera.cv2") as cv2_mock:
        cv2_mock.VideoCapture.return_value = cap
        cam = Camera(device="/dev/video0", width=640, height=480, warmup_frames=0)
        cam.open()
        cam.close()
        cap.release.assert_called_once()
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/bin/pytest services/detector/tests/test_camera.py -v`

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `services/detector/src/camera.py`**

Create `services/detector/src/camera.py`:
```python
from typing import Optional
import cv2
import numpy as np


class CameraOpenError(RuntimeError):
    pass


class Camera:
    def __init__(self, *, device: str, width: int, height: int, warmup_frames: int):
        self._device = device
        self._width = width
        self._height = height
        self._warmup_frames = warmup_frames
        self._cap: Optional[cv2.VideoCapture] = None
        self.consecutive_failures = 0

    def open(self) -> None:
        cap = cv2.VideoCapture(self._device)
        if not cap.isOpened():
            raise CameraOpenError(f"failed to open camera at {self._device}")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        for _ in range(self._warmup_frames):
            cap.read()
        self._cap = cap

    def read(self) -> Optional[np.ndarray]:
        if self._cap is None:
            raise CameraOpenError("camera not opened")
        ok, frame = self._cap.read()
        if not ok or frame is None:
            self.consecutive_failures += 1
            return None
        self.consecutive_failures = 0
        return frame

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest services/detector/tests/test_camera.py -v`

Expected: 5 passed.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check services/detector/src/camera.py services/detector/tests/test_camera.py
git add services/detector/src/camera.py services/detector/tests/test_camera.py
git commit -m "feat(detector): add USB camera wrapper with failure counter"
```

---

### Task 2.4: MediaPipe inference wrapper

**Files:**
- Create: `services/detector/src/inference.py`
- Create: `services/detector/tests/test_inference.py`

Wraps the MediaPipe ObjectDetector. Loads `efficientdet_lite0.tflite`, runs inference on a
frame, and returns whether a person was detected with score ≥ threshold. The MediaPipe API
is not trivial to mock; we test by injecting a fake detector that returns canned results.

- [ ] **Step 1: Write failing tests with injected fake detector**

Create `services/detector/tests/test_inference.py`:
```python
from dataclasses import dataclass
import numpy as np
import pytest
from services.detector.src.inference import PersonDetector, DetectionResult


@dataclass
class _FakeCategory:
    category_name: str
    score: float


@dataclass
class _FakeDetection:
    categories: list


@dataclass
class _FakeMpResult:
    detections: list


class _FakeBackend:
    def __init__(self, results: list):
        self._results = results
        self.calls = 0

    def detect(self, mp_image):  # noqa: ARG002
        r = self._results[self.calls]
        self.calls += 1
        return r


def _frame() -> np.ndarray:
    return np.zeros((480, 640, 3), dtype=np.uint8)


def test_returns_has_person_true_when_score_meets_threshold():
    backend = _FakeBackend([
        _FakeMpResult(detections=[
            _FakeDetection(categories=[_FakeCategory("person", 0.7)]),
        ])
    ])
    det = PersonDetector(backend=backend, score_threshold=0.5, target_category="person")
    r = det.detect(_frame())
    assert r.has_person is True
    assert r.top_score == 0.7
    assert r.detections_count == 1


def test_returns_has_person_false_when_below_threshold():
    backend = _FakeBackend([
        _FakeMpResult(detections=[
            _FakeDetection(categories=[_FakeCategory("person", 0.3)]),
        ])
    ])
    det = PersonDetector(backend=backend, score_threshold=0.5, target_category="person")
    r = det.detect(_frame())
    assert r.has_person is False
    assert r.top_score == 0.3


def test_ignores_non_person_categories():
    backend = _FakeBackend([
        _FakeMpResult(detections=[
            _FakeDetection(categories=[_FakeCategory("cat", 0.99)]),
        ])
    ])
    det = PersonDetector(backend=backend, score_threshold=0.5, target_category="person")
    r = det.detect(_frame())
    assert r.has_person is False


def test_empty_detections_returns_no_person():
    backend = _FakeBackend([_FakeMpResult(detections=[])])
    det = PersonDetector(backend=backend, score_threshold=0.5, target_category="person")
    r = det.detect(_frame())
    assert r.has_person is False
    assert r.top_score == 0.0
    assert r.detections_count == 0
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/bin/pytest services/detector/tests/test_inference.py -v`

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `services/detector/src/inference.py`**

Create `services/detector/src/inference.py`:
```python
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Protocol
import numpy as np


@dataclass(frozen=True)
class DetectionResult:
    has_person: bool
    top_score: float
    detections_count: int
    infer_ms: float


class _DetectBackend(Protocol):
    def detect(self, mp_image: Any) -> Any: ...


class PersonDetector:
    """Thin wrapper around MediaPipe ObjectDetector.
    The backend can be swapped in tests with a fake.
    """

    def __init__(self, *, backend: _DetectBackend, score_threshold: float, target_category: str):
        self._backend = backend
        self._threshold = score_threshold
        self._target = target_category

    @classmethod
    def from_model_path(cls, *, model_path: Path | str, score_threshold: float, target_category: str):
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision

        opts = mp_vision.ObjectDetectorOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
            score_threshold=score_threshold,
            category_allowlist=[target_category],
            running_mode=mp_vision.RunningMode.IMAGE,
        )
        backend = mp_vision.ObjectDetector.create_from_options(opts)
        return cls(backend=backend, score_threshold=score_threshold, target_category=target_category)

    def detect(self, frame_bgr: np.ndarray) -> DetectionResult:
        import time
        t0 = time.monotonic()
        # MediaPipe expects RGB; conversion is done lazily here to keep the interface simple.
        mp_image = self._to_mp_image(frame_bgr)
        result = self._backend.detect(mp_image)
        elapsed = (time.monotonic() - t0) * 1000.0

        top_score = 0.0
        count = 0
        for det in getattr(result, "detections", []):
            for cat in getattr(det, "categories", []):
                if cat.category_name == self._target and cat.score > top_score:
                    top_score = cat.score
                    count += 1
        return DetectionResult(
            has_person=top_score >= self._threshold,
            top_score=top_score,
            detections_count=count,
            infer_ms=elapsed,
        )

    @staticmethod
    def _to_mp_image(frame_bgr: np.ndarray) -> Any:
        # In tests with a fake backend we never reach this path; keep the dependency lazy.
        try:
            import mediapipe as mp
            import cv2
            rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            return mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        except ImportError:
            return frame_bgr
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest services/detector/tests/test_inference.py -v`

Expected: 4 passed.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check services/detector/src/inference.py services/detector/tests/test_inference.py
git add services/detector/src/inference.py services/detector/tests/test_inference.py
git commit -m "feat(detector): add MediaPipe person detector wrapper"
```

---

### Task 2.5: Detector MQTT client (publish + ACK subscribe)

**Files:**
- Create: `services/detector/src/mqtt_client.py`
- Create: `services/detector/tests/test_mqtt_client.py`

Encapsulates `paho-mqtt`. Methods:
- `connect_and_loop(host, port, ...)`: starts a background thread (`loop_start`).
- `publish_event(topic, payload_dict, qos=2)`: returns the paho `MessageInfo`; logs at INFO.
- `subscribe_ack(topic, callback)`: registers a callback with `(event_id, mk_date_committed)`.
- `disconnect()`.

For the unit tests we patch `paho.mqtt.client.Client`.

- [ ] **Step 1: Write failing tests**

Create `services/detector/tests/test_mqtt_client.py`:
```python
import json
from unittest.mock import MagicMock, patch
from services.detector.src.mqtt_client import DetectorMqttClient


def _msg(topic: str, payload: dict) -> MagicMock:
    m = MagicMock()
    m.topic = topic
    m.payload = json.dumps(payload).encode("utf-8")
    return m


def test_connect_starts_paho_loop():
    with patch("services.detector.src.mqtt_client.paho.Client") as paho_cls:
        client = paho_cls.return_value
        c = DetectorMqttClient(client_id_prefix="x")
        c.connect_and_loop(host="mosquitto", port=1883)
        client.connect.assert_called_once_with("mosquitto", 1883, keepalive=60)
        client.loop_start.assert_called_once()


def test_publish_event_serializes_payload_and_uses_qos2():
    with patch("services.detector.src.mqtt_client.paho.Client") as paho_cls:
        client = paho_cls.return_value
        client.publish.return_value = MagicMock(rc=0, mid=42)
        c = DetectorMqttClient(client_id_prefix="x")
        c.connect_and_loop(host="m", port=1883)
        info = c.publish_event("presence/event", {"event_id": "abc", "event": "ENTER"})
        client.publish.assert_called_once()
        args, kwargs = client.publish.call_args
        assert args[0] == "presence/event"
        assert json.loads(args[1]) == {"event_id": "abc", "event": "ENTER"}
        assert kwargs.get("qos") == 2
        assert info.mid == 42


def test_subscribe_ack_invokes_callback_on_message():
    received = []

    def on_ack(event_id: str, mk_date_committed: str) -> None:
        received.append((event_id, mk_date_committed))

    with patch("services.detector.src.mqtt_client.paho.Client") as paho_cls:
        client = paho_cls.return_value
        c = DetectorMqttClient(client_id_prefix="x")
        c.connect_and_loop(host="m", port=1883)
        c.subscribe_ack("presence/event/ack", on_ack)
        # Find the message handler that was registered, simulate a message:
        on_message = client.message_callback_add.call_args.args[1]
        on_message(client, None, _msg("presence/event/ack", {
            "event_id": "abc", "mk_date_committed": "20260427120000"
        }))
        assert received == [("abc", "20260427120000")]
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/bin/pytest services/detector/tests/test_mqtt_client.py -v`

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `services/detector/src/mqtt_client.py`**

Create `services/detector/src/mqtt_client.py`:
```python
import json
import logging
import uuid
from typing import Callable, Optional
import paho.mqtt.client as paho


_log = logging.getLogger("detector.mqtt")


class DetectorMqttClient:
    def __init__(self, *, client_id_prefix: str):
        self._client_id = f"{client_id_prefix}-{uuid.uuid4().hex[:8]}"
        self._client: Optional[paho.Client] = None

    def connect_and_loop(self, *, host: str, port: int, keepalive: int = 60) -> None:
        client = paho.Client(client_id=self._client_id, protocol=paho.MQTTv5)
        client.reconnect_delay_set(min_delay=1, max_delay=60)
        client.connect(host, port, keepalive=keepalive)
        client.loop_start()
        self._client = client

    def publish_event(self, topic: str, payload: dict, *, qos: int = 2) -> paho.MQTTMessageInfo:
        if self._client is None:
            raise RuntimeError("mqtt client not connected")
        body = json.dumps(payload, ensure_ascii=False)
        info = self._client.publish(topic, body, qos=qos)
        _log.info(
            "publish",
            extra={
                "event": "publish",
                "topic": topic,
                "qos": qos,
                "event_id": payload.get("event_id"),
                "payload_size_bytes": len(body),
                "mid": info.mid,
            },
        )
        return info

    def subscribe_ack(self, topic: str, callback: Callable[[str, str], None]) -> None:
        if self._client is None:
            raise RuntimeError("mqtt client not connected")

        def _on_message(_client, _userdata, msg) -> None:
            try:
                data = json.loads(msg.payload.decode("utf-8"))
            except Exception:
                _log.warning("ack_decode_failed", extra={"event": "ack_decode_failed", "topic": msg.topic})
                return
            event_id = data.get("event_id")
            mk = data.get("mk_date_committed")
            if event_id and mk:
                callback(event_id, mk)

        self._client.subscribe(topic, qos=2)
        self._client.message_callback_add(topic, _on_message)

    def disconnect(self) -> None:
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest services/detector/tests/test_mqtt_client.py -v`

Expected: 3 passed.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check services/detector/src/mqtt_client.py services/detector/tests/test_mqtt_client.py
git add services/detector/src/mqtt_client.py services/detector/tests/test_mqtt_client.py
git commit -m "feat(detector): add MQTT client (publish events, subscribe acks)"
```

---

### Task 2.6: Detector main loop integration

**Files:**
- Create: `services/detector/src/main.py`
- Create: `services/detector/tests/test_main_loop.py`

Wires the components: load config, init logging, open camera + detector + MQTT, then in a
loop: read frame, run inference, observe FSM, on transition → persist event in buffer →
publish → schedule retry; periodic stats; healthcheck touch; ACK callback marks events
acked. The unit test exercises a single iteration with mocked components.

- [ ] **Step 1: Write failing test for one iteration of the loop logic**

Create `services/detector/tests/test_main_loop.py`:
```python
from unittest.mock import MagicMock
from services.detector.src.main import process_observation, RuntimeContext
from services.detector.src.fsm import PresenceFSM, FSMConfig, Observation, Transition
from services.detector.src.buffer import BufferRepository
import services.detector.src.main as main_mod


def _ctx(*, fsm, buffer, mqtt, time_source, hostname="rpi-test", device_cfg=None):
    if device_cfg is None:
        device_cfg = {"device_id": hostname, "station": {"sta_no1": "001", "sta_no2": "A", "sta_no3": "01"}}
    return RuntimeContext(
        device_cfg=device_cfg,
        fsm=fsm,
        buffer=buffer,
        mqtt=mqtt,
        time_source=time_source,
        topic_event="presence/event",
        retry_policy=main_mod.BackoffPolicy(initial=5, multiplier=3, cap=600),
    )


def test_process_observation_no_transition_does_not_publish(tmp_path):
    fsm = PresenceFSM(config=FSMConfig(enter_seconds=3.0, exit_seconds=3.0))
    buf = BufferRepository(tmp_path / "x.db"); buf.init()
    mqtt = MagicMock()
    ts = MagicMock()
    ts.is_synced.return_value = True
    ts.now.return_value.isoformat.return_value = "2026-04-27T12:00:00+09:00"
    process_observation(_ctx(fsm=fsm, buffer=buf, mqtt=mqtt, time_source=ts),
                        Observation(present=False, score=0.0, monotonic_ns=0))
    assert mqtt.publish_event.call_count == 0
    assert buf.count() == 0


def test_process_observation_transition_persists_and_publishes(tmp_path, monkeypatch):
    fsm = PresenceFSM(config=FSMConfig(enter_seconds=3.0, exit_seconds=3.0))
    buf = BufferRepository(tmp_path / "x.db"); buf.init()
    mqtt = MagicMock()
    ts = MagicMock()
    ts.is_synced.return_value = True
    from datetime import datetime, timezone, timedelta
    ts.now.return_value = datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone(timedelta(hours=9)))
    monkeypatch.setattr("uuid.uuid4", lambda: type("U", (), {"hex": "deadbeef" * 4, "__str__": lambda self: "0192b6d2-fixed"})())

    ctx = _ctx(fsm=fsm, buffer=buf, mqtt=mqtt, time_source=ts)
    process_observation(ctx, Observation(present=True, score=0.8, monotonic_ns=0))
    process_observation(ctx, Observation(present=True, score=0.9, monotonic_ns=3_000_000_000))

    assert mqtt.publish_event.call_count == 1
    topic, payload = mqtt.publish_event.call_args.args[0], mqtt.publish_event.call_args.args[1]
    assert topic == "presence/event"
    assert payload["event"] == "ENTER"
    assert payload["event_time"] == "20260427120000"
    assert payload["wall_clock_synced"] is True
    assert payload["device_id"] == "rpi-test"
    assert payload["schema_version"] == 1
    assert buf.count() == 1
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/bin/pytest services/detector/tests/test_main_loop.py -v`

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `services/detector/src/main.py`**

Create `services/detector/src/main.py`:
```python
from __future__ import annotations
import logging
import os
import signal
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from services.detector.src import config as cfg_mod
from services.detector.src.buffer import BufferRepository, PendingEvent
from services.detector.src.camera import Camera
from services.detector.src.fsm import FSMConfig, Observation, PresenceFSM
from services.detector.src.inference import PersonDetector
from services.detector.src.logging_setup import setup_logging
from services.detector.src.mqtt_client import DetectorMqttClient
from services.detector.src.retry import BackoffPolicy, next_retry_at
from services.detector.src.time_source import TimeSource, format_iso_with_tz, format_mk_date


_log = logging.getLogger("detector.main")
HEALTH_FILE = "/tmp/detector.healthy"      # noqa: S108
DEFAULT_DETECTOR_YAML = "/etc/presence-logger/detector.yaml"
DEFAULT_DEVICE_YAML = "/etc/presence-logger/device.yaml"


@dataclass
class RuntimeContext:
    device_cfg: dict
    fsm: PresenceFSM
    buffer: BufferRepository
    mqtt: DetectorMqttClient
    time_source: TimeSource
    topic_event: str
    retry_policy: BackoffPolicy


def process_observation(ctx: RuntimeContext, obs: Observation) -> None:
    """Single FSM step. If a transition fires, persist + publish."""
    transition = ctx.fsm.observe(obs)
    if transition is None:
        return
    _emit_transition(ctx, transition)


def _emit_transition(ctx: RuntimeContext, transition) -> None:
    event_id = str(uuid.uuid4())
    synced = ctx.time_source.is_synced()
    now = ctx.time_source.now()
    payload = {
        "event_id": event_id,
        "event": transition.event_type,
        "event_time": format_mk_date(now) if synced else None,
        "event_time_iso": format_iso_with_tz(now) if synced else None,
        "monotonic_ns": transition.confirmed_at_monotonic_ns,
        "wall_clock_synced": synced,
        "device_id": ctx.device_cfg["device_id"],
        "score": transition.latest_score,
        "schema_version": 1,
    }
    pending = PendingEvent(
        event_id=event_id,
        event_type=transition.event_type,
        mk_date=payload["event_time"],
        monotonic_ns=transition.confirmed_at_monotonic_ns,
        wall_synced=synced,
        score=transition.latest_score,
        status="pending",
        created_at_iso=format_iso_with_tz(now),
        retry_count=0,
        next_retry_at_iso=None,
        last_publish_at_iso=None,
    )
    ctx.buffer.insert_pending(pending)
    ctx.mqtt.publish_event(ctx.topic_event, payload, qos=2)
    ctx.buffer.mark_sent(event_id)
    _log.info("transition", extra={
        "event": "transition",
        "from": transition.from_state,
        "to": transition.to_state,
        "event_type": transition.event_type,
        "event_id": event_id,
        "candidate_duration_ms": transition.candidate_duration_ms,
        "latest_score": transition.latest_score,
    })


def retry_pending(ctx: RuntimeContext) -> None:
    """Re-publish events that are pending or sent (no ACK yet) and due."""
    now = ctx.time_source.now()
    now_iso = format_iso_with_tz(now)
    for status in ("pending", "sent"):
        for row in ctx.buffer.iter_due_for_retry(now_iso=now_iso, status=status):
            payload = _build_resend_payload(ctx, row)
            ctx.mqtt.publish_event(ctx.topic_event, payload, qos=2)
            attempt = row.retry_count + 1
            ctx.buffer.update_retry_metadata(
                row.event_id,
                retry_count=attempt,
                next_retry_at_iso=format_iso_with_tz(next_retry_at(now, attempt=attempt, policy=ctx.retry_policy)),
            )


def _build_resend_payload(ctx: RuntimeContext, row: PendingEvent) -> dict:
    return {
        "event_id": row.event_id,
        "event": row.event_type,
        "event_time": row.mk_date,
        "event_time_iso": row.created_at_iso if row.wall_synced else None,
        "monotonic_ns": row.monotonic_ns,
        "wall_clock_synced": row.wall_synced,
        "device_id": ctx.device_cfg["device_id"],
        "score": row.score or 0.0,
        "schema_version": 1,
    }


def main() -> int:    # pragma: no cover (integration entry point)
    detector_yaml = Path(os.environ.get("DETECTOR_YAML", DEFAULT_DETECTOR_YAML))
    device_yaml = Path(os.environ.get("DEVICE_YAML", DEFAULT_DEVICE_YAML))
    detector_cfg = cfg_mod.load_detector_config(detector_yaml)
    device_cfg = cfg_mod.load_device_config(device_yaml)

    setup_logging(
        process="detector",
        device_id=device_cfg["device_id"],
        log_dir="/var/log/presence-logger",
        level=os.environ.get("LOG_LEVEL", "INFO"),
    )
    _log.info("startup", extra={"event": "startup", "config_path": str(detector_yaml)})

    camera = Camera(
        device=detector_cfg["camera"]["device"],
        width=detector_cfg["camera"]["width"],
        height=detector_cfg["camera"]["height"],
        warmup_frames=detector_cfg["camera"]["warmup_frames"],
    )
    camera.open()

    detector = PersonDetector.from_model_path(
        model_path=detector_cfg["inference"]["model_path"],
        score_threshold=detector_cfg["inference"]["score_threshold"],
        target_category=detector_cfg["inference"]["category"],
    )

    fsm = PresenceFSM(config=FSMConfig(
        enter_seconds=detector_cfg["debounce"]["enter_seconds"],
        exit_seconds=detector_cfg["debounce"]["exit_seconds"],
    ))
    buffer = BufferRepository(detector_cfg["buffer"]["path"])
    buffer.init()

    mqtt = DetectorMqttClient(client_id_prefix=detector_cfg["mqtt"]["client_id_prefix"])
    mqtt.connect_and_loop(
        host=os.environ.get("MQTT_HOST", detector_cfg["mqtt"]["host"]),
        port=detector_cfg["mqtt"]["port"],
    )

    def _on_ack(event_id: str, mk_date_committed: str) -> None:
        buffer.mark_acked(event_id)
        _log.info("ack_received", extra={
            "event": "ack_received",
            "event_id": event_id,
            "mk_date_committed": mk_date_committed,
        })

    mqtt.subscribe_ack(detector_cfg["mqtt"]["topic_ack"], _on_ack)

    time_source = TimeSource()
    ctx = RuntimeContext(
        device_cfg=device_cfg,
        fsm=fsm,
        buffer=buffer,
        mqtt=mqtt,
        time_source=time_source,
        topic_event=detector_cfg["mqtt"]["topic_event"],
        retry_policy=BackoffPolicy(
            initial=detector_cfg["retry"]["initial_delay_seconds"],
            multiplier=detector_cfg["retry"]["multiplier"],
            cap=detector_cfg["retry"]["max_delay_seconds"],
        ),
    )

    target_fps = detector_cfg["inference"]["target_fps"]
    period = 1.0 / target_fps
    last_health = 0.0
    last_retry_scan = 0.0
    last_stats = 0.0
    running = True

    def _stop(*_a):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    while running:
        loop_start = time.monotonic()

        frame = camera.read()
        if frame is None and camera.consecutive_failures >= 10:
            t = fsm.force_exit(monotonic_ns=time_source.monotonic_ns(), reason="camera_lost")
            if t is not None:
                _emit_transition(ctx, t)
            _log.error("camera_failure", extra={
                "event": "camera_failure",
                "consecutive_failures": camera.consecutive_failures,
            })

        if frame is not None:
            r = detector.detect(frame)
            obs = Observation(
                present=r.has_person,
                score=r.top_score,
                monotonic_ns=time_source.monotonic_ns(),
            )
            process_observation(ctx, obs)

        now = time.monotonic()
        if now - last_retry_scan >= 5.0:
            retry_pending(ctx)
            last_retry_scan = now
        if now - last_health >= 5.0:
            Path(HEALTH_FILE).touch()
            last_health = now
        if now - last_stats >= 60.0:
            _log.info("periodic", extra={
                "event": "periodic",
                "fps_target": target_fps,
                "buffer_pending": buffer.count(),
                "camera_consecutive_errors": camera.consecutive_failures,
            })
            last_stats = now

        elapsed = time.monotonic() - loop_start
        if elapsed < period:
            time.sleep(period - elapsed)

    camera.close()
    mqtt.disconnect()
    return 0


if __name__ == "__main__":     # pragma: no cover
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest services/detector/tests/test_main_loop.py -v`

Expected: 2 passed.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check services/detector/src/main.py services/detector/tests/test_main_loop.py
git add services/detector/src/main.py services/detector/tests/test_main_loop.py
git commit -m "feat(detector): wire main loop (camera, infer, fsm, buffer, mqtt, retry)"
```

---

### Task 2.7: Detector Dockerfile

**Files:**
- Create: `services/detector/Dockerfile`
- Create: `services/detector/.dockerignore`
- Create: `services/detector/models/.gitkeep`

The detector image needs OpenCV runtime (`libgl1`, `libglib2.0-0`) and the MediaPipe model
file. The TFLite model is a build-time artifact downloaded into `services/detector/models/`.

- [ ] **Step 1: Document model download in `services/detector/models/README.md`**

Create `services/detector/models/README.md`:
```markdown
# MediaPipe models

Place `efficientdet_lite0.tflite` here before building the detector image.

Download:
```bash
wget -O efficientdet_lite0.tflite \
  https://storage.googleapis.com/mediapipe-models/object_detector/efficientdet_lite0/float32/latest/efficientdet_lite0.tflite
```

The file is ignored by git (`*.tflite` is in `.gitignore`); only this README is tracked
via `models/.gitkeep`.
```

Also: `touch services/detector/models/.gitkeep`.

- [ ] **Step 2: Write `services/detector/.dockerignore`**

Create `services/detector/.dockerignore`:
```
__pycache__
*.pyc
tests/
.pytest_cache
.ruff_cache
```

- [ ] **Step 3: Write `services/detector/Dockerfile`**

Create `services/detector/Dockerfile`:
```dockerfile
FROM python:3.11-slim-bookworm

# OpenCV runtime libraries.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./services/detector/src/
COPY models/efficientdet_lite0.tflite /opt/models/efficientdet_lite0.tflite

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

CMD ["python", "-m", "services.detector.src.main"]
```

- [ ] **Step 4: Verify image builds locally**

Run:
```bash
# Place the model first (or skip the COPY for a smoke build by commenting line)
docker build -t presence-detector:test services/detector/
```

Expected: `Successfully tagged presence-detector:test`. (If the model is missing, the COPY
step fails — that's fine; the developer can drop the model in and rebuild.)

- [ ] **Step 5: Commit**

```bash
git add services/detector/Dockerfile services/detector/.dockerignore services/detector/models/README.md services/detector/models/.gitkeep
git commit -m "build(detector): add Dockerfile and model directory"
```

---

## Phase 2 complete

You now have a fully built detector service:
- Persistent SQLite buffer with ring eviction
- Time-based debounce FSM with `force_exit` for camera-lost recovery
- USB camera wrapper with consecutive-failure counter
- MediaPipe person detector wrapper (testable via injected backend)
- MQTT client with QoS=2 publish + ACK subscription
- `main.py` orchestrating the loop, retry sweep, healthcheck file, periodic stats
- Dockerfile ready to build (once `efficientdet_lite0.tflite` is in `models/`)

Run `.venv/bin/pytest services/detector/ -v` to confirm all detector unit tests pass.

`git log --oneline | head -10` should show seven new commits since Phase 1.

---

## Phase 3: Bridge Service

### Task 3.1: Bridge inbox repository (SQLite)

**Files:**
- Create: `services/bridge/src/inbox.py`
- Create: `services/bridge/tests/test_inbox.py`

CRUD layer for `bridge_buf.db`. Schema follows spec section 6.2. Methods needed:
- `init(path)`: file + schema + PRAGMAs.
- `insert_received(event)`: idempotent insert (`ON CONFLICT(event_id) DO NOTHING`).
- `mark_sent(event_id, mk_date_committed, profile_at_send, sent_at_iso)`.
- `update_retry(event_id, retry_count, next_retry_at, last_error)`.
- `iter_received_due(now_iso)` / `iter_sent_without_ack(now_iso)`.
- `get(event_id)` / `count()` / `ring_evict(max_rows)`.

- [ ] **Step 1: Write failing tests**

Create `services/bridge/tests/test_inbox.py`:
```python
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from services.bridge.src.inbox import InboxRepository, InboxEvent


def _evt(event_id: str, *, status: str = "received", received_at: datetime | None = None) -> InboxEvent:
    received_at = received_at or datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)
    return InboxEvent(
        event_id=event_id,
        event_type="ENTER",
        mk_date="20260427120000",
        monotonic_ns=1_000_000_000,
        wall_synced=True,
        device_id="rpi-test",
        score=0.9,
        raw_payload='{"event_id":"' + event_id + '"}',
        status=status,
        ssid_at_receive="factory_a_wifi",
        profile_at_send=None,
        mk_date_committed=None,
        received_at_iso=received_at.isoformat(),
        sent_at_iso=None,
        retry_count=0,
        next_retry_at_iso=None,
        last_error=None,
    )


def test_init_uses_wal(tmp_path: Path):
    repo = InboxRepository(tmp_path / "x.db")
    repo.init()
    with sqlite3.connect(tmp_path / "x.db") as c:
        assert c.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_insert_received_is_idempotent(tmp_path: Path):
    repo = InboxRepository(tmp_path / "x.db")
    repo.init()
    e = _evt("e1")
    repo.insert_received(e)
    repo.insert_received(e)
    assert repo.count() == 1


def test_mark_sent_persists_committed_fields(tmp_path: Path):
    repo = InboxRepository(tmp_path / "x.db")
    repo.init()
    repo.insert_received(_evt("e1"))
    repo.mark_sent(
        "e1",
        mk_date_committed="20260427120002",
        profile_at_send="factory_a_wifi",
        sent_at_iso="2026-04-27T12:00:02+00:00",
    )
    row = repo.get("e1")
    assert row.status == "sent"
    assert row.mk_date_committed == "20260427120002"
    assert row.profile_at_send == "factory_a_wifi"


def test_iter_received_due_filters_by_time(tmp_path: Path):
    repo = InboxRepository(tmp_path / "x.db")
    repo.init()
    now = datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)
    e_due = _evt("e_due"); e_due.next_retry_at_iso = (now - timedelta(seconds=5)).isoformat()
    e_future = _evt("e_future"); e_future.next_retry_at_iso = (now + timedelta(seconds=60)).isoformat()
    repo.insert_received(e_due)
    repo.insert_received(e_future)
    due = [r.event_id for r in repo.iter_received_due(now_iso=now.isoformat())]
    assert due == ["e_due"]


def test_iter_sent_without_ack_returns_status_sent_only(tmp_path: Path):
    repo = InboxRepository(tmp_path / "x.db")
    repo.init()
    repo.insert_received(_evt("e1"))
    repo.insert_received(_evt("e2"))
    repo.mark_sent("e2", mk_date_committed="20260427120000", profile_at_send="x", sent_at_iso="2026-04-27T12:00:00+00:00")
    sent_ids = [r.event_id for r in repo.iter_sent_without_ack(now_iso="2026-04-27T13:00:00+00:00")]
    assert sent_ids == ["e2"]


def test_update_retry_records_error(tmp_path: Path):
    repo = InboxRepository(tmp_path / "x.db")
    repo.init()
    repo.insert_received(_evt("e1"))
    repo.update_retry("e1", retry_count=2, next_retry_at_iso="2026-04-27T12:00:30+00:00", last_error="ORA-12541")
    row = repo.get("e1")
    assert row.retry_count == 2
    assert row.last_error == "ORA-12541"


def test_ring_evict_drops_sent_before_received(tmp_path: Path):
    repo = InboxRepository(tmp_path / "x.db")
    repo.init()
    base = datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)
    for i, status in enumerate(["sent", "sent", "received", "received"]):
        repo.insert_received(_evt(f"e{i}", status=status, received_at=base + timedelta(seconds=i)))
        if status == "sent":
            repo.mark_sent(f"e{i}", mk_date_committed="x", profile_at_send="p", sent_at_iso="x")
    deleted = repo.ring_evict(max_rows=2)
    assert deleted == 2
    remaining = {r.event_id for r in repo.all_rows()}
    assert remaining == {"e2", "e3"}
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/bin/pytest services/bridge/tests/test_inbox.py -v`

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `services/bridge/src/inbox.py`**

Create `services/bridge/src/inbox.py`:
```python
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterator, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS inbox (
  event_id            TEXT PRIMARY KEY,
  event_type          TEXT NOT NULL CHECK(event_type IN ('ENTER','EXIT')),
  mk_date             TEXT,
  monotonic_ns        INTEGER NOT NULL,
  wall_synced         INTEGER NOT NULL,
  device_id           TEXT,
  score               REAL,
  raw_payload         TEXT NOT NULL,
  status              TEXT NOT NULL CHECK(status IN ('received','sent')),
  ssid_at_receive     TEXT,
  profile_at_send     TEXT,
  mk_date_committed   TEXT,
  received_at_iso     TEXT NOT NULL,
  sent_at_iso         TEXT,
  retry_count         INTEGER NOT NULL DEFAULT 0,
  next_retry_at_iso   TEXT,
  last_error          TEXT
);
CREATE INDEX IF NOT EXISTS idx_inbox_status_retry ON inbox(status, next_retry_at_iso);
CREATE INDEX IF NOT EXISTS idx_inbox_received_at ON inbox(received_at_iso);
"""

PRAGMAS = ["PRAGMA journal_mode = WAL", "PRAGMA synchronous = NORMAL"]


@dataclass
class InboxEvent:
    event_id: str
    event_type: str
    mk_date: Optional[str]
    monotonic_ns: int
    wall_synced: bool
    device_id: Optional[str]
    score: Optional[float]
    raw_payload: str
    status: str
    ssid_at_receive: Optional[str]
    profile_at_send: Optional[str]
    mk_date_committed: Optional[str]
    received_at_iso: str
    sent_at_iso: Optional[str]
    retry_count: int
    next_retry_at_iso: Optional[str]
    last_error: Optional[str]


class InboxRepository:
    def __init__(self, path: Path | str):
        self.path = str(path)

    def init(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            for p in PRAGMAS:
                c.execute(p)
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def insert_received(self, e: InboxEvent) -> None:
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO inbox (event_id, event_type, mk_date, monotonic_ns, wall_synced,
                  device_id, score, raw_payload, status, ssid_at_receive, profile_at_send,
                  mk_date_committed, received_at_iso, sent_at_iso, retry_count,
                  next_retry_at_iso, last_error)
                VALUES (:event_id, :event_type, :mk_date, :monotonic_ns, :wall_synced,
                  :device_id, :score, :raw_payload, :status, :ssid_at_receive, :profile_at_send,
                  :mk_date_committed, :received_at_iso, :sent_at_iso, :retry_count,
                  :next_retry_at_iso, :last_error)
                ON CONFLICT(event_id) DO NOTHING
                """,
                {**asdict(e), "wall_synced": int(e.wall_synced)},
            )

    def mark_sent(self, event_id: str, *, mk_date_committed: str, profile_at_send: str,
                  sent_at_iso: str) -> None:
        with self._conn() as c:
            c.execute(
                """
                UPDATE inbox
                SET status='sent', mk_date_committed=?, profile_at_send=?, sent_at_iso=?
                WHERE event_id=?
                """,
                (mk_date_committed, profile_at_send, sent_at_iso, event_id),
            )

    def update_retry(self, event_id: str, *, retry_count: int, next_retry_at_iso: str,
                     last_error: str) -> None:
        with self._conn() as c:
            c.execute(
                """
                UPDATE inbox
                SET retry_count=?, next_retry_at_iso=?, last_error=?
                WHERE event_id=?
                """,
                (retry_count, next_retry_at_iso, last_error, event_id),
            )

    def get(self, event_id: str) -> Optional[InboxEvent]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM inbox WHERE event_id=?", (event_id,)).fetchone()
        return self._row_to_event(row) if row else None

    def iter_received_due(self, *, now_iso: str) -> Iterator[InboxEvent]:
        with self._conn() as c:
            cur = c.execute(
                """
                SELECT * FROM inbox
                WHERE status='received'
                  AND (next_retry_at_iso IS NULL OR next_retry_at_iso <= ?)
                ORDER BY received_at_iso ASC
                """,
                (now_iso,),
            )
            for row in cur.fetchall():
                yield self._row_to_event(row)

    def iter_sent_without_ack(self, *, now_iso: str) -> Iterator[InboxEvent]:
        # ACK-resend candidates: rows whose status='sent' (the bridge restart case).
        # `now_iso` is reserved for future age-based filtering; not currently used.
        del now_iso
        with self._conn() as c:
            cur = c.execute(
                "SELECT * FROM inbox WHERE status='sent' ORDER BY received_at_iso ASC"
            )
            for row in cur.fetchall():
                yield self._row_to_event(row)

    def all_rows(self) -> Iterator[InboxEvent]:
        with self._conn() as c:
            cur = c.execute("SELECT * FROM inbox ORDER BY received_at_iso ASC")
            for row in cur.fetchall():
                yield self._row_to_event(row)

    def count(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM inbox").fetchone()[0]

    def ring_evict(self, *, max_rows: int) -> int:
        deleted = 0
        with self._conn() as c:
            current = c.execute("SELECT COUNT(*) FROM inbox").fetchone()[0]
            to_delete = max(0, current - max_rows)
            for status in ("sent", "received"):
                if to_delete == 0:
                    break
                cur = c.execute(
                    """
                    SELECT event_id FROM inbox
                    WHERE status=?
                    ORDER BY received_at_iso ASC
                    LIMIT ?
                    """,
                    (status, to_delete),
                )
                ids = [r[0] for r in cur.fetchall()]
                if ids:
                    c.executemany("DELETE FROM inbox WHERE event_id=?", [(i,) for i in ids])
                    deleted += len(ids)
                    to_delete -= len(ids)
        return deleted

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> InboxEvent:
        return InboxEvent(
            event_id=row["event_id"],
            event_type=row["event_type"],
            mk_date=row["mk_date"],
            monotonic_ns=row["monotonic_ns"],
            wall_synced=bool(row["wall_synced"]),
            device_id=row["device_id"],
            score=row["score"],
            raw_payload=row["raw_payload"],
            status=row["status"],
            ssid_at_receive=row["ssid_at_receive"],
            profile_at_send=row["profile_at_send"],
            mk_date_committed=row["mk_date_committed"],
            received_at_iso=row["received_at_iso"],
            sent_at_iso=row["sent_at_iso"],
            retry_count=row["retry_count"],
            next_retry_at_iso=row["next_retry_at_iso"],
            last_error=row["last_error"],
        )
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest services/bridge/tests/test_inbox.py -v`

Expected: 7 passed.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check services/bridge/src/inbox.py services/bridge/tests/test_inbox.py
git add services/bridge/src/inbox.py services/bridge/tests/test_inbox.py
git commit -m "feat(bridge): add SQLite inbox repository with idempotent insert"
```

---

### Task 3.2: Network watcher (current SSID via nmcli)

**Files:**
- Create: `services/bridge/src/network_watcher.py`
- Create: `services/bridge/tests/test_network_watcher.py`

Polls `nmcli -t -f ACTIVE,SSID dev wifi` (default), parses output, returns the active SSID
or `None`. The watcher caches the latest value for the bridge's other components to query.

- [ ] **Step 1: Write failing tests**

Create `services/bridge/tests/test_network_watcher.py`:
```python
import subprocess
from unittest.mock import patch
from services.bridge.src.network_watcher import (
    NetworkWatcher, parse_nmcli_output,
)


def test_parse_nmcli_returns_active_ssid():
    out = "no:guest_wifi\nyes:factory_a_wifi\nno:other\n"
    assert parse_nmcli_output(out) == "factory_a_wifi"


def test_parse_nmcli_empty_returns_none():
    assert parse_nmcli_output("") is None


def test_parse_nmcli_no_active_returns_none():
    assert parse_nmcli_output("no:a\nno:b\n") is None


def test_parse_nmcli_handles_ssid_with_colon():
    # nmcli escapes embedded colons with backslash; mimic that.
    out = r"yes:my\:wifi"
    assert parse_nmcli_output(out) == "my:wifi"


def test_get_current_ssid_runs_command_and_parses():
    nw = NetworkWatcher(command="nmcli -t -f ACTIVE,SSID dev wifi")
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="yes:factory_a_wifi\n", stderr=""
        )
        assert nw.get_current_ssid() == "factory_a_wifi"


def test_get_current_ssid_returns_none_on_command_error():
    nw = NetworkWatcher(command="nmcli ...")
    with patch("subprocess.run", side_effect=FileNotFoundError):
        assert nw.get_current_ssid() is None


def test_cache_returns_last_value_until_refreshed():
    nw = NetworkWatcher(command="nmcli ...")
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="yes:factory_a_wifi\n", stderr=""
        )
        assert nw.get_current_ssid() == "factory_a_wifi"
    # Now nmcli is unavailable, but cache still serves last good value.
    with patch("subprocess.run", side_effect=FileNotFoundError):
        assert nw.cached_ssid == "factory_a_wifi"
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/bin/pytest services/bridge/tests/test_network_watcher.py -v`

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `services/bridge/src/network_watcher.py`**

Create `services/bridge/src/network_watcher.py`:
```python
import logging
import shlex
import subprocess
from typing import Optional


_log = logging.getLogger("bridge.network")


def parse_nmcli_output(stdout: str) -> Optional[str]:
    """Parse `nmcli -t -f ACTIVE,SSID dev wifi` output. Returns the active SSID or None.

    nmcli terse mode escapes colons in SSIDs with backslashes (e.g. `my\\:wifi`); we unescape
    that. The first column is `yes`/`no` for ACTIVE state.
    """
    for raw_line in stdout.splitlines():
        # Split on the first un-escaped colon.
        parts = _split_first_unescaped_colon(raw_line)
        if not parts or len(parts) < 2:
            continue
        active, ssid = parts[0], parts[1]
        if active.strip().lower() == "yes":
            return ssid.replace("\\:", ":")
    return None


def _split_first_unescaped_colon(line: str) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            buf.append(line[i:i + 2])
            i += 2
            continue
        if ch == ":":
            out.append("".join(buf))
            buf = []
            out.append(line[i + 1:])
            return out
        buf.append(ch)
        i += 1
    if buf:
        out.append("".join(buf))
    return out


class NetworkWatcher:
    def __init__(self, *, command: str):
        self._argv = shlex.split(command)
        self.cached_ssid: Optional[str] = None

    def get_current_ssid(self) -> Optional[str]:
        try:
            r = subprocess.run(self._argv, capture_output=True, text=True, timeout=5.0, check=False)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
            _log.warning("nmcli_failed", extra={"event": "nmcli_failed", "error": {"type": type(e).__name__, "message": str(e)}})
            return self.cached_ssid
        if r.returncode != 0:
            _log.warning("nmcli_nonzero", extra={"event": "nmcli_nonzero", "rc": r.returncode, "stderr": r.stderr.strip()})
            return self.cached_ssid
        ssid = parse_nmcli_output(r.stdout)
        self.cached_ssid = ssid
        return ssid
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest services/bridge/tests/test_network_watcher.py -v`

Expected: 7 passed.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check services/bridge/src/network_watcher.py services/bridge/tests/test_network_watcher.py
git add services/bridge/src/network_watcher.py services/bridge/tests/test_network_watcher.py
git commit -m "feat(bridge): add WiFi SSID watcher (nmcli via DBus mount)"
```

---

### Task 3.3: Time watcher and correction

**Files:**
- Create: `services/bridge/src/time_watcher.py`
- Create: `services/bridge/src/time_correction.py`
- Create: `services/bridge/tests/test_time_watcher.py`
- Create: `services/bridge/tests/test_time_correction.py`

`time_watcher.py`: polls `timedatectl show -p NTPSynchronized --value` and emits a callback
when the sync state changes. When sync transitions `false → true`, captures
`(sync_wall_dt, sync_monotonic_ns)` as the correction baseline.

`time_correction.py`: pure functions that compute the wall-clock instant of an event given
its `monotonic_ns`, the baseline, and Asia/Tokyo TZ.

- [ ] **Step 1: Write failing tests for time_correction (pure)**

Create `services/bridge/tests/test_time_correction.py`:
```python
from datetime import datetime, timezone, timedelta
from services.bridge.src.time_correction import correct_event_wall, format_mk_date_jst, JST


def test_correct_event_wall_subtracts_monotonic_delta():
    sync_wall = datetime(2026, 4, 27, 17, 23, 51, tzinfo=JST)
    sync_mono_ns = 13_000_000_000
    event_mono_ns = 6_200_000_000      # 6.8 s before sync
    out = correct_event_wall(
        sync_wall=sync_wall, sync_monotonic_ns=sync_mono_ns, event_monotonic_ns=event_mono_ns
    )
    assert out == datetime(2026, 4, 27, 17, 23, 44, 200_000, tzinfo=JST)


def test_format_mk_date_jst_strips_tz_after_conversion():
    dt_utc = datetime(2026, 4, 27, 8, 23, 45, tzinfo=timezone.utc)  # 17:23:45 JST
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
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/bin/pytest services/bridge/tests/test_time_correction.py -v`

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `services/bridge/src/time_correction.py`**

Create `services/bridge/src/time_correction.py`:
```python
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
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest services/bridge/tests/test_time_correction.py -v`

Expected: 3 passed.

- [ ] **Step 5: Write failing tests for time_watcher**

Create `services/bridge/tests/test_time_watcher.py`:
```python
import subprocess
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from services.bridge.src.time_watcher import TimeWatcher, SyncBaseline


def test_initial_state_unsynced():
    tw = TimeWatcher(command="timedatectl show -p NTPSynchronized --value", monotonic_clock=lambda: 0)
    assert tw.is_synced is False
    assert tw.baseline is None


def test_acquire_baseline_when_sync_transitions_true():
    mono = [10_000_000_000, 13_000_000_000]
    tw = TimeWatcher(
        command="timedatectl show -p NTPSynchronized --value",
        monotonic_clock=lambda: mono.pop(0),
        wall_clock=lambda: datetime(2026, 4, 27, 17, 23, 51, tzinfo=timezone(timedelta(hours=9))),
    )
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="no\n", stderr="")
        tw.poll()
        assert tw.is_synced is False
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="yes\n", stderr="")
        tw.poll()
    assert tw.is_synced is True
    assert tw.baseline is not None
    assert tw.baseline.sync_monotonic_ns == 13_000_000_000


def test_baseline_is_not_recaptured_while_already_synced():
    mono = [13_000_000_000, 20_000_000_000]
    wall_dts = [
        datetime(2026, 4, 27, 17, 23, 51, tzinfo=timezone(timedelta(hours=9))),
        datetime(2026, 4, 27, 18, 0, 0, tzinfo=timezone(timedelta(hours=9))),
    ]
    tw = TimeWatcher(
        command="t",
        monotonic_clock=lambda: mono.pop(0),
        wall_clock=lambda: wall_dts.pop(0),
    )
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="yes\n", stderr="")
        tw.poll()
        first_baseline = tw.baseline
        tw.poll()
    assert tw.baseline is first_baseline


def test_sync_loss_resets_baseline():
    mono = [13_000_000_000, 14_000_000_000]
    tw = TimeWatcher(
        command="t",
        monotonic_clock=lambda: mono.pop(0),
        wall_clock=lambda: datetime(2026, 4, 27, 17, 23, 51, tzinfo=timezone(timedelta(hours=9))),
    )
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="yes\n", stderr="")
        tw.poll()
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="no\n", stderr="")
        tw.poll()
    assert tw.is_synced is False
    assert tw.baseline is None
```

- [ ] **Step 6: Run, expect failure**

Run: `.venv/bin/pytest services/bridge/tests/test_time_watcher.py -v`

Expected: `ModuleNotFoundError`.

- [ ] **Step 7: Implement `services/bridge/src/time_watcher.py`**

Create `services/bridge/src/time_watcher.py`:
```python
import logging
import shlex
import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional


_log = logging.getLogger("bridge.time")


@dataclass(frozen=True)
class SyncBaseline:
    sync_wall: datetime
    sync_monotonic_ns: int


class TimeWatcher:
    def __init__(
        self,
        *,
        command: str,
        monotonic_clock: Optional[Callable[[], int]] = None,
        wall_clock: Optional[Callable[[], datetime]] = None,
    ):
        import time as _time
        self._argv = shlex.split(command)
        self._mono = monotonic_clock or _time.monotonic_ns
        self._wall = wall_clock or (lambda: datetime.now().astimezone())
        self.is_synced: bool = False
        self.baseline: Optional[SyncBaseline] = None

    def poll(self) -> None:
        synced_now = self._query()
        if synced_now and not self.is_synced:
            self.baseline = SyncBaseline(sync_wall=self._wall(), sync_monotonic_ns=self._mono())
            _log.info("sync_acquired", extra={
                "event": "sync_acquired",
                "sync_wall_iso": self.baseline.sync_wall.isoformat(timespec="milliseconds"),
                "sync_monotonic_ns": self.baseline.sync_monotonic_ns,
            })
        elif not synced_now and self.is_synced:
            _log.warning("sync_lost", extra={"event": "sync_lost"})
            self.baseline = None
        self.is_synced = synced_now

    def _query(self) -> bool:
        try:
            r = subprocess.run(self._argv, capture_output=True, text=True, timeout=5.0, check=False)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False
        return r.stdout.strip().lower() == "yes"
```

- [ ] **Step 8: Run all time tests**

Run: `.venv/bin/pytest services/bridge/tests/test_time_watcher.py services/bridge/tests/test_time_correction.py -v`

Expected: 7 passed.

- [ ] **Step 9: Lint and commit**

```bash
.venv/bin/ruff check services/bridge/src/time_watcher.py services/bridge/src/time_correction.py services/bridge/tests/test_time_watcher.py services/bridge/tests/test_time_correction.py
git add services/bridge/src/time_watcher.py services/bridge/src/time_correction.py services/bridge/tests/test_time_watcher.py services/bridge/tests/test_time_correction.py
git commit -m "feat(bridge): add SNTP sync watcher and time correction utilities"
```

---

### Task 3.4: Profile resolver

**Files:**
- Create: `services/bridge/src/profile_resolver.py`
- Create: `services/bridge/tests/test_profile_resolver.py`

Given the parsed profiles dict and a current SSID, return the matching profile or `None`.
Handles the `unknown_ssid_policy` setting and provides a `redact_for_logging(profile)`
helper that strips passwords and wallet credentials before they hit logs.

- [ ] **Step 1: Write failing tests**

Create `services/bridge/tests/test_profile_resolver.py`:
```python
import pytest
from services.bridge.src.profile_resolver import (
    ProfileResolver, redact_for_logging, ResolverDecision,
)


def _profiles():
    return {
        "factory_a_wifi": {
            "description": "A",
            "sntp": {"servers": ["ntp.a"]},
            "oracle": {
                "client_mode": "thin", "auth_mode": "basic",
                "host": "10.0.0.1", "port": 1521, "service_name": "S",
                "user": "u", "password": "p1", "table_name": "HF1RCM01",
            },
        },
        "factory_b_wifi": {
            "description": "B",
            "sntp": {"servers": ["ntp.b"]},
            "oracle": {
                "client_mode": "thin", "auth_mode": "wallet",
                "dsn": "myadb_high", "user": "u", "password": "p2",
                "wallet_dir": "/etc/presence-logger/wallets/factory_b",
                "wallet_password": "wp", "table_name": "HF1RCM01",
            },
        },
    }


def test_resolve_known_ssid_returns_profile():
    resolver = ProfileResolver(profiles=_profiles(), unknown_policy="hold")
    decision = resolver.resolve("factory_a_wifi")
    assert decision.action == "send"
    assert decision.profile_name == "factory_a_wifi"


def test_resolve_unknown_ssid_with_hold_policy():
    resolver = ProfileResolver(profiles=_profiles(), unknown_policy="hold")
    decision = resolver.resolve("guest_wifi")
    assert decision.action == "hold"
    assert decision.profile_name is None


def test_resolve_unknown_ssid_with_use_last_policy():
    resolver = ProfileResolver(profiles=_profiles(), unknown_policy="use_last")
    resolver.resolve("factory_a_wifi")
    decision = resolver.resolve("guest_wifi")
    assert decision.action == "send"
    assert decision.profile_name == "factory_a_wifi"


def test_resolve_unknown_ssid_with_drop_policy():
    resolver = ProfileResolver(profiles=_profiles(), unknown_policy="drop")
    decision = resolver.resolve("guest_wifi")
    assert decision.action == "drop"


def test_resolve_no_ssid_with_hold_policy():
    resolver = ProfileResolver(profiles=_profiles(), unknown_policy="hold")
    decision = resolver.resolve(None)
    assert decision.action == "hold"


def test_redact_for_logging_strips_secrets():
    out = redact_for_logging(_profiles()["factory_b_wifi"])
    assert out["oracle"]["password"] == "***"
    assert out["oracle"]["wallet_password"] == "***"
    assert out["oracle"]["user"] == "u"
    assert out["oracle"]["dsn"] == "myadb_high"


def test_redact_for_logging_does_not_mutate_input():
    profile = _profiles()["factory_a_wifi"]
    _ = redact_for_logging(profile)
    assert profile["oracle"]["password"] == "p1"
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/bin/pytest services/bridge/tests/test_profile_resolver.py -v`

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `services/bridge/src/profile_resolver.py`**

Create `services/bridge/src/profile_resolver.py`:
```python
import copy
from dataclasses import dataclass
from typing import Any, Literal, Optional


REDACTED = "***"
SECRET_KEYS = {"password", "wallet_password"}

Action = Literal["send", "hold", "drop"]


@dataclass(frozen=True)
class ResolverDecision:
    action: Action
    profile_name: Optional[str]


class ProfileResolver:
    def __init__(self, *, profiles: dict[str, Any], unknown_policy: str):
        self._profiles = profiles
        self._policy = unknown_policy
        self._last_known: Optional[str] = None

    def resolve(self, ssid: Optional[str]) -> ResolverDecision:
        if ssid and ssid in self._profiles:
            self._last_known = ssid
            return ResolverDecision(action="send", profile_name=ssid)
        if self._policy == "use_last" and self._last_known:
            return ResolverDecision(action="send", profile_name=self._last_known)
        if self._policy == "drop":
            return ResolverDecision(action="drop", profile_name=None)
        return ResolverDecision(action="hold", profile_name=None)

    def get(self, profile_name: str) -> dict[str, Any]:
        return self._profiles[profile_name]


def redact_for_logging(profile: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy then replace any secret-like value with '***'. Pure function."""
    redacted = copy.deepcopy(profile)
    _redact_in_place(redacted)
    return redacted


def _redact_in_place(node: Any) -> None:
    if isinstance(node, dict):
        for k, v in list(node.items()):
            if k in SECRET_KEYS and isinstance(v, str):
                node[k] = REDACTED
            else:
                _redact_in_place(v)
    elif isinstance(node, list):
        for item in node:
            _redact_in_place(item)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest services/bridge/tests/test_profile_resolver.py -v`

Expected: 7 passed.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check services/bridge/src/profile_resolver.py services/bridge/tests/test_profile_resolver.py
git add services/bridge/src/profile_resolver.py services/bridge/tests/test_profile_resolver.py
git commit -m "feat(bridge): add SSID profile resolver with policy and log redaction"
```

---

### Task 3.5: Circuit breaker

**Files:**
- Create: `services/bridge/src/circuit_breaker.py`
- Create: `services/bridge/tests/test_circuit_breaker.py`

Per-profile state machine: `closed → open` when a permanent ORA-error is detected; after
`half_open_after_seconds`, transitions to `half_open` and allows exactly one attempt.
Success returns to `closed`; failure goes back to `open` (with timer reset).

- [ ] **Step 1: Write failing tests**

Create `services/bridge/tests/test_circuit_breaker.py`:
```python
from datetime import datetime, timedelta, timezone
from services.bridge.src.circuit_breaker import CircuitBreaker, CircuitState, is_permanent_error


def test_initial_state_is_closed():
    cb = CircuitBreaker(half_open_after_seconds=900, permanent_codes={942, 1017})
    assert cb.state_for("p1") == "closed"


def test_record_failure_with_permanent_code_opens_circuit():
    cb = CircuitBreaker(half_open_after_seconds=900, permanent_codes={942})
    now = datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)
    cb.record_failure("p1", ora_code=942, now=now)
    assert cb.state_for("p1", now=now) == "open"


def test_record_failure_with_transient_code_does_not_open():
    cb = CircuitBreaker(half_open_after_seconds=900, permanent_codes={942})
    now = datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)
    cb.record_failure("p1", ora_code=12541, now=now)
    assert cb.state_for("p1", now=now) == "closed"


def test_open_circuit_transitions_to_half_open_after_timeout():
    cb = CircuitBreaker(half_open_after_seconds=900, permanent_codes={942})
    open_at = datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)
    cb.record_failure("p1", ora_code=942, now=open_at)
    later = open_at + timedelta(seconds=901)
    assert cb.state_for("p1", now=later) == "half_open"


def test_half_open_success_closes_circuit():
    cb = CircuitBreaker(half_open_after_seconds=900, permanent_codes={942})
    open_at = datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)
    cb.record_failure("p1", ora_code=942, now=open_at)
    later = open_at + timedelta(seconds=901)
    cb.record_success("p1", now=later)
    assert cb.state_for("p1", now=later) == "closed"


def test_half_open_failure_reopens_circuit():
    cb = CircuitBreaker(half_open_after_seconds=900, permanent_codes={942})
    open_at = datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)
    cb.record_failure("p1", ora_code=942, now=open_at)
    later = open_at + timedelta(seconds=901)
    cb.record_failure("p1", ora_code=942, now=later)
    much_later = later + timedelta(seconds=300)
    assert cb.state_for("p1", now=much_later) == "open"


def test_is_permanent_error_helper():
    assert is_permanent_error(942, permanent_codes={942, 1017}) is True
    assert is_permanent_error(12541, permanent_codes={942, 1017}) is False
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/bin/pytest services/bridge/tests/test_circuit_breaker.py -v`

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `services/bridge/src/circuit_breaker.py`**

Create `services/bridge/src/circuit_breaker.py`:
```python
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal, Optional


CircuitState = Literal["closed", "open", "half_open"]


@dataclass
class _ProfileEntry:
    state: CircuitState = "closed"
    opened_at: Optional[datetime] = None
    last_ora_code: Optional[int] = None


def is_permanent_error(ora_code: Optional[int], *, permanent_codes: set[int]) -> bool:
    return ora_code is not None and ora_code in permanent_codes


class CircuitBreaker:
    def __init__(self, *, half_open_after_seconds: int, permanent_codes: set[int]):
        self._timeout = timedelta(seconds=half_open_after_seconds)
        self._permanent = permanent_codes
        self._entries: dict[str, _ProfileEntry] = {}

    def _entry(self, profile: str) -> _ProfileEntry:
        return self._entries.setdefault(profile, _ProfileEntry())

    def state_for(self, profile: str, *, now: Optional[datetime] = None) -> CircuitState:
        e = self._entry(profile)
        if e.state == "open" and e.opened_at is not None and now is not None:
            if now - e.opened_at >= self._timeout:
                e.state = "half_open"
        return e.state

    def record_failure(self, profile: str, *, ora_code: Optional[int], now: datetime) -> None:
        e = self._entry(profile)
        if not is_permanent_error(ora_code, permanent_codes=self._permanent):
            return
        e.state = "open"
        e.opened_at = now
        e.last_ora_code = ora_code

    def record_success(self, profile: str, *, now: datetime) -> None:
        e = self._entry(profile)
        e.state = "closed"
        e.opened_at = None
        e.last_ora_code = None
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest services/bridge/tests/test_circuit_breaker.py -v`

Expected: 7 passed.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check services/bridge/src/circuit_breaker.py services/bridge/tests/test_circuit_breaker.py
git add services/bridge/src/circuit_breaker.py services/bridge/tests/test_circuit_breaker.py
git commit -m "feat(bridge): add per-profile circuit breaker for permanent Oracle errors"
```

---

### Task 3.6: Oracle client (Thin/Thick × basic/wallet, MERGE)

**Files:**
- Create: `services/bridge/src/oracle_client.py`
- Create: `services/bridge/tests/test_oracle_client.py`

Two responsibilities:
1. **Process-wide initialization**: scan all profiles; if any `client_mode=thick`, call
   `oracledb.init_oracle_client(lib_dir=...)` at startup. Otherwise stay in thin mode.
2. **Per-profile connection + MERGE**: open a connection using `auth_mode=basic|wallet`,
   execute the MERGE statement, return the rows-affected count and any ORA error code.

For tests we patch `oracledb.connect` and `oracledb.init_oracle_client`.

- [ ] **Step 1: Write failing tests**

Create `services/bridge/tests/test_oracle_client.py`:
```python
from unittest.mock import patch, MagicMock
import pytest
from services.bridge.src.oracle_client import (
    init_oracle_client_for_profiles, build_merge_statement, open_connection,
    execute_merge, MergeResult,
)


def test_build_merge_statement_targets_correct_table():
    sql = build_merge_statement(table_name="HF1RCM01")
    assert "MERGE INTO HF1RCM01" in sql
    assert "WHEN NOT MATCHED THEN" in sql
    assert "INSERT (MK_DATE, STA_NO1, STA_NO2, STA_NO3, T1_STATUS)" in sql


def test_init_oracle_client_skips_when_all_thin():
    profiles = {
        "a": {"oracle": {"client_mode": "thin"}},
        "b": {"oracle": {"client_mode": "thin"}},
    }
    with patch("services.bridge.src.oracle_client.oracledb") as o:
        mode = init_oracle_client_for_profiles(profiles, instant_client_dir="/opt/oc")
        o.init_oracle_client.assert_not_called()
        assert mode == "thin"


def test_init_oracle_client_invokes_thick_when_any_profile_thick(tmp_path):
    profiles = {
        "a": {"oracle": {"client_mode": "thin"}},
        "b": {"oracle": {"client_mode": "thick"}},
    }
    ic_dir = tmp_path / "instantclient"
    ic_dir.mkdir()
    with patch("services.bridge.src.oracle_client.oracledb") as o:
        mode = init_oracle_client_for_profiles(profiles, instant_client_dir=str(ic_dir))
        o.init_oracle_client.assert_called_once_with(lib_dir=str(ic_dir))
        assert mode == "thick"


def test_init_oracle_client_thick_missing_dir_raises(tmp_path):
    profiles = {"b": {"oracle": {"client_mode": "thick"}}}
    with pytest.raises(RuntimeError, match="Instant Client"):
        init_oracle_client_for_profiles(profiles, instant_client_dir=str(tmp_path / "nonexistent"))


def test_open_connection_basic_mode_uses_makedsn():
    cfg = {
        "client_mode": "thin", "auth_mode": "basic", "host": "h", "port": 1521,
        "service_name": "S", "user": "u", "password": "p", "table_name": "HF1RCM01",
    }
    with patch("services.bridge.src.oracle_client.oracledb") as o:
        o.makedsn.return_value = "DSN"
        open_connection(cfg)
        o.makedsn.assert_called_once_with("h", 1521, service_name="S")
        o.connect.assert_called_once_with(user="u", password="p", dsn="DSN")


def test_open_connection_wallet_mode_uses_wallet_kwargs():
    cfg = {
        "client_mode": "thin", "auth_mode": "wallet", "dsn": "myadb_high",
        "user": "u", "password": "p", "wallet_dir": "/etc/wallets/x",
        "wallet_password": "wp", "table_name": "HF1RCM01",
    }
    with patch("services.bridge.src.oracle_client.oracledb") as o:
        open_connection(cfg)
        o.connect.assert_called_once_with(
            user="u", password="p", dsn="myadb_high",
            config_dir="/etc/wallets/x", wallet_location="/etc/wallets/x",
            wallet_password="wp",
        )


def test_open_connection_wallet_mode_omits_wallet_password_when_absent():
    cfg = {
        "client_mode": "thin", "auth_mode": "wallet", "dsn": "tcps_dsn",
        "user": "u", "password": "p", "wallet_dir": "/etc/wallets/x",
        "table_name": "HF1RCM01",
    }
    with patch("services.bridge.src.oracle_client.oracledb") as o:
        open_connection(cfg)
        kwargs = o.connect.call_args.kwargs
        assert "wallet_password" not in kwargs


def test_execute_merge_returns_rows_affected_and_no_error():
    cursor = MagicMock()
    cursor.rowcount = 1
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    result = execute_merge(
        conn, table_name="HF1RCM01",
        mk_date="20260427120000", sta_no1="001", sta_no2="A", sta_no3="01", t1_status=1,
    )
    assert isinstance(result, MergeResult)
    assert result.rows_affected == 1
    assert result.ora_code is None
    cursor.execute.assert_called_once()
    conn.commit.assert_called_once()


def test_execute_merge_captures_ora_code_on_database_error():
    import oracledb as _oracledb_real_module  # imported only for type
    cursor = MagicMock()
    err = MagicMock()
    err.code = 942
    err.message = "ORA-00942: table or view does not exist"
    db_error = type("DatabaseError", (Exception,), {})
    db_error_instance = db_error()
    db_error_instance.args = (err,)
    cursor.execute.side_effect = db_error_instance
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor

    with patch("services.bridge.src.oracle_client.oracledb") as o:
        o.DatabaseError = db_error
        result = execute_merge(
            conn, table_name="HF1RCM01",
            mk_date="20260427120000", sta_no1="001", sta_no2="A", sta_no3="01", t1_status=1,
        )
    assert result.rows_affected == 0
    assert result.ora_code == 942
    assert "ORA-00942" in result.error_message
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/bin/pytest services/bridge/tests/test_oracle_client.py -v`

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `services/bridge/src/oracle_client.py`**

Create `services/bridge/src/oracle_client.py`:
```python
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Optional
import oracledb


_log = logging.getLogger("bridge.oracle")

ClientMode = Literal["thin", "thick"]


@dataclass
class MergeResult:
    rows_affected: int
    ora_code: Optional[int]
    error_message: str


def build_merge_statement(*, table_name: str) -> str:
    return f"""
MERGE INTO {table_name} t
USING (SELECT :1 AS MK_DATE, :2 AS STA_NO1, :3 AS STA_NO2, :4 AS STA_NO3, :5 AS T1_STATUS FROM dual) s
ON (t.MK_DATE = s.MK_DATE
    AND t.STA_NO1 = s.STA_NO1
    AND t.STA_NO2 = s.STA_NO2
    AND t.STA_NO3 = s.STA_NO3
    AND t.T1_STATUS = s.T1_STATUS)
WHEN NOT MATCHED THEN
  INSERT (MK_DATE, STA_NO1, STA_NO2, STA_NO3, T1_STATUS)
  VALUES (s.MK_DATE, s.STA_NO1, s.STA_NO2, s.STA_NO3, s.T1_STATUS)
""".strip()


def init_oracle_client_for_profiles(profiles: dict[str, Any], *, instant_client_dir: str) -> ClientMode:
    needs_thick = any(p["oracle"].get("client_mode") == "thick" for p in profiles.values())
    if not needs_thick:
        return "thin"
    if not Path(instant_client_dir).exists():
        raise RuntimeError(
            f"client_mode=thick requires Instant Client at {instant_client_dir}, but path is missing"
        )
    oracledb.init_oracle_client(lib_dir=instant_client_dir)
    return "thick"


def open_connection(profile_oracle: dict[str, Any]) -> oracledb.Connection:
    cfg = profile_oracle
    user = cfg["user"]
    password = cfg["password"]
    if cfg["auth_mode"] == "basic":
        dsn = oracledb.makedsn(cfg["host"], cfg["port"], service_name=cfg["service_name"])
        return oracledb.connect(user=user, password=password, dsn=dsn)
    if cfg["auth_mode"] == "wallet":
        kwargs: dict[str, Any] = dict(
            user=user,
            password=password,
            dsn=cfg["dsn"],
            config_dir=cfg["wallet_dir"],
            wallet_location=cfg["wallet_dir"],
        )
        if cfg.get("wallet_password"):
            kwargs["wallet_password"] = cfg["wallet_password"]
        return oracledb.connect(**kwargs)
    raise ValueError(f"unknown auth_mode: {cfg['auth_mode']}")


def execute_merge(
    conn: oracledb.Connection,
    *,
    table_name: str,
    mk_date: str,
    sta_no1: str,
    sta_no2: str,
    sta_no3: str,
    t1_status: int,
) -> MergeResult:
    sql = build_merge_statement(table_name=table_name)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (mk_date, sta_no1, sta_no2, sta_no3, t1_status))
            rows = cur.rowcount or 0
        conn.commit()
        return MergeResult(rows_affected=rows, ora_code=None, error_message="")
    except oracledb.DatabaseError as e:
        ora_code = None
        message = str(e)
        if e.args and hasattr(e.args[0], "code"):
            ora_code = int(e.args[0].code)
            message = str(getattr(e.args[0], "message", "") or message)
        return MergeResult(rows_affected=0, ora_code=ora_code, error_message=message)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest services/bridge/tests/test_oracle_client.py -v`

Expected: 9 passed.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check services/bridge/src/oracle_client.py services/bridge/tests/test_oracle_client.py
git add services/bridge/src/oracle_client.py services/bridge/tests/test_oracle_client.py
git commit -m "feat(bridge): add Oracle client (Thin/Thick × basic/wallet, MERGE)"
```

---

### Task 3.7: Bridge MQTT listener and ACK publisher

**Files:**
- Create: `services/bridge/src/mqtt_listener.py`
- Create: `services/bridge/tests/test_mqtt_listener.py`

The bridge subscribes to `presence/event` (QoS=2). Each received message is parsed, validated
(required keys present), and persisted to `inbox` (idempotent). After Oracle commit (handled
elsewhere), `publish_ack(event_id, mk_date_committed)` sends to `presence/event/ack`.

- [ ] **Step 1: Write failing tests**

Create `services/bridge/tests/test_mqtt_listener.py`:
```python
import json
from unittest.mock import patch, MagicMock
from services.bridge.src.mqtt_listener import BridgeMqttClient, parse_event_payload, EventPayload


def _msg(topic: str, payload: dict) -> MagicMock:
    m = MagicMock()
    m.topic = topic
    m.payload = json.dumps(payload).encode("utf-8")
    return m


def test_parse_event_payload_extracts_fields():
    p = {
        "event_id": "abc", "event": "ENTER", "event_time": "20260427120000",
        "event_time_iso": "2026-04-27T12:00:00+09:00",
        "monotonic_ns": 1, "wall_clock_synced": True,
        "device_id": "rpi-1", "score": 0.9, "schema_version": 1,
    }
    out = parse_event_payload(json.dumps(p).encode("utf-8"))
    assert isinstance(out, EventPayload)
    assert out.event_id == "abc"
    assert out.event_type == "ENTER"
    assert out.wall_clock_synced is True


def test_parse_event_payload_rejects_missing_required():
    p = {"event_id": "abc"}
    import pytest
    with pytest.raises(ValueError, match="event"):
        parse_event_payload(json.dumps(p).encode("utf-8"))


def test_parse_event_payload_rejects_invalid_json():
    import pytest
    with pytest.raises(ValueError):
        parse_event_payload(b"not json")


def test_subscribe_event_invokes_callback_with_parsed_payload():
    received = []
    with patch("services.bridge.src.mqtt_listener.paho.Client") as paho_cls:
        client = paho_cls.return_value
        c = BridgeMqttClient(client_id="bridge-test")
        c.connect_and_loop(host="m", port=1883)
        c.subscribe_event("presence/event", lambda payload, raw: received.append((payload, raw)))
        on_message = client.message_callback_add.call_args.args[1]
        good = {
            "event_id": "abc", "event": "ENTER", "event_time": "20260427120000",
            "event_time_iso": "2026-04-27T12:00:00+09:00",
            "monotonic_ns": 1, "wall_clock_synced": True, "device_id": "x",
            "score": 0.9, "schema_version": 1,
        }
        on_message(client, None, _msg("presence/event", good))
    assert len(received) == 1
    assert received[0][0].event_id == "abc"


def test_subscribe_event_logs_and_drops_malformed_messages():
    with patch("services.bridge.src.mqtt_listener.paho.Client") as paho_cls:
        client = paho_cls.return_value
        called = []
        c = BridgeMqttClient(client_id="bridge-test")
        c.connect_and_loop(host="m", port=1883)
        c.subscribe_event("presence/event", lambda *a: called.append(a))
        on_message = client.message_callback_add.call_args.args[1]
        bad_msg = MagicMock(); bad_msg.topic = "presence/event"; bad_msg.payload = b"not json"
        on_message(client, None, bad_msg)
    assert called == []  # malformed messages are dropped, not delivered to handler


def test_publish_ack_serializes_payload_with_qos2():
    with patch("services.bridge.src.mqtt_listener.paho.Client") as paho_cls:
        client = paho_cls.return_value
        c = BridgeMqttClient(client_id="bridge-test")
        c.connect_and_loop(host="m", port=1883)
        c.publish_ack("presence/event/ack", event_id="abc",
                      mk_date_committed="20260427120000",
                      committed_at_iso="2026-04-27T12:00:00.123+09:00")
        client.publish.assert_called_once()
        args, kwargs = client.publish.call_args
        body = json.loads(args[1])
        assert body == {
            "event_id": "abc", "mk_date_committed": "20260427120000",
            "committed_at_iso": "2026-04-27T12:00:00.123+09:00",
            "schema_version": 1,
        }
        assert kwargs.get("qos") == 2
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/bin/pytest services/bridge/tests/test_mqtt_listener.py -v`

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `services/bridge/src/mqtt_listener.py`**

Create `services/bridge/src/mqtt_listener.py`:
```python
import json
import logging
from dataclasses import dataclass
from typing import Callable, Optional
import paho.mqtt.client as paho


_log = logging.getLogger("bridge.mqtt")

REQUIRED_PAYLOAD_KEYS = (
    "event_id", "event", "monotonic_ns", "wall_clock_synced",
    "device_id", "schema_version",
)


@dataclass(frozen=True)
class EventPayload:
    event_id: str
    event_type: str
    mk_date: Optional[str]
    event_time_iso: Optional[str]
    monotonic_ns: int
    wall_clock_synced: bool
    device_id: str
    score: Optional[float]
    schema_version: int


def parse_event_payload(raw: bytes) -> EventPayload:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"invalid JSON payload: {e}") from e
    missing = [k for k in REQUIRED_PAYLOAD_KEYS if k not in data]
    if missing:
        raise ValueError(f"payload missing required keys: {missing}")
    return EventPayload(
        event_id=data["event_id"],
        event_type=data["event"],
        mk_date=data.get("event_time"),
        event_time_iso=data.get("event_time_iso"),
        monotonic_ns=int(data["monotonic_ns"]),
        wall_clock_synced=bool(data["wall_clock_synced"]),
        device_id=data["device_id"],
        score=data.get("score"),
        schema_version=int(data["schema_version"]),
    )


class BridgeMqttClient:
    def __init__(self, *, client_id: str):
        self._client_id = client_id
        self._client: Optional[paho.Client] = None

    def connect_and_loop(self, *, host: str, port: int, keepalive: int = 60) -> None:
        client = paho.Client(client_id=self._client_id, protocol=paho.MQTTv5)
        client.reconnect_delay_set(min_delay=1, max_delay=60)
        client.connect(host, port, keepalive=keepalive)
        client.loop_start()
        self._client = client

    def subscribe_event(self, topic: str, handler: Callable[[EventPayload, bytes], None]) -> None:
        if self._client is None:
            raise RuntimeError("mqtt client not connected")

        def _on_message(_client, _userdata, msg) -> None:
            try:
                payload = parse_event_payload(msg.payload)
            except ValueError as e:
                _log.warning(
                    "event_parse_failed",
                    extra={"event": "event_parse_failed", "error": {"type": type(e).__name__, "message": str(e)}},
                )
                return
            handler(payload, msg.payload)

        self._client.subscribe(topic, qos=2)
        self._client.message_callback_add(topic, _on_message)

    def publish_ack(self, topic: str, *, event_id: str, mk_date_committed: str,
                    committed_at_iso: str) -> None:
        if self._client is None:
            raise RuntimeError("mqtt client not connected")
        body = json.dumps({
            "event_id": event_id,
            "mk_date_committed": mk_date_committed,
            "committed_at_iso": committed_at_iso,
            "schema_version": 1,
        })
        self._client.publish(topic, body, qos=2)

    def disconnect(self) -> None:
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest services/bridge/tests/test_mqtt_listener.py -v`

Expected: 6 passed.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check services/bridge/src/mqtt_listener.py services/bridge/tests/test_mqtt_listener.py
git add services/bridge/src/mqtt_listener.py services/bridge/tests/test_mqtt_listener.py
git commit -m "feat(bridge): add MQTT subscribe/publish-ack with payload validation"
```

---

### Task 3.8: Sender (combine inbox + Oracle + circuit breaker + ACK)

**Files:**
- Create: `services/bridge/src/sender.py`
- Create: `services/bridge/tests/test_sender.py`

The Sender pulls one batch from `inbox` (status=`received` and due), resolves the current
profile, performs MERGE, publishes ACK, and updates statuses. Handles SNTP-not-synced (skip
until baseline available), unknown SSID (hold), and circuit breaker (skip).

- [ ] **Step 1: Write failing tests**

Create `services/bridge/tests/test_sender.py`:
```python
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock
from services.bridge.src.inbox import InboxRepository, InboxEvent
from services.bridge.src.profile_resolver import ProfileResolver
from services.bridge.src.circuit_breaker import CircuitBreaker
from services.bridge.src.time_watcher import SyncBaseline
from services.bridge.src.sender import Sender, SenderDeps
from services.bridge.src.oracle_client import MergeResult


def _make_event(event_id="e1", *, wall_synced=True, mk_date="20260427120000"):
    return InboxEvent(
        event_id=event_id,
        event_type="ENTER",
        mk_date=mk_date,
        monotonic_ns=1_000_000_000,
        wall_synced=wall_synced,
        device_id="rpi-test",
        score=0.9,
        raw_payload="{}",
        status="received",
        ssid_at_receive="factory_a_wifi",
        profile_at_send=None,
        mk_date_committed=None,
        received_at_iso="2026-04-27T12:00:00+00:00",
        sent_at_iso=None,
        retry_count=0,
        next_retry_at_iso=None,
        last_error=None,
    )


def _profiles():
    return {
        "factory_a_wifi": {
            "description": "A",
            "sntp": {"servers": ["ntp.a"]},
            "oracle": {
                "client_mode": "thin", "auth_mode": "basic",
                "host": "h", "port": 1521, "service_name": "S",
                "user": "u", "password": "p", "table_name": "HF1RCM01",
            },
        }
    }


def _build_sender(tmp_path: Path, *, network_ssid="factory_a_wifi", synced=True,
                   merge_result=None, baseline=None):
    inbox = InboxRepository(tmp_path / "i.db"); inbox.init()
    resolver = ProfileResolver(profiles=_profiles(), unknown_policy="hold")
    breaker = CircuitBreaker(half_open_after_seconds=900, permanent_codes={942})
    network = MagicMock(); network.get_current_ssid.return_value = network_ssid
    time_watcher = MagicMock(); time_watcher.is_synced = synced
    time_watcher.baseline = baseline if baseline else SyncBaseline(
        sync_wall=datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone(timedelta(hours=9))),
        sync_monotonic_ns=2_000_000_000,
    ) if synced else None
    oracle = MagicMock()
    oracle.execute_merge_for_profile.return_value = merge_result or MergeResult(
        rows_affected=1, ora_code=None, error_message="",
    )
    mqtt = MagicMock()
    deps = SenderDeps(
        inbox=inbox,
        resolver=resolver,
        breaker=breaker,
        network=network,
        time_watcher=time_watcher,
        oracle=oracle,
        mqtt=mqtt,
        device_cfg={"device_id": "rpi-test", "station": {"sta_no1": "001", "sta_no2": "A", "sta_no3": "01"}},
        topic_ack="presence/event/ack",
    )
    return Sender(deps=deps), deps


def test_sender_processes_event_and_publishes_ack(tmp_path: Path):
    sender, deps = _build_sender(tmp_path)
    deps.inbox.insert_received(_make_event("e1"))
    sender.run_once(now=datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc))
    deps.oracle.execute_merge_for_profile.assert_called_once()
    deps.mqtt.publish_ack.assert_called_once()
    assert deps.inbox.get("e1").status == "sent"


def test_sender_skips_when_no_ssid(tmp_path: Path):
    sender, deps = _build_sender(tmp_path, network_ssid=None)
    deps.inbox.insert_received(_make_event("e1"))
    sender.run_once(now=datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc))
    deps.oracle.execute_merge_for_profile.assert_not_called()
    deps.mqtt.publish_ack.assert_not_called()
    assert deps.inbox.get("e1").status == "received"


def test_sender_skips_when_sntp_not_synced(tmp_path: Path):
    sender, deps = _build_sender(tmp_path, synced=False, baseline=None)
    deps.inbox.insert_received(_make_event("e1", wall_synced=False, mk_date=None))
    sender.run_once(now=datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc))
    deps.oracle.execute_merge_for_profile.assert_not_called()
    assert deps.inbox.get("e1").status == "received"


def test_sender_corrects_mk_date_for_unsynced_event(tmp_path: Path):
    baseline = SyncBaseline(
        sync_wall=datetime(2026, 4, 27, 17, 23, 51, tzinfo=timezone(timedelta(hours=9))),
        sync_monotonic_ns=13_000_000_000,
    )
    sender, deps = _build_sender(tmp_path, baseline=baseline)
    deps.inbox.insert_received(_make_event("e1", wall_synced=False, mk_date=None))
    deps.inbox.get("e1")  # still received
    # Override the event's monotonic to 6_200_000_000 -> wall = 17:23:44.2 -> '20260427172344'
    e = deps.inbox.get("e1")
    e.monotonic_ns = 6_200_000_000
    deps.inbox.insert_received(e)  # re-insert is no-op (idempotent), so update directly
    with __import__("sqlite3").connect(deps.inbox.path) as c:
        c.execute("UPDATE inbox SET monotonic_ns=? WHERE event_id=?", (6_200_000_000, "e1"))
    sender.run_once(now=datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc))
    args, kwargs = deps.oracle.execute_merge_for_profile.call_args
    assert kwargs["mk_date"] == "20260427172344"


def test_sender_records_failure_and_schedules_retry(tmp_path: Path):
    sender, deps = _build_sender(
        tmp_path,
        merge_result=MergeResult(rows_affected=0, ora_code=12541, error_message="ORA-12541"),
    )
    deps.inbox.insert_received(_make_event("e1"))
    sender.run_once(now=datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc))
    row = deps.inbox.get("e1")
    assert row.status == "received"
    assert row.retry_count == 1
    assert row.last_error and "12541" in row.last_error


def test_sender_opens_circuit_on_permanent_error(tmp_path: Path):
    sender, deps = _build_sender(
        tmp_path,
        merge_result=MergeResult(rows_affected=0, ora_code=942, error_message="ORA-00942"),
    )
    deps.inbox.insert_received(_make_event("e1"))
    sender.run_once(now=datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc))
    assert deps.breaker.state_for("factory_a_wifi", now=datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)) == "open"


def test_sender_skips_when_circuit_open(tmp_path: Path):
    sender, deps = _build_sender(tmp_path)
    deps.breaker.record_failure("factory_a_wifi", ora_code=942,
                                 now=datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc))
    deps.inbox.insert_received(_make_event("e1"))
    sender.run_once(now=datetime(2026, 4, 27, 12, 0, 1, tzinfo=timezone.utc))
    deps.oracle.execute_merge_for_profile.assert_not_called()
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/bin/pytest services/bridge/tests/test_sender.py -v`

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `services/bridge/src/sender.py`**

Create `services/bridge/src/sender.py`:
```python
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from services.bridge.src.circuit_breaker import CircuitBreaker
from services.bridge.src.inbox import InboxEvent, InboxRepository
from services.bridge.src.network_watcher import NetworkWatcher
from services.bridge.src.oracle_client import MergeResult
from services.bridge.src.profile_resolver import ProfileResolver
from services.bridge.src.time_correction import correct_event_wall, format_mk_date_jst
from services.bridge.src.time_watcher import TimeWatcher

# next_retry_at logic mirrors the detector retry module to keep the bridge self-contained.
from services.bridge.src.retry import BackoffPolicy, next_retry_at


_log = logging.getLogger("bridge.sender")


class _OracleProto:
    def execute_merge_for_profile(self, *, profile: dict, mk_date: str, sta_no1: str,
                                  sta_no2: str, sta_no3: str, t1_status: int) -> MergeResult: ...


class _MqttProto:
    def publish_ack(self, topic: str, *, event_id: str, mk_date_committed: str,
                    committed_at_iso: str) -> None: ...


@dataclass
class SenderDeps:
    inbox: InboxRepository
    resolver: ProfileResolver
    breaker: CircuitBreaker
    network: NetworkWatcher
    time_watcher: TimeWatcher
    oracle: _OracleProto
    mqtt: _MqttProto
    device_cfg: dict[str, Any]
    topic_ack: str
    backoff_policy: BackoffPolicy = BackoffPolicy(initial=5.0, multiplier=3.0, cap=600.0)


class Sender:
    def __init__(self, *, deps: SenderDeps):
        self._d = deps

    def run_once(self, *, now: datetime) -> None:
        ssid = self._d.network.get_current_ssid()
        decision = self._d.resolver.resolve(ssid)
        if decision.action != "send":
            return
        profile_name = decision.profile_name
        if profile_name is None:
            return
        if self._d.breaker.state_for(profile_name, now=now) == "open":
            return

        profile = self._d.resolver.get(profile_name)
        for event in self._d.inbox.iter_received_due(now_iso=now.isoformat()):
            mk_date = self._resolve_mk_date(event)
            if mk_date is None:
                # SNTP not synced and event has no wall clock — defer.
                continue
            self._send_one(event=event, profile=profile, profile_name=profile_name,
                           mk_date=mk_date, now=now)

    def _resolve_mk_date(self, event: InboxEvent) -> str | None:
        if event.wall_synced and event.mk_date:
            return event.mk_date
        # Need to backfill from monotonic baseline.
        baseline = self._d.time_watcher.baseline
        if baseline is None:
            return None
        wall = correct_event_wall(
            sync_wall=baseline.sync_wall,
            sync_monotonic_ns=baseline.sync_monotonic_ns,
            event_monotonic_ns=event.monotonic_ns,
        )
        return format_mk_date_jst(wall)

    def _send_one(self, *, event: InboxEvent, profile: dict, profile_name: str,
                   mk_date: str, now: datetime) -> None:
        sta = self._d.device_cfg["station"]
        t1_status = 1 if event.event_type == "ENTER" else 2
        result = self._d.oracle.execute_merge_for_profile(
            profile=profile,
            mk_date=mk_date,
            sta_no1=sta["sta_no1"],
            sta_no2=sta["sta_no2"],
            sta_no3=sta["sta_no3"],
            t1_status=t1_status,
        )
        if result.ora_code is None:
            self._d.inbox.mark_sent(
                event.event_id,
                mk_date_committed=mk_date,
                profile_at_send=profile_name,
                sent_at_iso=now.isoformat(),
            )
            self._d.breaker.record_success(profile_name, now=now)
            self._d.mqtt.publish_ack(
                self._d.topic_ack,
                event_id=event.event_id,
                mk_date_committed=mk_date,
                committed_at_iso=now.isoformat(timespec="milliseconds"),
            )
            _log.info("merge_committed", extra={
                "event": "merge_committed",
                "event_id": event.event_id,
                "mk_date": mk_date,
                "rows_affected": result.rows_affected,
                "profile": profile_name,
            })
        else:
            self._d.breaker.record_failure(profile_name, ora_code=result.ora_code, now=now)
            attempt = event.retry_count + 1
            self._d.inbox.update_retry(
                event.event_id,
                retry_count=attempt,
                next_retry_at_iso=next_retry_at(now, attempt=attempt, policy=self._d.backoff_policy).isoformat(),
                last_error=f"ORA-{result.ora_code}: {result.error_message}",
            )
            _log.error("merge_failed", extra={
                "event": "merge_failed",
                "event_id": event.event_id,
                "ora_code": result.ora_code,
                "retry_count": attempt,
            })
```

- [ ] **Step 4: Add bridge `retry.py` (mirror of detector retry)**

Create `services/bridge/src/retry.py`:
```python
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
```

- [ ] **Step 5: Run all sender tests**

Run: `.venv/bin/pytest services/bridge/tests/test_sender.py -v`

Expected: 7 passed.

- [ ] **Step 6: Lint and commit**

```bash
.venv/bin/ruff check services/bridge/src/sender.py services/bridge/src/retry.py services/bridge/tests/test_sender.py
git add services/bridge/src/sender.py services/bridge/src/retry.py services/bridge/tests/test_sender.py
git commit -m "feat(bridge): add Sender that combines inbox, Oracle, breaker, and ACK"
```

---

### Task 3.9: Bridge main integration

**Files:**
- Create: `services/bridge/src/main.py`

Wires all components. Loads configs, initializes Oracle client mode, sets up listener,
launches background threads (network watcher, time watcher, sender, stats, healthcheck).
This module has no unit tests of its own — it is integration-tested in Phase 4.

- [ ] **Step 1: Implement `services/bridge/src/main.py`**

Create `services/bridge/src/main.py`:
```python
from __future__ import annotations
import logging
import os
import signal
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.bridge.src import config as cfg_mod
from services.bridge.src.circuit_breaker import CircuitBreaker
from services.bridge.src.inbox import InboxEvent, InboxRepository
from services.bridge.src.logging_setup import setup_logging
from services.bridge.src.mqtt_listener import BridgeMqttClient, EventPayload
from services.bridge.src.network_watcher import NetworkWatcher
from services.bridge.src.oracle_client import (
    MergeResult, execute_merge, init_oracle_client_for_profiles, open_connection,
)
from services.bridge.src.profile_resolver import ProfileResolver
from services.bridge.src.retry import BackoffPolicy
from services.bridge.src.sender import Sender, SenderDeps
from services.bridge.src.time_watcher import TimeWatcher


_log = logging.getLogger("bridge.main")
HEALTH_FILE = "/tmp/bridge.healthy"        # noqa: S108
DEFAULT_BRIDGE_YAML = "/etc/presence-logger/bridge.yaml"
DEFAULT_DEVICE_YAML = "/etc/presence-logger/device.yaml"
DEFAULT_PROFILES_YAML = "/etc/presence-logger/profiles.yaml"


class _OracleAdapter:
    """Adapts open_connection + execute_merge into the SenderDeps oracle protocol."""

    def __init__(self):
        self._cache: dict[str, Any] = {}

    def execute_merge_for_profile(self, *, profile: dict, mk_date: str, sta_no1: str,
                                   sta_no2: str, sta_no3: str, t1_status: int) -> MergeResult:
        oracle_cfg = profile["oracle"]
        # Connection per call is acceptable for INSERT-only workloads at 1-2 events/min.
        conn = open_connection(oracle_cfg)
        try:
            return execute_merge(
                conn,
                table_name=oracle_cfg["table_name"],
                mk_date=mk_date,
                sta_no1=sta_no1, sta_no2=sta_no2, sta_no3=sta_no3,
                t1_status=t1_status,
            )
        finally:
            try:
                conn.close()
            except Exception:    # noqa: BLE001
                pass


def main() -> int:    # pragma: no cover
    bridge_cfg = cfg_mod.load_bridge_config(Path(os.environ.get("BRIDGE_YAML", DEFAULT_BRIDGE_YAML)))
    device_cfg = cfg_mod.load_device_config(Path(os.environ.get("DEVICE_YAML", DEFAULT_DEVICE_YAML)))
    profiles_cfg = cfg_mod.load_profiles_config(Path(os.environ.get("PROFILES_YAML", DEFAULT_PROFILES_YAML)))

    setup_logging(
        process="bridge",
        device_id=device_cfg["device_id"],
        log_dir="/var/log/presence-logger",
        level=bridge_cfg["logging"]["level"],
    )
    _log.info("startup", extra={"event": "startup"})

    init_oracle_client_for_profiles(
        profiles_cfg["profiles"],
        instant_client_dir=bridge_cfg["oracle"]["instant_client_dir"],
    )

    inbox = InboxRepository(bridge_cfg["buffer"]["path"]); inbox.init()
    resolver = ProfileResolver(
        profiles=profiles_cfg["profiles"],
        unknown_policy=profiles_cfg["unknown_ssid_policy"],
    )
    breaker = CircuitBreaker(
        half_open_after_seconds=bridge_cfg["circuit_breaker"]["half_open_after_seconds"],
        permanent_codes=set(bridge_cfg["circuit_breaker"]["permanent_ora_codes"]),
    )
    network = NetworkWatcher(command=bridge_cfg["network_watcher"]["ssid_command"])
    time_watcher = TimeWatcher(command=bridge_cfg["time_watcher"]["sync_command"])
    oracle_adapter = _OracleAdapter()

    mqtt = BridgeMqttClient(client_id=bridge_cfg["mqtt"]["client_id"])
    mqtt.connect_and_loop(
        host=os.environ.get("MQTT_HOST", bridge_cfg["mqtt"]["host"]),
        port=bridge_cfg["mqtt"]["port"],
    )

    def _on_event(payload: EventPayload, raw: bytes) -> None:
        event = InboxEvent(
            event_id=payload.event_id,
            event_type=payload.event_type,
            mk_date=payload.mk_date,
            monotonic_ns=payload.monotonic_ns,
            wall_synced=payload.wall_clock_synced,
            device_id=payload.device_id,
            score=payload.score,
            raw_payload=raw.decode("utf-8", errors="replace"),
            status="received",
            ssid_at_receive=network.cached_ssid,
            profile_at_send=None,
            mk_date_committed=None,
            received_at_iso=datetime.now(timezone.utc).isoformat(),
            sent_at_iso=None,
            retry_count=0,
            next_retry_at_iso=None,
            last_error=None,
        )
        inbox.insert_received(event)
        _log.info("received", extra={"event": "received", "event_id": payload.event_id})

    mqtt.subscribe_event(bridge_cfg["mqtt"]["topic_event"], _on_event)

    sender = Sender(deps=SenderDeps(
        inbox=inbox,
        resolver=resolver,
        breaker=breaker,
        network=network,
        time_watcher=time_watcher,
        oracle=oracle_adapter,
        mqtt=mqtt,
        device_cfg=device_cfg,
        topic_ack=bridge_cfg["mqtt"]["topic_ack"],
        backoff_policy=BackoffPolicy(
            initial=bridge_cfg["retry"]["initial_delay_seconds"],
            multiplier=bridge_cfg["retry"]["multiplier"],
            cap=bridge_cfg["retry"]["max_delay_seconds"],
        ),
    ))

    running = True

    def _stop(*_a):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    last_health = 0.0
    last_stats = 0.0
    last_network = 0.0
    last_time = 0.0

    while running:
        now = time.monotonic()

        if now - last_network >= bridge_cfg["network_watcher"]["poll_interval_seconds"]:
            network.get_current_ssid()
            last_network = now
        if now - last_time >= bridge_cfg["time_watcher"]["poll_interval_seconds"]:
            time_watcher.poll()
            last_time = now

        sender.run_once(now=datetime.now(timezone.utc))

        if now - last_health >= 5.0:
            Path(HEALTH_FILE).touch()
            last_health = now
        if now - last_stats >= bridge_cfg["logging"]["buffer_stats_interval_seconds"]:
            _log.info("periodic", extra={
                "event": "periodic",
                "current_ssid": network.cached_ssid,
                "ntp_synced": time_watcher.is_synced,
                "inbox_count": inbox.count(),
            })
            last_stats = now

        time.sleep(1.0)

    mqtt.disconnect()
    return 0


if __name__ == "__main__":     # pragma: no cover
    raise SystemExit(main())
```

- [ ] **Step 2: Smoke import test**

Run: `.venv/bin/python -c "from services.bridge.src import main; print('ok')"`

Expected: `ok` (proves all imports resolve).

- [ ] **Step 3: Run all bridge tests**

Run: `.venv/bin/pytest services/bridge/ -v`

Expected: All previously written bridge tests pass.

- [ ] **Step 4: Lint and commit**

```bash
.venv/bin/ruff check services/bridge/src/main.py
git add services/bridge/src/main.py
git commit -m "feat(bridge): wire main loop (mqtt listener, sender, watchers)"
```

---

### Task 3.10: Bridge Dockerfile

**Files:**
- Create: `services/bridge/Dockerfile`
- Create: `services/bridge/.dockerignore`

The bridge needs `nmcli` (from `network-manager`), Oracle Instant Client Basic Light (for
optional Thick mode), and a small set of Python libs. Instant Client URL is passed in via
build arg so the spec stays version-independent.

- [ ] **Step 1: Write `services/bridge/.dockerignore`**

Create `services/bridge/.dockerignore`:
```
__pycache__
*.pyc
tests/
.pytest_cache
.ruff_cache
```

- [ ] **Step 2: Write `services/bridge/Dockerfile`**

Create `services/bridge/Dockerfile`:
```dockerfile
FROM python:3.11-slim-bookworm

# nmcli (NetworkManager client) and Instant Client deps.
RUN apt-get update && apt-get install -y --no-install-recommends \
        network-manager libaio1 ca-certificates wget unzip \
    && rm -rf /var/lib/apt/lists/*

# Optional: install Oracle Instant Client Basic Light (Linux ARM64).
# Caller passes INSTANT_CLIENT_URL at build time. If unset, Thin mode only.
ARG INSTANT_CLIENT_URL=""
RUN if [ -n "$INSTANT_CLIENT_URL" ]; then \
        mkdir -p /opt/oracle && \
        wget -q -O /tmp/ic.zip "$INSTANT_CLIENT_URL" && \
        unzip -q /tmp/ic.zip -d /opt/oracle && \
        mv /opt/oracle/instantclient_* /opt/oracle/instantclient && \
        rm /tmp/ic.zip ; \
    fi

ENV LD_LIBRARY_PATH=/opt/oracle/instantclient

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./services/bridge/src/

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

CMD ["python", "-m", "services.bridge.src.main"]
```

- [ ] **Step 3: Verify image builds (Thin only)**

Run:
```bash
docker build -t presence-bridge:test services/bridge/
```

Expected: `Successfully tagged presence-bridge:test`.

- [ ] **Step 4: Verify image builds with Thick (optional smoke test)**

If the engineer has Internet access and wants Thick mode, supply the build arg:
```bash
docker build \
  --build-arg INSTANT_CLIENT_URL="https://download.oracle.com/.../instantclient-basiclite-linux.arm64-21.13.0.0.0dbru.zip" \
  -t presence-bridge:thick services/bridge/
```

Expected: succeeds; `LD_LIBRARY_PATH` and `/opt/oracle/instantclient` populated inside the image.

- [ ] **Step 5: Commit**

```bash
git add services/bridge/Dockerfile services/bridge/.dockerignore
git commit -m "build(bridge): add Dockerfile with optional Instant Client install"
```

---

## Phase 3 complete

Bridge service is now feature-complete:
- Persistent SQLite inbox with idempotent insert and ring eviction
- WiFi SSID watcher (DBus/nmcli) with caching fallback
- SNTP sync watcher with monotonic baseline capture
- Time correction utilities (Asia/Tokyo)
- SSID → profile resolver with `hold/use_last/drop` policy and log redaction
- Per-profile circuit breaker for permanent ORA errors
- Oracle client supporting Thin/Thick × basic/wallet (lazy init at process start)
- MQTT listener (event subscribe with parsing) and ACK publisher
- Sender that ties it all together, including SNTP-deferred and circuit-skipped paths
- `main.py` wiring everything plus background polling, healthcheck, periodic stats
- Dockerfile with optional Instant Client install via build arg

Run `.venv/bin/pytest services/bridge/ -v` to confirm all bridge unit tests pass.

`git log --oneline | head -15` should show eleven new commits since Phase 2.

---

## Phase 4: Integration & Deployment

### Task 4.1: mosquitto configuration

**Files:**
- Create: `docker/mosquitto/mosquitto.conf`

The broker is local-only; no auth, no persistence, listener bound to all interfaces inside
the `presence-net` Docker network only (no host port publish in `docker-compose.yml`).

- [ ] **Step 1: Write `docker/mosquitto/mosquitto.conf`**

Create `docker/mosquitto/mosquitto.conf`:
```
listener 1883 0.0.0.0
allow_anonymous true
persistence false
log_dest stdout
log_type error
log_type warning
log_type notice
log_type information
connection_messages true
log_timestamp true
```

- [ ] **Step 2: Commit**

```bash
git add docker/mosquitto/mosquitto.conf
git commit -m "build(mosquitto): add internal-only broker config"
```

---

### Task 4.2: docker-compose.yml

**Files:**
- Create: `docker-compose.yml`
- Create: `.env.example`

- [ ] **Step 1: Write `docker-compose.yml`**

Create `docker-compose.yml`:
```yaml
version: "3.8"

services:
  mosquitto:
    image: eclipse-mosquitto:2
    container_name: presence-mosquitto
    restart: unless-stopped
    networks: [presence-net]
    volumes:
      - ./docker/mosquitto/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro

  detector:
    build: ./services/detector
    container_name: presence-detector
    restart: unless-stopped
    depends_on: [mosquitto]
    networks: [presence-net]
    devices:
      - "/dev/video0:/dev/video0"
    volumes:
      - /etc/presence-logger/device.yaml:/etc/presence-logger/device.yaml:ro
      - /etc/presence-logger/detector.yaml:/etc/presence-logger/detector.yaml:ro
      - /etc/hostname:/etc/host_hostname:ro
      - /var/lib/presence-logger:/var/lib/presence-logger
      - /var/log/presence-logger:/var/log/presence-logger
    environment:
      MQTT_HOST: mosquitto
      LOG_LEVEL: INFO
      TZ: Asia/Tokyo
    healthcheck:
      test: ["CMD", "test", "-f", "/tmp/detector.healthy"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 30s

  bridge:
    build:
      context: ./services/bridge
      args:
        INSTANT_CLIENT_URL: "${INSTANT_CLIENT_URL:-}"
    container_name: presence-bridge
    restart: unless-stopped
    depends_on: [mosquitto]
    networks: [presence-net]
    volumes:
      - /etc/presence-logger/device.yaml:/etc/presence-logger/device.yaml:ro
      - /etc/presence-logger/profiles.yaml:/etc/presence-logger/profiles.yaml:ro
      - /etc/presence-logger/bridge.yaml:/etc/presence-logger/bridge.yaml:ro
      - /etc/presence-logger/wallets:/etc/presence-logger/wallets:ro
      - /etc/hostname:/etc/host_hostname:ro
      - /var/lib/presence-logger:/var/lib/presence-logger
      - /var/log/presence-logger:/var/log/presence-logger
      - /run/dbus:/run/dbus:ro
      - /var/run/NetworkManager:/var/run/NetworkManager:ro
      - /etc/timezone:/etc/timezone:ro
      - /etc/localtime:/etc/localtime:ro
    env_file: [/etc/presence-logger/secrets.env]
    environment:
      MQTT_HOST: mosquitto
      LOG_LEVEL: INFO
      TZ: Asia/Tokyo
    healthcheck:
      test: ["CMD", "test", "-f", "/tmp/bridge.healthy"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 30s

networks:
  presence-net:
    driver: bridge
```

- [ ] **Step 2: Write `.env.example`**

Create `.env.example`:
```bash
# Build-time only: set this to enable Thick mode in the bridge image.
# Leave blank to build a Thin-only bridge.
INSTANT_CLIENT_URL=
```

- [ ] **Step 3: Validate compose syntax**

Run: `docker compose config`

Expected: parsed YAML output, no errors. (If your shell warns about missing
`/etc/presence-logger/*.yaml`, that's normal — those files are placed by `install.sh` later.)

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml .env.example
git commit -m "build: add docker-compose.yml with three-service stack"
```

---

### Task 4.3: Example config files

**Files:**
- Create: `config/device.yaml.example`
- Create: `config/detector.yaml.example`
- Create: `config/bridge.yaml.example`
- Create: `config/profiles.yaml.example`
- Create: `config/secrets.env.example`

- [ ] **Step 1: Write `config/device.yaml.example`**

Create `config/device.yaml.example`:
```yaml
device_id: null              # null=hostname auto-detected from /etc/host_hostname
station:
  sta_no1: "001"
  sta_no2: "A"
  sta_no3: "01"
```

- [ ] **Step 2: Write `config/detector.yaml.example`**

Create `config/detector.yaml.example`:
```yaml
camera:
  device: "/dev/video0"
  width: 640
  height: 480
  warmup_frames: 5

inference:
  model_path: "/opt/models/efficientdet_lite0.tflite"
  target_fps: 1.5
  score_threshold: 0.5
  category: "person"

debounce:
  enter_seconds: 3.0
  exit_seconds: 3.0

mqtt:
  host: "mosquitto"
  port: 1883
  qos: 2
  topic_event: "presence/event"
  topic_ack: "presence/event/ack"
  client_id_prefix: "presence-detector"

retry:
  initial_delay_seconds: 5
  max_delay_seconds: 600
  multiplier: 3

buffer:
  path: "/var/lib/presence-logger/detector_buf.db"
  max_rows: 100000
```

- [ ] **Step 3: Write `config/bridge.yaml.example`**

Create `config/bridge.yaml.example`:
```yaml
mqtt:
  host: "mosquitto"
  port: 1883
  qos: 2
  topic_event: "presence/event"
  topic_ack: "presence/event/ack"
  client_id: "presence-bridge"

oracle:
  connect_timeout_seconds: 10
  query_timeout_seconds: 30
  pool_min: 1
  pool_max: 2
  instant_client_dir: "/opt/oracle/instantclient"

network_watcher:
  poll_interval_seconds: 5
  ssid_command: "nmcli -t -f ACTIVE,SSID dev wifi"

time_watcher:
  poll_interval_seconds: 10
  sync_command: "timedatectl show -p NTPSynchronized --value"

retry:
  initial_delay_seconds: 5
  max_delay_seconds: 600
  multiplier: 3

circuit_breaker:
  permanent_ora_codes: [942, 904, 1017, 1031, 12514]
  half_open_after_seconds: 900

buffer:
  path: "/var/lib/presence-logger/bridge_buf.db"
  max_rows: 100000

logging:
  level: "INFO"
  buffer_stats_interval_seconds: 60
```

- [ ] **Step 4: Write `config/profiles.yaml.example`**

Create `config/profiles.yaml.example`:
```yaml
profiles:
  factory_a_wifi:
    description: "Factory A — line 1 (on-prem Oracle, Thin)"
    sntp:
      servers: ["ntp.factory-a.local", "ntp.nict.jp"]
    oracle:
      client_mode: "thin"
      auth_mode: "basic"
      host: "10.10.1.50"
      port: 1521
      service_name: "PRDDB"
      user: "presence_user"
      password: "${ORACLE_PASSWORD_A}"
      table_name: "HF1RCM01"

  factory_b_wifi:
    description: "Factory B — Autonomous DB with wallet (Thin)"
    sntp:
      servers: ["ntp.factory-b.local"]
    oracle:
      client_mode: "thin"
      auth_mode: "wallet"
      dsn: "myadb_high"
      user: "presence_user"
      password: "${ORACLE_PASSWORD_B}"
      wallet_dir: "/etc/presence-logger/wallets/factory_b"
      wallet_password: "${WALLET_PASSWORD_B}"
      table_name: "HF1RCM01"

  factory_legacy_wifi:
    description: "Factory D — legacy Oracle requiring Thick mode"
    sntp:
      servers: ["ntp.factory-d.local"]
    oracle:
      client_mode: "thick"
      auth_mode: "basic"
      host: "10.40.1.50"
      port: 1521
      service_name: "LEGACYDB"
      user: "presence_user"
      password: "${ORACLE_PASSWORD_D}"
      table_name: "HF1RCM01"

unknown_ssid_policy: "hold"
```

- [ ] **Step 5: Write `config/secrets.env.example`**

Create `config/secrets.env.example`:
```bash
# Loaded by docker-compose env_file. Real secrets.env must be chmod 600 root.
ORACLE_PASSWORD_A=replace-me
ORACLE_PASSWORD_B=replace-me
WALLET_PASSWORD_B=replace-me
ORACLE_PASSWORD_D=replace-me
```

- [ ] **Step 6: Commit**

```bash
git add config/device.yaml.example config/detector.yaml.example config/bridge.yaml.example config/profiles.yaml.example config/secrets.env.example
git commit -m "docs: add example config files"
```

---

### Task 4.4: install.sh script

**Files:**
- Create: `scripts/install.sh`
- Create: `scripts/tail-logs.sh`

`install.sh` is idempotent: creates `/etc/presence-logger/`, `/var/lib/presence-logger/`,
`/var/log/presence-logger/`, copies example YAMLs only if not present, sets permissions on
`secrets.env`, and writes the consolidated SNTP server list to
`/etc/systemd/timesyncd.conf`.

- [ ] **Step 1: Write `scripts/install.sh`**

Create `scripts/install.sh`:
```bash
#!/usr/bin/env bash
# Idempotent installer for presence-logger. Run as root.
# - Creates required directories
# - Copies example configs if real configs are missing
# - Configures systemd-timesyncd with the consolidated SNTP server list
# - Sets strict permissions on secrets.env

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "must be run as root" >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ETC_DIR="/etc/presence-logger"
LIB_DIR="/var/lib/presence-logger"
LOG_DIR="/var/log/presence-logger"
WALLETS_DIR="$ETC_DIR/wallets"

mkdir -p "$ETC_DIR" "$LIB_DIR" "$LOG_DIR" "$WALLETS_DIR"
chmod 0755 "$ETC_DIR" "$LIB_DIR" "$LOG_DIR"
chmod 0700 "$WALLETS_DIR"

# Copy example configs only if no real config exists yet.
copy_if_missing() {
    local src="$1"
    local dst="$2"
    local mode="$3"
    if [[ ! -f "$dst" ]]; then
        cp "$src" "$dst"
        chmod "$mode" "$dst"
        chown root:root "$dst"
        echo "installed: $dst"
    else
        echo "exists, skipped: $dst"
    fi
}

copy_if_missing "$REPO_DIR/config/device.yaml.example"     "$ETC_DIR/device.yaml"     0644
copy_if_missing "$REPO_DIR/config/detector.yaml.example"   "$ETC_DIR/detector.yaml"   0644
copy_if_missing "$REPO_DIR/config/bridge.yaml.example"     "$ETC_DIR/bridge.yaml"     0644
copy_if_missing "$REPO_DIR/config/profiles.yaml.example"   "$ETC_DIR/profiles.yaml"   0640
copy_if_missing "$REPO_DIR/config/secrets.env.example"     "$ETC_DIR/secrets.env"     0600

# Build a consolidated SNTP NTP= line from profiles.yaml.
# We use python (already required by the host for installation context); keep this dependency-free.
SNTP_SERVERS=$(python3 - <<'PY' "$ETC_DIR/profiles.yaml"
import sys, yaml
data = yaml.safe_load(open(sys.argv[1]))
seen = []
for p in (data.get("profiles") or {}).values():
    for s in (p.get("sntp", {}).get("servers") or []):
        if s not in seen:
            seen.append(s)
print(" ".join(seen))
PY
)

if [[ -n "$SNTP_SERVERS" ]]; then
    cat >/etc/systemd/timesyncd.conf <<EOF
[Time]
NTP=$SNTP_SERVERS
FallbackNTP=ntp.nict.jp time.cloudflare.com
EOF
    systemctl restart systemd-timesyncd
    echo "configured timesyncd with: $SNTP_SERVERS"
else
    echo "no SNTP servers found in profiles.yaml; timesyncd left unchanged"
fi

# Install the systemd unit if present.
if [[ -f "$REPO_DIR/systemd/presence-logger.service" ]]; then
    cp "$REPO_DIR/systemd/presence-logger.service" /etc/systemd/system/presence-logger.service
    systemctl daemon-reload
    echo "installed systemd unit: /etc/systemd/system/presence-logger.service"
fi

echo
echo "Install complete. Edit configs in $ETC_DIR, then:"
echo "  systemctl enable --now presence-logger.service"
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x scripts/install.sh`

- [ ] **Step 3: Write `scripts/tail-logs.sh`**

Create `scripts/tail-logs.sh`:
```bash
#!/usr/bin/env bash
# Tail all presence-logger JSON logs and pretty-print key fields with jq.
set -euo pipefail
exec tail -F /var/log/presence-logger/*.log \
  | jq -c '{ts, level, logger, event, event_id, message}'
```

Run: `chmod +x scripts/tail-logs.sh`

- [ ] **Step 4: Commit**

```bash
git add scripts/install.sh scripts/tail-logs.sh
git commit -m "build: add install.sh and tail-logs.sh helpers"
```

---

### Task 4.5: systemd unit

**Files:**
- Create: `systemd/presence-logger.service`

- [ ] **Step 1: Write the unit file**

Create `systemd/presence-logger.service`:
```ini
[Unit]
Description=Presence Logger (USB camera -> Oracle via MQTT)
Requires=docker.service network-online.target NetworkManager.service
After=docker.service network-online.target NetworkManager.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/presence-logger
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
ExecReload=/usr/bin/docker compose restart
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Commit**

```bash
git add systemd/presence-logger.service
git commit -m "build: add systemd unit for docker-compose lifecycle"
```

---

### Task 4.6: Integration test harness (mock Oracle)

**Files:**
- Create: `tests/integration/test_end_to_end.py`
- Create: `tests/integration/fakes.py`

We test the full bridge pipeline without a real Oracle by replacing `_OracleAdapter` with a
collecting fake. Detector → mosquitto is replaced by directly synthesizing `EventPayload`
objects and calling the bridge's `_on_event` handler. This exercises Sender + circuit breaker
+ inbox + ACK publisher without spinning up containers.

- [ ] **Step 1: Write `tests/integration/fakes.py`**

Create `tests/integration/fakes.py`:
```python
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any
from services.bridge.src.oracle_client import MergeResult


@dataclass
class FakeOracle:
    canned: list[MergeResult] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def execute_merge_for_profile(self, **kwargs):
        self.calls.append(kwargs)
        return self.canned.pop(0) if self.canned else MergeResult(rows_affected=1, ora_code=None, error_message="")


@dataclass
class FakeMqtt:
    acks: list[dict[str, Any]] = field(default_factory=list)

    def publish_ack(self, topic: str, *, event_id: str, mk_date_committed: str,
                    committed_at_iso: str) -> None:
        self.acks.append({
            "topic": topic, "event_id": event_id,
            "mk_date_committed": mk_date_committed, "committed_at_iso": committed_at_iso,
        })


@dataclass
class FakeNetwork:
    ssid: str | None = "factory_a_wifi"
    cached_ssid: str | None = None
    def __post_init__(self): self.cached_ssid = self.ssid
    def get_current_ssid(self) -> str | None:
        return self.ssid


@dataclass
class FakeTimeWatcher:
    is_synced: bool = True
    baseline: Any = None

    def __post_init__(self):
        if self.is_synced and self.baseline is None:
            from services.bridge.src.time_watcher import SyncBaseline
            self.baseline = SyncBaseline(
                sync_wall=datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone(timedelta(hours=9))),
                sync_monotonic_ns=2_000_000_000,
            )

    def poll(self) -> None:
        pass
```

- [ ] **Step 2: Write `tests/integration/test_end_to_end.py`**

Create `tests/integration/test_end_to_end.py`:
```python
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from services.bridge.src.inbox import InboxRepository, InboxEvent
from services.bridge.src.profile_resolver import ProfileResolver
from services.bridge.src.circuit_breaker import CircuitBreaker
from services.bridge.src.sender import Sender, SenderDeps
from services.bridge.src.oracle_client import MergeResult
from tests.integration.fakes import FakeOracle, FakeMqtt, FakeNetwork, FakeTimeWatcher


def _profiles():
    return {
        "factory_a_wifi": {
            "description": "A",
            "sntp": {"servers": ["ntp.a"]},
            "oracle": {
                "client_mode": "thin", "auth_mode": "basic",
                "host": "h", "port": 1521, "service_name": "S",
                "user": "u", "password": "p", "table_name": "HF1RCM01",
            },
        }
    }


def _ingest(inbox: InboxRepository, payload: dict, *, ssid: str = "factory_a_wifi") -> None:
    e = InboxEvent(
        event_id=payload["event_id"],
        event_type=payload["event"],
        mk_date=payload.get("event_time"),
        monotonic_ns=int(payload["monotonic_ns"]),
        wall_synced=bool(payload["wall_clock_synced"]),
        device_id=payload["device_id"],
        score=payload.get("score"),
        raw_payload=json.dumps(payload),
        status="received",
        ssid_at_receive=ssid,
        profile_at_send=None,
        mk_date_committed=None,
        received_at_iso=datetime.now(timezone.utc).isoformat(),
        sent_at_iso=None,
        retry_count=0,
        next_retry_at_iso=None,
        last_error=None,
    )
    inbox.insert_received(e)


def _make_sender(tmp_path: Path, *, oracle: FakeOracle, mqtt: FakeMqtt,
                  network: FakeNetwork, time_watcher: FakeTimeWatcher) -> tuple[Sender, InboxRepository]:
    inbox = InboxRepository(tmp_path / "inbox.db"); inbox.init()
    resolver = ProfileResolver(profiles=_profiles(), unknown_policy="hold")
    breaker = CircuitBreaker(half_open_after_seconds=900, permanent_codes={942})
    sender = Sender(deps=SenderDeps(
        inbox=inbox, resolver=resolver, breaker=breaker,
        network=network, time_watcher=time_watcher,
        oracle=oracle, mqtt=mqtt,
        device_cfg={"device_id": "rpi", "station": {"sta_no1": "001", "sta_no2": "A", "sta_no3": "01"}},
        topic_ack="presence/event/ack",
    ))
    return sender, inbox


def test_e2e_normal_enter_then_exit_writes_two_rows(tmp_path: Path):
    oracle = FakeOracle()
    mqtt = FakeMqtt()
    sender, inbox = _make_sender(tmp_path, oracle=oracle, mqtt=mqtt,
                                  network=FakeNetwork(), time_watcher=FakeTimeWatcher())
    _ingest(inbox, {
        "event_id": "e1", "event": "ENTER", "event_time": "20260427120000",
        "event_time_iso": "2026-04-27T12:00:00+09:00",
        "monotonic_ns": 1, "wall_clock_synced": True,
        "device_id": "rpi", "score": 0.9, "schema_version": 1,
    })
    _ingest(inbox, {
        "event_id": "e2", "event": "EXIT", "event_time": "20260427120010",
        "event_time_iso": "2026-04-27T12:00:10+09:00",
        "monotonic_ns": 2, "wall_clock_synced": True,
        "device_id": "rpi", "score": 0.0, "schema_version": 1,
    })
    sender.run_once(now=datetime(2026, 4, 27, 12, 0, 11, tzinfo=timezone.utc))
    assert len(oracle.calls) == 2
    statuses = [c["t1_status"] for c in oracle.calls]
    assert statuses == [1, 2]
    assert {a["event_id"] for a in mqtt.acks} == {"e1", "e2"}


def test_e2e_oracle_down_then_up_recovers(tmp_path: Path):
    oracle = FakeOracle(canned=[
        MergeResult(rows_affected=0, ora_code=12541, error_message="ORA-12541: TNS:no listener"),
        MergeResult(rows_affected=1, ora_code=None, error_message=""),
    ])
    mqtt = FakeMqtt()
    sender, inbox = _make_sender(tmp_path, oracle=oracle, mqtt=mqtt,
                                  network=FakeNetwork(), time_watcher=FakeTimeWatcher())
    _ingest(inbox, {
        "event_id": "e1", "event": "ENTER", "event_time": "20260427120000",
        "event_time_iso": "2026-04-27T12:00:00+09:00",
        "monotonic_ns": 1, "wall_clock_synced": True,
        "device_id": "rpi", "score": 0.9, "schema_version": 1,
    })
    # First run: failure, retry scheduled.
    sender.run_once(now=datetime(2026, 4, 27, 12, 0, 1, tzinfo=timezone.utc))
    assert len(oracle.calls) == 1
    assert mqtt.acks == []
    row = inbox.get("e1")
    assert row.retry_count == 1 and row.last_error and "12541" in row.last_error

    # Second run after retry window passes: success.
    sender.run_once(now=datetime(2026, 4, 27, 12, 0, 30, tzinfo=timezone.utc))
    assert len(oracle.calls) == 2
    assert len(mqtt.acks) == 1


def test_e2e_unknown_ssid_holds_then_flushes(tmp_path: Path):
    oracle = FakeOracle()
    mqtt = FakeMqtt()
    network = FakeNetwork(ssid=None)
    sender, inbox = _make_sender(tmp_path, oracle=oracle, mqtt=mqtt,
                                  network=network, time_watcher=FakeTimeWatcher())
    _ingest(inbox, {
        "event_id": "e1", "event": "ENTER", "event_time": "20260427120000",
        "event_time_iso": "2026-04-27T12:00:00+09:00",
        "monotonic_ns": 1, "wall_clock_synced": True,
        "device_id": "rpi", "score": 0.9, "schema_version": 1,
    }, ssid="guest_wifi")
    sender.run_once(now=datetime(2026, 4, 27, 12, 0, 1, tzinfo=timezone.utc))
    assert oracle.calls == []
    # SSID returns to known.
    network.ssid = "factory_a_wifi"
    sender.run_once(now=datetime(2026, 4, 27, 12, 0, 5, tzinfo=timezone.utc))
    assert len(oracle.calls) == 1


def test_e2e_unsynced_event_then_sync_correction(tmp_path: Path):
    from services.bridge.src.time_watcher import SyncBaseline
    # Initial: unsynced, baseline=None.
    tw = FakeTimeWatcher(is_synced=False, baseline=None)
    oracle = FakeOracle()
    mqtt = FakeMqtt()
    sender, inbox = _make_sender(tmp_path, oracle=oracle, mqtt=mqtt,
                                  network=FakeNetwork(), time_watcher=tw)
    _ingest(inbox, {
        "event_id": "e1", "event": "ENTER", "event_time": None,
        "event_time_iso": None,
        "monotonic_ns": 6_200_000_000, "wall_clock_synced": False,
        "device_id": "rpi", "score": 0.9, "schema_version": 1,
    })
    sender.run_once(now=datetime(2026, 4, 27, 12, 0, 1, tzinfo=timezone.utc))
    assert oracle.calls == []  # held until sync

    # Sync arrives at 17:23:51 JST with monotonic 13_000_000_000.
    tw.is_synced = True
    tw.baseline = SyncBaseline(
        sync_wall=datetime(2026, 4, 27, 17, 23, 51, tzinfo=timezone(timedelta(hours=9))),
        sync_monotonic_ns=13_000_000_000,
    )
    sender.run_once(now=datetime(2026, 4, 27, 12, 0, 30, tzinfo=timezone.utc))
    assert len(oracle.calls) == 1
    # 13_000_000_000 - 6_200_000_000 = 6_800_000_000 ns = 6.8s before 17:23:51 -> 17:23:44.2 -> '20260427172344'
    assert oracle.calls[0]["mk_date"] == "20260427172344"
    assert mqtt.acks[0]["mk_date_committed"] == "20260427172344"


def test_e2e_circuit_breaker_opens_on_permanent_error(tmp_path: Path):
    oracle = FakeOracle(canned=[
        MergeResult(rows_affected=0, ora_code=942, error_message="ORA-00942: table or view does not exist"),
    ])
    mqtt = FakeMqtt()
    sender, inbox = _make_sender(tmp_path, oracle=oracle, mqtt=mqtt,
                                  network=FakeNetwork(), time_watcher=FakeTimeWatcher())
    _ingest(inbox, {
        "event_id": "e1", "event": "ENTER", "event_time": "20260427120000",
        "event_time_iso": "2026-04-27T12:00:00+09:00",
        "monotonic_ns": 1, "wall_clock_synced": True,
        "device_id": "rpi", "score": 0.9, "schema_version": 1,
    })
    sender.run_once(now=datetime(2026, 4, 27, 12, 0, 1, tzinfo=timezone.utc))
    # Subsequent run within 15 minutes is blocked by the breaker.
    _ingest(inbox, {
        "event_id": "e2", "event": "EXIT", "event_time": "20260427120010",
        "event_time_iso": "2026-04-27T12:00:10+09:00",
        "monotonic_ns": 2, "wall_clock_synced": True,
        "device_id": "rpi", "score": 0.0, "schema_version": 1,
    })
    sender.run_once(now=datetime(2026, 4, 27, 12, 5, 0, tzinfo=timezone.utc))
    assert len(oracle.calls) == 1  # second event was not even attempted


def test_e2e_idempotent_replay_does_not_duplicate(tmp_path: Path):
    oracle = FakeOracle()
    mqtt = FakeMqtt()
    sender, inbox = _make_sender(tmp_path, oracle=oracle, mqtt=mqtt,
                                  network=FakeNetwork(), time_watcher=FakeTimeWatcher())
    payload = {
        "event_id": "e1", "event": "ENTER", "event_time": "20260427120000",
        "event_time_iso": "2026-04-27T12:00:00+09:00",
        "monotonic_ns": 1, "wall_clock_synced": True,
        "device_id": "rpi", "score": 0.9, "schema_version": 1,
    }
    _ingest(inbox, payload)
    _ingest(inbox, payload)   # detector replay
    sender.run_once(now=datetime(2026, 4, 27, 12, 0, 1, tzinfo=timezone.utc))
    assert len(oracle.calls) == 1   # only one MERGE despite duplicate insert attempts
```

- [ ] **Step 3: Run integration tests**

Run: `.venv/bin/pytest tests/integration/ -v`

Expected: 6 passed.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_end_to_end.py tests/integration/fakes.py
git commit -m "test(integration): add end-to-end scenarios with fake Oracle/MQTT"
```

---

### Task 4.7: README expansion and acceptance checklist

**Files:**
- Modify: `README.md`
- Create: `docs/acceptance-checklist.md`

- [ ] **Step 1: Replace `README.md` with the production-ready version**

Replace `README.md` contents with:
```markdown
# Presence Logger

Detects person presence from a USB camera on Raspberry Pi 5 and records ENTER/EXIT events
to Oracle DB with exactly-once delivery. SNTP server and Oracle endpoint switch automatically
based on connected WiFi SSID.

## Architecture

Three Docker containers connected via the internal `presence-net` bridge network:

| Container | Role |
|---|---|
| `mosquitto` | Local MQTT broker (no host port) |
| `detector` | USB camera + MediaPipe person detection + MQTT publish (QoS=2) |
| `bridge` | MQTT subscribe + SQLite buffer + Oracle MERGE + ACK |

Detector → bridge handshake guarantees **exactly-once** delivery to Oracle even under power
loss, network drops, or DB outages. The bridge resolves the active WiFi SSID via
`nmcli` (mounted DBus socket), looks up the matching profile in `profiles.yaml`, and uses
the profile's Oracle credentials and SNTP servers.

See `docs/superpowers/specs/2026-04-27-presence-logger-design.md` for the full design and
`docs/superpowers/plans/2026-04-27-presence-logger.md` for the implementation plan.

## Production install (Raspberry Pi 5, Bookworm 64bit)

```bash
# 1. Clone repository to /opt/presence-logger
sudo git clone <repo-url> /opt/presence-logger
cd /opt/presence-logger

# 2. Place the MediaPipe model
sudo curl -o services/detector/models/efficientdet_lite0.tflite \
  https://storage.googleapis.com/mediapipe-models/object_detector/efficientdet_lite0/float32/latest/efficientdet_lite0.tflite

# 3. Run installer (creates /etc/presence-logger, copies examples, configures timesyncd)
sudo bash scripts/install.sh

# 4. Edit configs
sudo $EDITOR /etc/presence-logger/device.yaml      # set sta_no1/2/3
sudo $EDITOR /etc/presence-logger/profiles.yaml    # add WiFi profiles
sudo $EDITOR /etc/presence-logger/secrets.env      # set ORACLE_PASSWORD_*

# 5. (Optional) place Oracle wallets if any profile uses auth_mode=wallet
sudo unzip wallet.zip -d /etc/presence-logger/wallets/factory_b/

# 6. (Optional) build with Thick mode support
echo "INSTANT_CLIENT_URL=https://download.oracle.com/.../instantclient-basiclite-linux.arm64-21.13.0.0.0dbru.zip" | sudo tee /opt/presence-logger/.env

# 7. Start
sudo docker compose --project-directory /opt/presence-logger build
sudo systemctl enable --now presence-logger.service
```

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt -r services/detector/requirements.txt -r services/bridge/requirements.txt
.venv/bin/pytest
```

## Operations

- **Tail logs**: `bash scripts/tail-logs.sh`
- **Find an event's full trace**: `grep '<event_id>' /var/log/presence-logger/*.log | jq -s 'sort_by(.ts)'`
- **Inspect inbox**: `sqlite3 /var/lib/presence-logger/bridge_buf.db 'SELECT status, COUNT(*) FROM inbox GROUP BY status;'`
- **Inspect detector buffer**: `sqlite3 /var/lib/presence-logger/detector_buf.db 'SELECT status, COUNT(*) FROM pending_events GROUP BY status;'`

## Acceptance test checklist

See `docs/acceptance-checklist.md` for the manual-receipt scenarios to run after deploying
to the target hardware.
```

- [ ] **Step 2: Write the acceptance checklist**

Create `docs/acceptance-checklist.md`:
```markdown
# Acceptance Test Checklist

After installing on real hardware, verify each scenario by inspecting `HF1RCM01` and the
`detector.log` / `bridge.log` files.

## Scenario 1 — Basic ENTER/EXIT

- [ ] Stand in front of the camera for ≥ 5 seconds.
- [ ] Within ~3 seconds of standing still, an `ENTER` row (`T1_STATUS=1`) appears in `HF1RCM01`.
- [ ] `MK_DATE` is the JST timestamp; `STA_NO1/2/3` matches `device.yaml`.
- [ ] Step out of frame.
- [ ] Within ~3 seconds, an `EXIT` row (`T1_STATUS=2`) appears.

## Scenario 2 — Debounce ignores brief flashes

- [ ] Wave a hand briefly in front of the camera (< 2 seconds).
- [ ] No new rows appear in `HF1RCM01`.
- [ ] `detector.log` shows `candidate_start` and `candidate_cancel` events but no transition.

## Scenario 3 — Bridge restart

- [ ] Trigger an ENTER, confirm DB row appears.
- [ ] `docker restart presence-bridge` while staying in frame.
- [ ] No new ENTER row is added (idempotent).
- [ ] Step out → EXIT row is added once after restart.

## Scenario 4 — Oracle outage

- [ ] Block Oracle access (e.g. `iptables -A OUTPUT -d <oracle-ip> -j DROP`).
- [ ] Trigger ENTER and EXIT.
- [ ] `bridge.log` shows `merge_failed` with retry scheduling.
- [ ] Restore Oracle access.
- [ ] Both rows appear in `HF1RCM01` after the next retry window.

## Scenario 5 — WiFi loss

- [ ] Disable WiFi (`nmcli radio wifi off`).
- [ ] Trigger ENTER and EXIT.
- [ ] `bridge.log` shows events received but held (`unknown_ssid` or no profile resolution).
- [ ] Re-enable WiFi.
- [ ] Both rows appear in `HF1RCM01`.

## Scenario 6 — SNTP cold start

- [ ] On a freshly-imaged device with no RTC, `systemctl stop systemd-timesyncd`.
- [ ] Start the stack.
- [ ] Trigger ENTER.
- [ ] `detector.log` shows `wall_clock_synced=false`; `bridge.log` shows the event held.
- [ ] `systemctl start systemd-timesyncd`; wait for sync.
- [ ] `bridge.log` shows `sync_acquired` and the event committed with the correct backfilled MK_DATE.

## Scenario 7 — Camera removal

- [ ] During an active ENTER state, unplug the USB camera.
- [ ] `detector.log` shows `camera_failure` after 10 consecutive failures.
- [ ] An automatic EXIT row (with `reason=camera_lost` in detector logs) appears in `HF1RCM01`.

## Scenario 8 — Permanent error / circuit breaker

- [ ] Temporarily revoke the Oracle user's INSERT privilege on `HF1RCM01` (or rename the table) to provoke ORA-00942 / ORA-01031.
- [ ] Trigger an ENTER.
- [ ] `bridge.log` shows `circuit_open` and CRITICAL messages.
- [ ] Restore privileges.
- [ ] After the half-open window (default 15 min), the next ENTER succeeds and `circuit_close` is logged.

## Scenario 9 — Log rotation

- [ ] Tail `/var/log/presence-logger/`, run for several hours under normal load.
- [ ] Confirm log files rotate at 10 MB and that `detector.log.1` ... `detector.log.5` exist.

## Scenario 10 — Buffer ring eviction (optional)

- [ ] Lower `buffer.max_rows` to 10 in `detector.yaml` and `bridge.yaml`, restart.
- [ ] Trigger > 10 events while bridge is unable to ACK (e.g. block MQTT).
- [ ] Confirm only the most recent 10 are retained in `pending_events` and oldest are evicted with WARN-level log entries.
```

- [ ] **Step 3: Commit**

```bash
git add README.md docs/acceptance-checklist.md
git commit -m "docs: production README and acceptance checklist"
```

---

### Task 4.8: Final repository smoke test

- [ ] **Step 1: Run the full unit/integration test suite**

Run: `.venv/bin/pytest -v`

Expected: All tests pass; no warnings about uncollected files.

- [ ] **Step 2: Run linters**

Run: `.venv/bin/ruff check .`

Expected: `All checks passed!`

- [ ] **Step 3: Verify the docker images build**

Run:
```bash
docker compose build mosquitto
docker compose build detector            # may fail without the model file — that's expected
docker compose build bridge
```

Expected: `mosquitto` and `bridge` succeed. `detector` succeeds only if the
`efficientdet_lite0.tflite` model file is present.

- [ ] **Step 4: Final commit**

```bash
git add -A
git status   # should be clean (no unstaged changes)
git log --oneline | head -30
```

Expected: All previous commits visible, no pending changes.

---

## Phase 4 complete

The repository is now fully buildable, testable, and deployable:
- `mosquitto.conf` constrains the broker to internal-only use
- `docker-compose.yml` defines all three services with correct mounts (DBus, devices,
  configs, hostname, wallets)
- `config/*.yaml.example` files document every tunable
- `scripts/install.sh` is idempotent: directories, configs, timesyncd, systemd unit
- `systemd/presence-logger.service` controls the compose lifecycle
- `tests/integration/test_end_to_end.py` covers six full pipelines without real Oracle
- `README.md` is operator-facing
- `docs/acceptance-checklist.md` lists the 10 manual hardware scenarios

---




