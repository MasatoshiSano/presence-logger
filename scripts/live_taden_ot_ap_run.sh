#!/usr/bin/env bash
# live_taden_ot_ap_run.sh
# Live end-to-end test: switch to taden-ot-ap for ~60s, let the bridge flush
# real detector events into HHS001, then SELECT the new rows to prove they
# landed, then (optionally) restore HOME_PROFILE. Everything trap-guarded.
#
# Usage:
#   sudo bash scripts/live_taden_ot_ap_run.sh [WINDOW_SECONDS=60]
#
# Env overrides:
#   PROFILE_NAME, PROFILES_YAML, SECRETS_ENV, HOME_PROFILE, TMP_PROFILE
#   HOME_PROFILE="" (default) means "do not restore on exit" — just bring the
#   temp profile down and let NetworkManager pick whatever is available.

set -uo pipefail
if [[ $EUID -ne 0 ]]; then
    echo "must be run as root (sudo)" >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TS="$(date +%Y%m%dT%H%M%S)"
LOG="/tmp/live-taden-ot-ap-${TS}.log"

PROFILE_NAME="${PROFILE_NAME:-taden-ot-ap}"
PROFILES_YAML="${PROFILES_YAML:-/etc/presence-logger/profiles.yaml}"
SECRETS_ENV="${SECRETS_ENV:-/etc/presence-logger/secrets.env}"
HOME_PROFILE="${HOME_PROFILE:-}"
TMP_PROFILE="${TMP_PROFILE:-${PROFILE_NAME}-live}"
CONTAINER="${CONTAINER:-presence-oracle-jdbc}"
SIDECAR_IN="${SIDECAR_IN:-http://127.0.0.1:8086}"
WINDOW_SECONDS="${1:-${WINDOW_SECONDS:-60}}"

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
emit("sta_no1", station.get("sta_no1", ""))
emit("sta_no2", station.get("sta_no2", ""))
emit("sta_no3", station.get("sta_no3", ""))
PY
)
    [[ -z "$raw" ]] && { echo "FAIL: could not load profile '$PROFILE_NAME'" >&2; exit 1; }
    eval "$raw"
}
load_profile_from_yaml

# Fallback: profiles.yaml に station 上書きが無ければ device.yaml を読む
if [[ -z "${PCFG[sta_no1]:-}" || -z "${PCFG[sta_no2]:-}" || -z "${PCFG[sta_no3]:-}" ]]; then
    DEVICE_YAML="${DEVICE_YAML:-/etc/presence-logger/device.yaml}"
    if [[ -f "$DEVICE_YAML" ]]; then
        eval "$(python3 - "$DEVICE_YAML" <<'PY'
import sys, yaml
with open(sys.argv[1]) as f: d = yaml.safe_load(f) or {}
s = d.get("station") or {}
print(f"PCFG[sta_no1]={s.get('sta_no1','')!r}")
print(f"PCFG[sta_no2]={s.get('sta_no2','')!r}")
print(f"PCFG[sta_no3]={s.get('sta_no3','')!r}")
PY
)"
    fi
fi

ORACLE_HOST="${PCFG[oracle_host]}"
ORACLE_PORT="${PCFG[oracle_port]}"
ORACLE_SVC="${PCFG[oracle_service]}"
ORACLE_USER="${PCFG[oracle_user]}"
ORACLE_PASSWORD="${PCFG[oracle_password]}"
ORACLE_TABLE="${PCFG[oracle_table]}"
STA1="${PCFG[sta_no1]}"
STA2="${PCFG[sta_no2]}"
STA3="${PCFG[sta_no3]}"
TADEN_SSID="$PROFILE_NAME"
TADEN_PSK="${PCFG[wifi_psk]}"
TADEN_HIDDEN="${PCFG[wifi_hidden]}"
STATIC_IP="${PCFG[static_ip]}"
STATIC_GW="${PCFG[static_gw]}"
STATIC_DNS="${PCFG[static_dns]}"

log() { printf '[%s] %s\n' "$(date -Iseconds)" "$*" | tee -a "$LOG"; }

cleanup() {
    local rc=$?
    if [[ -n "$HOME_PROFILE" ]]; then
        log "== cleanup (rc=$rc): restoring $HOME_PROFILE =="
        nmcli connection up "$HOME_PROFILE" >>"$LOG" 2>&1 || log "WARN: failed to re-up $HOME_PROFILE"
    else
        log "== cleanup (rc=$rc): bringing $TMP_PROFILE down (no restore) =="
        nmcli connection down "$TMP_PROFILE" >>"$LOG" 2>&1 || true
    fi
    if nmcli -t -f NAME connection show | grep -Fxq "$TMP_PROFILE"; then
        nmcli connection delete "$TMP_PROFILE" >>"$LOG" 2>&1 || true
        log "removed temp profile $TMP_PROFILE"
    fi
    log "== done. Full log at $LOG =="
    exit "$rc"
}
trap cleanup EXIT INT TERM

snapshot_buffers() {
    local label="$1"
    log "buffers ($label):"
    {
        docker exec presence-detector python -c "
import sqlite3
c = sqlite3.connect('/var/lib/presence-logger/detector_buf.db')
print('  detector pending_events:')
for s, n in c.execute('SELECT status, COUNT(*) FROM pending_events GROUP BY status'):
    print(f'    {s:10s} {n}')
" 2>&1 || true
        docker exec presence-bridge python -c "
import sqlite3
c = sqlite3.connect('/var/lib/presence-logger/bridge_buf.db')
print('  bridge inbox:')
for s, n in c.execute('SELECT status, COUNT(*) FROM inbox GROUP BY status'):
    print(f'    {s:10s} {n}')
" 2>&1 || true
    } | tee -a "$LOG"
}

log "== presence-logger LIVE run @ $TADEN_SSID (${WINDOW_SECONDS}s window) =="
log "STA_NO triple: $STA1/$STA2/$STA3"
log "target table: $ORACLE_TABLE @ jdbc:oracle:thin:@$ORACLE_HOST:$ORACLE_PORT/$ORACLE_SVC"

START_MK="$(date '+%Y%m%d%H%M%S')"
log "start MK_DATE marker: $START_MK"
snapshot_buffers "before switch"

log "step 1: iw reg set JP + (re)create nmcli profile"
iw reg set JP >>"$LOG" 2>&1 || { log "FAIL: iw reg set JP"; exit 2; }
if nmcli -t -f NAME connection show | grep -Fxq "$TMP_PROFILE"; then
    nmcli connection delete "$TMP_PROFILE" >>"$LOG" 2>&1 || true
fi
nmcli connection add type wifi con-name "$TMP_PROFILE" ifname wlan0 \
    ssid "$TADEN_SSID" \
    802-11-wireless.hidden "$TADEN_HIDDEN" \
    802-11-wireless-security.key-mgmt wpa-psk \
    802-11-wireless-security.psk "$TADEN_PSK" \
    ipv4.method manual \
    ipv4.addresses "$STATIC_IP" \
    ipv4.gateway "$STATIC_GW" \
    ipv4.dns "$STATIC_DNS" \
    ipv6.method disabled \
    connection.autoconnect no >>"$LOG" 2>&1 \
    || { log "FAIL: nmcli connection add"; exit 2; }

log "step 2: nmcli connection up $TMP_PROFILE"
nmcli connection up "$TMP_PROFILE" >>"$LOG" 2>&1 || { log "FAIL: bring up $TMP_PROFILE"; exit 2; }
sleep 4
ACTUAL_SSID="$(nmcli -t -f ACTIVE,SSID dev wifi | awk -F: '$1=="yes"{print $2; exit}')"
log "active SSID after switch: $ACTUAL_SSID"
[[ "$ACTUAL_SSID" == "$TADEN_SSID" ]] || { log "FAIL: SSID mismatch"; exit 2; }

log "step 3: docker compose up -d oracle-jdbc (idempotent)"
docker compose --project-directory "$REPO_DIR" up -d oracle-jdbc >>"$LOG" 2>&1 \
    || { log "FAIL: docker compose up oracle-jdbc"; exit 1; }

log "step 4: wait healthz"
for i in $(seq 1 30); do
    docker exec "$CONTAINER" wget -q --timeout=10 -O - "$SIDECAR_IN/healthz" 2>/dev/null | grep -q '^ok' && break
    sleep 1
done
docker exec "$CONTAINER" wget -q --timeout=10 -O - "$SIDECAR_IN/healthz" 2>/dev/null | grep -q '^ok' \
    || { log "FAIL: healthz never came up"; exit 1; }
log "  healthz OK"

log "step 5: LIVE window — letting detector→bridge→HHS001 run for ${WINDOW_SECONDS}s"
log "  (bridge's network_watcher polls every 5s, so first MERGE may be ~5s in)"
sleep "$WINDOW_SECONDS"

END_MK="$(date '+%Y%m%d%H%M%S')"
log "end MK_DATE marker: $END_MK"

log "step 6: bridge log tail (merge_committed since start)"
docker logs presence-bridge --since "${WINDOW_SECONDS}s" 2>&1 \
    | grep -E "(merge_committed|merge_failed|received|drop_unknown_ssid)" \
    | tail -30 | tee -a "$LOG"

log "step 7: POST /select_range to confirm rows in HHS001"
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
    "mk_date_from":        "${START_MK}",
    "mk_date_to":          "${END_MK}",
    "connect_timeout_ms":  "10000",
    "read_timeout_ms":     "30000",
}))
PY
)"
RESPONSE="$(
    docker exec -i "$CONTAINER" wget -q --timeout=40 \
        --header='Content-Type: application/x-www-form-urlencoded' \
        --post-data="$BODY" -O - "$SIDECAR_IN/select_range" 2>&1
)" || { log "FAIL: docker exec wget /select_range failed: $RESPONSE"; exit 1; }
log "/select_range response:"
printf '%s\n' "$RESPONSE" | tee -a "$LOG"

snapshot_buffers "after window"

COUNT="$(printf '%s\n' "$RESPONSE" | awk -F= '/^count=/{print $2}')"
ORA_CODE="$(printf '%s\n' "$RESPONSE" | awk -F= '/^ora_code=/{print $2}')"
if [[ -n "$ORA_CODE" ]]; then
    log "FAIL: Oracle returned ora_code=$ORA_CODE"
    exit 1
fi
log "SUMMARY: HHS001 rows written in window [$START_MK .. $END_MK] for STA $STA1/$STA2/$STA3 -> $COUNT"
exit 0
