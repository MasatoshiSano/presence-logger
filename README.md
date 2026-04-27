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
# Set up local Python env for tests (skips mediapipe — Docker-only runtime dep)
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt -r services/detector/requirements.txt -r services/bridge/requirements.txt

# Run tests
.venv/bin/pytest
```

`mediapipe` is intentionally absent from `services/detector/requirements.txt` because no
aarch64 wheels exist for Python 3.13. It is listed in `services/detector/requirements-runtime.txt`
and installed inside the Docker image only.

## Production Install

See `scripts/install.sh` and `systemd/presence-logger.service`.
