#!/usr/bin/env bash
# Tail all presence-logger JSON logs and pretty-print key fields with jq.
# `fromjson?` silently skips non-JSON lines (e.g. the `==> file <==` headers
# tail emits when following multiple files).
set -uo pipefail
exec tail -F /var/log/presence-logger/*.log \
  | jq -Rc 'fromjson? | {ts, level, logger, event, event_id, message}'
