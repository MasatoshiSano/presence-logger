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
