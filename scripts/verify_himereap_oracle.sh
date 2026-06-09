#!/usr/bin/env bash
# verify_himereap_oracle.sh
# One-shot verification: switch Wi-Fi to HIME-H-REAP, exercise the
# oracle-jdbc sidecar against HHC001, restore UFI_103134.
#
# Designed to be safe even if the calling shell or Claude Code session dies
# mid-flight: the `trap cleanup EXIT` reliably restores the home Wi-Fi
# (UFI_103134) so the Pi never gets stranded on the corporate closed network.
#
# All log lines are appended to /tmp/verify-himereap-oracle-<ts>.log so the
# result is inspectable after the network round-trip, even if stdout was lost.
#
# Run as root:
#   sudo bash scripts/verify_himereap_oracle.sh
#
# Exit code:
#   0 — MERGE returned rows_affected=1 (or 0 on idempotent re-run) AND ora_code empty
#   1 — sidecar unreachable, MERGE failed, network setup failed, etc.
#   2 — Wi-Fi switch failed (likely regdomain/SSID issue)

set -uo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "must be run as root (sudo)" >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TS="$(date +%Y%m%dT%H%M%S)"
LOG="/tmp/verify-himereap-oracle-${TS}.log"

# The profile to verify is the SSID key in /etc/presence-logger/profiles.yaml.
# Override with PROFILE_NAME=<ssid> for a different site.
PROFILE_NAME="${PROFILE_NAME:-HIME-H-REAP}"
PROFILES_YAML="${PROFILES_YAML:-/etc/presence-logger/profiles.yaml}"
SECRETS_ENV="${SECRETS_ENV:-/etc/presence-logger/secrets.env}"

# The home/dev Wi-Fi profile to restore on EXIT. Not stored in profiles.yaml
# because it's a developer-machine concern, not a site config.
HOME_PROFILE="${HOME_PROFILE:-UFI_103134}"
TMP_PROFILE="${TMP_PROFILE:-${PROFILE_NAME}-verify}"

# All other values are pulled from profiles.yaml below. Override knobs:
#   PROFILE_NAME       which profile (= SSID) to verify
#   PROFILES_YAML      where profiles.yaml lives
#   SECRETS_ENV        env file expanding ${WIFI_PSK_*} / ${ORACLE_PASSWORD_*}
#   HOME_PROFILE       nmcli connection to restore on exit
#   TMP_PROFILE        nmcli connection name to (re)create for the verify run
# Profile shape (see config/profiles.yaml.example):
#   profiles.<NAME>.wifi.psk / .hidden / .static_ipv4.{address,gateway,dns}
#   profiles.<NAME>.oracle.{host,port,service_name,user,password,table_name}
#   profiles.<NAME>.station.{sta_no1,sta_no2,sta_no3}   (optional override)
declare -A PCFG
load_profile_from_yaml() {
    # shellcheck disable=SC2155
    local raw
    raw=$(python3 - "$PROFILES_YAML" "$PROFILE_NAME" "$SECRETS_ENV" <<'PY'
import os, sys, yaml
profiles_path, name, secrets_path = sys.argv[1], sys.argv[2], sys.argv[3]
# Expand the secrets.env file into the environment so ${VAR} placeholders
# inside profiles.yaml resolve.
if os.path.isfile(secrets_path):
    with open(secrets_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

import re
_var = re.compile(r"\$\{([^}]+)\}")
def expand(v):
    if isinstance(v, str):
        return _var.sub(lambda m: os.environ.get(m.group(1), ""), v)
    if isinstance(v, dict):
        return {k: expand(x) for k, x in v.items()}
    if isinstance(v, list):
        return [expand(x) for x in v]
    return v

with open(profiles_path) as f:
    data = yaml.safe_load(f)
profile = expand((data.get("profiles") or {}).get(name) or {})
if not profile:
    print(f"ERR no such profile: {name}", file=sys.stderr); sys.exit(2)

wifi = profile.get("wifi") or {}
sip = wifi.get("static_ipv4") or {}
oracle = profile.get("oracle") or {}
station = profile.get("station") or {}

# Single-quoted shell key=value lines for `eval` consumption.
def emit(k, v): print(f"PCFG[{k}]={repr(str(v))}")
emit("wifi_psk", wifi.get("psk", ""))
emit("wifi_hidden", "yes" if wifi.get("hidden") else "no")
emit("static_ip", sip.get("address", ""))
emit("static_gw", sip.get("gateway", ""))
emit("static_dns", " ".join(sip.get("dns") or []))
emit("oracle_host", oracle.get("host", ""))
emit("oracle_port", oracle.get("port", "1521"))
emit("oracle_service", oracle.get("service_name", ""))
emit("oracle_user", oracle.get("user", ""))
emit("oracle_password", oracle.get("password", ""))
emit("oracle_table", oracle.get("table_name", "HF1RCM01"))
emit("sta_no1", station.get("sta_no1", "999"))
emit("sta_no2", station.get("sta_no2", "998"))
emit("sta_no3", station.get("sta_no3", "997"))
PY
)
    if [[ -z "$raw" ]]; then
        echo "FAIL: could not load profile '$PROFILE_NAME' from $PROFILES_YAML" >&2
        exit 1
    fi
    eval "$raw"
}
load_profile_from_yaml

HIME_SSID="${HIME_SSID:-$PROFILE_NAME}"
HIME_PSK="${HIME_PSK:-${PCFG[wifi_psk]}}"
STATIC_IP="${STATIC_IP:-${PCFG[static_ip]}}"
STATIC_GW="${STATIC_GW:-${PCFG[static_gw]}}"
STATIC_DNS="${STATIC_DNS:-${PCFG[static_dns]}}"

ORACLE_HOST="${ORACLE_HOST:-${PCFG[oracle_host]}}"
ORACLE_PORT="${ORACLE_PORT:-${PCFG[oracle_port]}}"
ORACLE_SVC="${ORACLE_SVC:-${PCFG[oracle_service]}}"
ORACLE_USER="${ORACLE_USER:-${PCFG[oracle_user]}}"
ORACLE_PASSWORD="${ORACLE_PASSWORD:-${PCFG[oracle_password]}}"
ORACLE_TABLE="${ORACLE_TABLE:-${PCFG[oracle_table]}}"

# oracle-jdbc only listens on the presence-net docker network (no host port
# published) — same as production. Health/merge calls go via docker exec.
CONTAINER="${CONTAINER:-presence-oracle-jdbc}"
SIDECAR_IN="${SIDECAR_IN:-http://127.0.0.1:8086}"

log() { printf '[%s] %s\n' "$(date -Iseconds)" "$*" | tee -a "$LOG"; }

# Run wget inside the sidecar container against its loopback.
in_container_wget() {
    docker exec "$CONTAINER" wget -q --timeout=10 "$@"
}

cleanup() {
    local rc=$?
    log "== cleanup (rc=$rc): restoring $HOME_PROFILE =="
    nmcli connection up "$HOME_PROFILE" >>"$LOG" 2>&1 || \
        log "WARN: failed to re-up $HOME_PROFILE"
    # Always remove the temporary profile so credentials don't linger in
    # NetworkManager's keystore between runs.
    if nmcli -t -f NAME connection show | grep -Fxq "$TMP_PROFILE"; then
        nmcli connection delete "$TMP_PROFILE" >>"$LOG" 2>&1 || true
        log "removed temp profile $TMP_PROFILE"
    fi
    log "== done. Full log at $LOG =="
    exit "$rc"
}
trap cleanup EXIT INT TERM

log "== presence-logger HIME-H-REAP verification =="
log "repo: $REPO_DIR"
log "home profile: $HOME_PROFILE  (must already exist)"
log "tmp profile : $TMP_PROFILE"
log "target SSID : $HIME_SSID"
log "static IP   : $STATIC_IP via $STATIC_GW (DNS $STATIC_DNS)"
log "JDBC URL    : jdbc:oracle:thin:@$ORACLE_HOST:$ORACLE_PORT/$ORACLE_SVC"
log "JDBC user   : $ORACLE_USER  table=$ORACLE_TABLE"
log "sidecar     : container=$CONTAINER  internal=$SIDECAR_IN"

# 1. regdomain JP (idempotent)
log "step 1: iw reg set JP"
iw reg set JP >>"$LOG" 2>&1 || { log "FAIL: iw reg set JP"; exit 2; }
sleep 2

# 2. (Re)create the HIME-H-REAP nmcli profile with the static-IP shape.
log "step 2: nmcli connection (re)create $TMP_PROFILE"
if nmcli -t -f NAME connection show | grep -Fxq "$TMP_PROFILE"; then
    nmcli connection delete "$TMP_PROFILE" >>"$LOG" 2>&1 || true
fi
nmcli connection add type wifi con-name "$TMP_PROFILE" ifname wlan0 \
    ssid "$HIME_SSID" \
    802-11-wireless.hidden yes \
    802-11-wireless-security.key-mgmt wpa-psk \
    802-11-wireless-security.psk "$HIME_PSK" \
    ipv4.method manual \
    ipv4.addresses "$STATIC_IP" \
    ipv4.gateway "$STATIC_GW" \
    ipv4.dns "$STATIC_DNS" \
    ipv6.method disabled \
    connection.autoconnect no >>"$LOG" 2>&1 \
    || { log "FAIL: nmcli connection add"; exit 2; }

# 3. Switch to HIME-H-REAP.
log "step 3: nmcli connection up $TMP_PROFILE"
if ! nmcli connection up "$TMP_PROFILE" >>"$LOG" 2>&1; then
    log "FAIL: could not bring $TMP_PROFILE up (signal? PSK? regdomain?)"
    exit 2
fi
sleep 4
ACTUAL_SSID="$(nmcli -t -f ACTIVE,SSID dev wifi | awk -F: '$1=="yes"{print $2; exit}')"
log "active SSID after switch: $ACTUAL_SSID"
if [[ "$ACTUAL_SSID" != "$HIME_SSID" ]]; then
    log "FAIL: expected SSID=$HIME_SSID, got $ACTUAL_SSID"
    exit 2
fi

# 4. Bring up oracle-jdbc sidecar (idempotent; safe if already running).
log "step 4: docker compose up -d oracle-jdbc"
docker compose --project-directory "$REPO_DIR" up -d oracle-jdbc >>"$LOG" 2>&1 \
    || { log "FAIL: docker compose up oracle-jdbc"; exit 1; }

# 5. Wait until /healthz responds. Probe from INSIDE the sidecar container
#    because the sidecar's port 8086 is not published to the host (matches
#    production: bridge calls it via presence-net, not via host loopback).
log "step 5: wait for $SIDECAR_IN/healthz (inside $CONTAINER)"
for i in $(seq 1 30); do
    if in_container_wget -O - "$SIDECAR_IN/healthz" 2>/dev/null | grep -q '^ok'; then
        log "  healthz OK after ${i}s"
        break
    fi
    sleep 1
done
if ! in_container_wget -O - "$SIDECAR_IN/healthz" 2>/dev/null | grep -q '^ok'; then
    log "FAIL: healthz never came up"
    docker logs "$CONTAINER" 2>&1 | tail -20 | tee -a "$LOG"
    exit 1
fi

# 6. Reach Oracle host from inside the oracle-jdbc container.
log "step 6: TCP reach test $ORACLE_HOST:$ORACLE_PORT (from inside $CONTAINER)"
docker exec "$CONTAINER" /bin/sh -c \
    "cat </dev/tcp/$ORACLE_HOST/$ORACLE_PORT" >/dev/null 2>&1 \
    && log "  TCP reachable" \
    || log "WARN: TCP reach test inconclusive (sh may lack /dev/tcp); proceeding"

# 7. POST a smoke MERGE -- also via docker exec.
log "step 7: POST /merge to $SIDECAR_IN (inside $CONTAINER)"
# MK_DATE format MUST match services/bridge/src/time_correction.format_mk_date_jst:
# YYYYMMDDHHMMSS (14 chars). The HF1RCM01.MK_DATE column is 14 wide; anything
# else triggers ORA-12899 "value too large for column".
#
# We use a *stable sentinel* timestamp + station triple (999/998/997) so the
# MERGE is idempotent across runs: first invocation inserts the row, every
# subsequent run hits WHEN MATCHED (rows_affected=0) without polluting
# HHC001 with new rows. Override MK_DATE / STA_NO* via env if needed.
MK_DATE="${MK_DATE:-20990101000002}"
SMOKE_STA1="${SMOKE_STA1:-${PCFG[sta_no1]}}"
SMOKE_STA2="${SMOKE_STA2:-${PCFG[sta_no2]}}"
SMOKE_STA3="${SMOKE_STA3:-${PCFG[sta_no3]}}"
BODY="$(python3 - <<PY
import urllib.parse
print(urllib.parse.urlencode({
    "url":                 "jdbc:oracle:thin:@${ORACLE_HOST}:${ORACLE_PORT}/${ORACLE_SVC}",
    "user":                "${ORACLE_USER}",
    "password":            "${ORACLE_PASSWORD}",
    "table_name":          "${ORACLE_TABLE}",
    "mk_date":             "${MK_DATE}",
    "sta_no1":             "${SMOKE_STA1}",
    "sta_no2":             "${SMOKE_STA2}",
    "sta_no3":             "${SMOKE_STA3}",
    "t1_status":           "1",
    "connect_timeout_ms":  "10000",
    "read_timeout_ms":     "30000",
}))
PY
)"
RESPONSE="$(
    docker exec -i "$CONTAINER" wget -q --timeout=40 \
        --header='Content-Type: application/x-www-form-urlencoded' \
        --post-data="$BODY" -O - "$SIDECAR_IN/merge" 2>&1
)" || { log "FAIL: docker exec wget /merge failed: $RESPONSE"; exit 1; }
log "MERGE response:"
printf '%s\n' "$RESPONSE" | tee -a "$LOG"

# 8. Parse the key=value response.
ROWS_AFFECTED="$(printf '%s\n' "$RESPONSE" | awk -F= '/^rows_affected=/{print $2}')"
ORA_CODE="$(printf '%s\n' "$RESPONSE" | awk -F= '/^ora_code=/{print $2}')"
ERROR_MSG="$(printf '%s\n' "$RESPONSE" | awk -F= '/^error_message=/{sub(/^error_message=/,""); print}')"

log "parsed: rows_affected=$ROWS_AFFECTED ora_code='$ORA_CODE' error_message='$ERROR_MSG'"

if [[ -n "$ORA_CODE" ]]; then
    log "FAIL: Oracle returned ora_code=$ORA_CODE"
    exit 1
fi
if [[ "$ROWS_AFFECTED" != "1" && "$ROWS_AFFECTED" != "0" ]]; then
    log "FAIL: unexpected rows_affected=$ROWS_AFFECTED"
    exit 1
fi

log "SUCCESS: HIME-H-REAP -> oracle-jdbc -> HHC001 MERGE confirmed"
# trap cleanup restores UFI_103134 on exit.
exit 0
