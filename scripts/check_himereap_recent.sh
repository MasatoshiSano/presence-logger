#!/usr/bin/env bash
# check_himereap_recent.sh
# Quick verification: switch to HIME-H-REAP, SELECT recent rows from HHC001
# for the configured station triple (default: last 1 hour), restore UFI.
# Read-only -- never INSERTs/DELETEs anything. Sentinel 2099% rows are
# excluded by the sidecar's /select_range guard.
#
# Usage:
#   sudo bash scripts/check_himereap_recent.sh [LOOKBACK_SECONDS=3600]

set -uo pipefail
[[ $EUID -eq 0 ]] || { echo "must be root" >&2; exit 1; }

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TS="$(date +%Y%m%dT%H%M%S)"
LOG="/tmp/check-himereap-${TS}.log"

PROFILE_NAME="${PROFILE_NAME:-HIME-H-REAP}"
PROFILES_YAML="${PROFILES_YAML:-/etc/presence-logger/profiles.yaml}"
SECRETS_ENV="${SECRETS_ENV:-/etc/presence-logger/secrets.env}"
HOME_PROFILE="${HOME_PROFILE:-UFI_103134}"
TMP_PROFILE="${TMP_PROFILE:-${PROFILE_NAME}-check}"
CONTAINER="${CONTAINER:-presence-oracle-jdbc}"
SIDECAR_IN="${SIDECAR_IN:-http://127.0.0.1:8086}"
LOOKBACK_SECONDS="${1:-${LOOKBACK_SECONDS:-3600}}"

declare -A PCFG
raw=$(python3 - "$PROFILES_YAML" "$PROFILE_NAME" "$SECRETS_ENV" <<'PY'
import os, sys, yaml, re
profiles_path, name, secrets_path = sys.argv[1], sys.argv[2], sys.argv[3]
if os.path.isfile(secrets_path):
    with open(secrets_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())
_var = re.compile(r"\$\{([^}]+)\}")
def expand(v):
    if isinstance(v, str): return _var.sub(lambda m: os.environ.get(m.group(1), ""), v)
    if isinstance(v, dict): return {k: expand(x) for k, x in v.items()}
    if isinstance(v, list): return [expand(x) for x in v]
    return v
with open(profiles_path) as f: data = yaml.safe_load(f)
profile = expand((data.get("profiles") or {}).get(name) or {})
if not profile: print(f"ERR no profile {name}", file=sys.stderr); sys.exit(2)
wifi = profile.get("wifi") or {}; sip = wifi.get("static_ipv4") or {}
oracle = profile.get("oracle") or {}; station = profile.get("station") or {}
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
eval "$raw"

log() { printf '[%s] %s\n' "$(date -Iseconds)" "$*" | tee -a "$LOG"; }
cleanup() {
    local rc=$?
    log "== cleanup (rc=$rc): restoring $HOME_PROFILE =="
    nmcli connection up "$HOME_PROFILE" >>"$LOG" 2>&1 || true
    nmcli -t -f NAME connection show | grep -Fxq "$TMP_PROFILE" && \
        nmcli connection delete "$TMP_PROFILE" >>"$LOG" 2>&1 || true
    log "== done. Full log at $LOG =="
    exit "$rc"
}
trap cleanup EXIT INT TERM

NOW_S=$(date +%s)
FROM_S=$((NOW_S - LOOKBACK_SECONDS))
MK_DATE_FROM=$(date -d "@$FROM_S" '+%Y%m%d%H%M%S')
MK_DATE_TO=$(date -d "@$NOW_S" '+%Y%m%d%H%M%S')

log "== HIME-H-REAP recent rows check =="
log "STA: ${PCFG[sta_no1]}/${PCFG[sta_no2]}/${PCFG[sta_no3]}  table: ${PCFG[oracle_table]}"
log "window: MK_DATE $MK_DATE_FROM .. $MK_DATE_TO (lookback ${LOOKBACK_SECONDS}s)"

iw reg set JP >>"$LOG" 2>&1
nmcli -t -f NAME connection show | grep -Fxq "$TMP_PROFILE" && \
    nmcli connection delete "$TMP_PROFILE" >>"$LOG" 2>&1 || true
nmcli connection add type wifi con-name "$TMP_PROFILE" ifname wlan0 \
    ssid "$PROFILE_NAME" 802-11-wireless.hidden yes \
    802-11-wireless-security.key-mgmt wpa-psk \
    802-11-wireless-security.psk "${PCFG[wifi_psk]}" \
    ipv4.method manual ipv4.addresses "${PCFG[static_ip]}" \
    ipv4.gateway "${PCFG[static_gw]}" ipv4.dns "${PCFG[static_dns]}" \
    ipv6.method disabled connection.autoconnect no >>"$LOG" 2>&1

log "switching to $PROFILE_NAME ..."
nmcli connection up "$TMP_PROFILE" >>"$LOG" 2>&1 || { log "FAIL: switch"; exit 2; }
sleep 4
ACTUAL_SSID="$(nmcli -t -f ACTIVE,SSID dev wifi | awk -F: '$1=="yes"{print $2; exit}')"
[[ "$ACTUAL_SSID" == "$PROFILE_NAME" ]] || { log "FAIL: SSID=$ACTUAL_SSID"; exit 2; }

docker compose --project-directory "$REPO_DIR" up -d oracle-jdbc >>"$LOG" 2>&1
for i in $(seq 1 30); do
    docker exec "$CONTAINER" wget -q --timeout=10 -O - "$SIDECAR_IN/healthz" 2>/dev/null | grep -q '^ok' && break
    sleep 1
done

log "POST /select_range ..."
BODY="$(python3 - <<PY
import urllib.parse
print(urllib.parse.urlencode({
    "url":          "jdbc:oracle:thin:@${PCFG[oracle_host]}:${PCFG[oracle_port]}/${PCFG[oracle_service]}",
    "user":         "${PCFG[oracle_user]}",
    "password":     "${PCFG[oracle_password]}",
    "table_name":   "${PCFG[oracle_table]}",
    "sta_no1":      "${PCFG[sta_no1]}",
    "sta_no2":      "${PCFG[sta_no2]}",
    "sta_no3":      "${PCFG[sta_no3]}",
    "mk_date_from": "${MK_DATE_FROM}",
    "mk_date_to":   "${MK_DATE_TO}",
}))
PY
)"
RESPONSE="$(docker exec -i "$CONTAINER" wget -q --timeout=40 \
    --header='Content-Type: application/x-www-form-urlencoded' \
    --post-data="$BODY" -O - "$SIDECAR_IN/select_range" 2>&1)"
log "/select_range response:"
printf '%s\n' "$RESPONSE" | tee -a "$LOG"
log "== rows in HHC001 (last ${LOOKBACK_SECONDS}s) =="
printf '%s\n' "$RESPONSE" | awk -F= '/^row=/{print $2}' | tee -a "$LOG"

COUNT="$(printf '%s\n' "$RESPONSE" | awk -F= '/^count=/{print $2}')"
log "TOTAL ROWS: $COUNT"
exit 0
