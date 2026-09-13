#!/usr/bin/env bash
# Tail process JSON logs (bridge / detector) and pretty-print key fields with jq.
# child-mqtt.log is a different schema (kind/payload); follow it separately:
#   sudo tail -F /var/log/presence-logger/child-mqtt.log
# `fromjson?` silently skips non-JSON lines (e.g. the `==> file <==` headers
# tail emits when following multiple files).
set -uo pipefail
exec tail -F \
  /var/log/presence-logger/bridge.log \
  /var/log/presence-logger/detector.log \
  | jq -Rc 'fromjson? | {ts, level, logger, event, event_id, message}'
