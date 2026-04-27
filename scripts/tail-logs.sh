#!/usr/bin/env bash
# Tail all presence-logger JSON logs and pretty-print key fields with jq.
set -euo pipefail
exec tail -F /var/log/presence-logger/*.log \
  | jq -c '{ts, level, logger, event, event_id, message}'
