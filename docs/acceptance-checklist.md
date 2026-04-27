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
