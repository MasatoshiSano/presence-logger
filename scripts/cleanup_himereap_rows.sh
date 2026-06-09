#!/usr/bin/env bash
# cleanup_himereap_rows.sh
# One-shot: switch to HIME-H-REAP, POST /cleanup_range to oracle-jdbc to delete
# rows that were accidentally flushed during a previous verify-script run, then
# restore UFI_103134. Sentinel rows (MK_DATE LIKE '2099%') are protected by
# Main.java's hard-coded guard.
#
# Configuration: same env-override knobs as scripts/verify_himereap_oracle.sh.
# Defaults pulled from /etc/presence-logger/profiles.yaml entry PROFILE_NAME.
#
# Run as root:
#   sudo bash scripts/cleanup_himereap_rows.sh \
#     [MK_DATE_FROM=20260604170000 MK_DATE_TO=20260604173000]

set -uo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "must be run as root (sudo)" >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TS="$(date +%Y%m%dT%H%M%S)"
LOG="/tmp/cleanup-himereap-${TS}.log"

PROFILE_NAME="${PROFILE_NAME:-HIME-H-REAP}"
PROFILES_YAML="${PROFILES_YAML:-/etc/presence-logger/profiles.yaml}"
SECRETS_ENV="${SECRETS_ENV:-/etc/presence-logger/secrets.env}"
HOME_PROFILE="${HOME_PROFILE:-UFI_103134}"
TMP_PROFILE="${TMP_PROFILE:-${PROFILE_NAME}-cleanup}"
CONTAINER="${CONTAINER:-presence-oracle-jdbc}"
SIDECAR_IN="${SIDECAR_IN:-http://127.0.0.1:8086}"

# The deletion window. Defaults target the leak documented during the
# 2026-06-04 deployment (UFI rows that flushed at 17:34). Override via env.
MK_DATE_FROM="${MK_DATE_FROM:-20260604170000}"
MK_DATE_TO="${MK_DATE_TO:-20260604173000}"

declare -A PCFG
load_profile_from_yaml() {
    local raw
    raw=$(python3 - "$PROFILES_YAML" "$PROFILE_NAME" "$SECRETS_ENV" <<'PY'
import os, sys, yaml, re
profiles_path, name, secrets_path = sys.argv[1], sys.argv[2], sys.argv[3]
if os.path.isfile(secrets_path):
    with open(secrets_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())
_var = re.compile(r"\$\{([^}]+)\}")
def expand(v):
    if isinstance(v, str): return _var.sub(lambda m: os.environ.get(m.group(1), ""), v)
    if isinstance(v, dict): return {k: expand(x) for k, x in v.items()}
    if isinstance(v, list): return [expand(x) for x in v]
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
def emit(k, v): print(f"PCFG[{k}]={repr(str(v))}")
emit("wifi_psk", wifi.get("psk", ""))
emit("static_ip", sip.get("address", ""))
emit("static_gw", sip.get("gateway", ""))
emit("static_dns", " ".join(sip.get("dns") or []))
emit("oracle_host", oracle.get("host", ""))
emit("oracle_port", oracle.get("port", "1521"))
emit("oracle_service", oracle.get("service_name", ""))
emit("oracle_user", oracle.get("user", ""))
emit("oracle_password", oracle.get("password", ""))
emit("oracle_table", oracle.get("table_name", "HF1RCM01"))
emit("sta_no1", station.get("sta_no1", ""))
emit("sta_no2", station.get("sta_no2", ""))
emit("sta_no3", station.get("sta_no3", ""))
PY
)
    [[ -z "$raw" ]] && { echo "FAIL: could not load profile '$PROFILE_NAME'" >&2; exit 1; }
    eval "$raw"
}
load_profile_from_yaml

HIME_SSID="$PROFILE_NAME"
HIME_PSK="${PCFG[wifi_psk]}"
STATIC_IP="${PCFG[static_ip]}"
STATIC_GW="${PCFG[static_gw]}"
STATIC_DNS="${PCFG[static_dns]}"
ORACLE_HOST="${PCFG[oracle_host]}"
ORACLE_PORT="${PCFG[oracle_port]}"
ORACLE_SVC="${PCFG[oracle_service]}"
ORACLE_USER="${PCFG[oracle_user]}"
ORACLE_PASSWORD="${PCFG[oracle_password]}"
ORACLE_TABLE="${PCFG[oracle_table]}"
STA1="${PCFG[sta_no1]}"
STA2="${PCFG[sta_no2]}"
STA3="${PCFG[sta_no3]}"

log() { printf '[%s] %s\n' "$(date -Iseconds)" "$*" | tee -a "$LOG"; }
in_container_wget() { docker exec "$CONTAINER" wget -q --timeout=10 "$@"; }

cleanup() {
    local rc=$?
    log "== cleanup (rc=$rc): restoring $HOME_PROFILE =="
    nmcli connection up "$HOME_PROFILE" >>"$LOG" 2>&1 || log "WARN: failed to re-up $HOME_PROFILE"
    if nmcli -t -f NAME connection show | grep -Fxq "$TMP_PROFILE"; then
        nmcli connection delete "$TMP_PROFILE" >>"$LOG" 2>&1 || true
        log "removed temp profile $TMP_PROFILE"
    fi
    log "== done. Full log at $LOG =="
    exit "$rc"
}
trap cleanup EXIT INT TERM

log "== presence-logger HIME-H-REAP cleanup =="
log "delete window: MK_DATE $MK_DATE_FROM .. $MK_DATE_TO"
log "delete key   : STA_NO=$STA1/$STA2/$STA3 (from profiles.yaml)"
log "table        : $ORACLE_TABLE @ jdbc:oracle:thin:@$ORACLE_HOST:$ORACLE_PORT/$ORACLE_SVC"

log "step 1: iw reg set JP"
iw reg set JP >>"$LOG" 2>&1 || { log "FAIL: iw reg set JP"; exit 2; }

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

log "step 3: nmcli connection up $TMP_PROFILE"
nmcli connection up "$TMP_PROFILE" >>"$LOG" 2>&1 || { log "FAIL: bring up $TMP_PROFILE"; exit 2; }
sleep 4
ACTUAL_SSID="$(nmcli -t -f ACTIVE,SSID dev wifi | awk -F: '$1=="yes"{print $2; exit}')"
log "active SSID after switch: $ACTUAL_SSID"
[[ "$ACTUAL_SSID" == "$HIME_SSID" ]] || { log "FAIL: SSID mismatch"; exit 2; }

log "step 4: docker compose up -d oracle-jdbc"
docker compose --project-directory "$REPO_DIR" up -d oracle-jdbc >>"$LOG" 2>&1 \
    || { log "FAIL: docker compose up oracle-jdbc"; exit 1; }

log "step 5: wait for $SIDECAR_IN/healthz"
for i in $(seq 1 30); do
    if in_container_wget -O - "$SIDECAR_IN/healthz" 2>/dev/null | grep -q '^ok'; then
        log "  healthz OK after ${i}s"
        break
    fi
    sleep 1
done
in_container_wget -O - "$SIDECAR_IN/healthz" 2>/dev/null | grep -q '^ok' \
    || { log "FAIL: healthz never came up"; docker logs "$CONTAINER" 2>&1 | tail -20 | tee -a "$LOG"; exit 1; }

log "step 6: POST /cleanup_range"
BODY="$(python3 - <<PY
import urllib.parse
print(urllib.parse.urlencode({
    "url":                 "jdbc:oracle:thin:@${ORACLE_HOST}:${ORACLE_PORT}/${ORACLE_SVC}",
    "user":                "${ORACLE_USER}",
    "password":            "${ORACLE_PASSWORD}",
    "table_name":          "${ORACLE_TABLE}",
    "sta_no1":             "${STA1}",
    "sta_no2":             "${STA2}",
    "sta_no3":             "${STA3}",
    "mk_date_from":        "${MK_DATE_FROM}",
    "mk_date_to":          "${MK_DATE_TO}",
    "connect_timeout_ms":  "10000",
    "read_timeout_ms":     "30000",
}))
PY
)"
RESPONSE="$(
    docker exec -i "$CONTAINER" wget -q --timeout=40 \
        --header='Content-Type: application/x-www-form-urlencoded' \
        --post-data="$BODY" -O - "$SIDECAR_IN/cleanup_range" 2>&1
)" || { log "FAIL: docker exec wget /cleanup_range failed: $RESPONSE"; exit 1; }
log "cleanup_range response:"
printf '%s\n' "$RESPONSE" | tee -a "$LOG"

ROWS_DELETED="$(printf '%s\n' "$RESPONSE" | awk -F= '/^rows_deleted=/{print $2}')"
ORA_CODE="$(printf '%s\n' "$RESPONSE" | awk -F= '/^ora_code=/{print $2}')"

if [[ -n "$ORA_CODE" ]]; then
    log "FAIL: Oracle returned ora_code=$ORA_CODE"
    exit 1
fi
log "SUCCESS: deleted $ROWS_DELETED rows from $ORACLE_TABLE (sentinel 2099%% preserved)"
exit 0
