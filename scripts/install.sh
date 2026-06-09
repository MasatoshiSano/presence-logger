#!/usr/bin/env bash
# Idempotent installer for presence-logger. Run as root.
# - Creates required directories
# - Copies example configs if real configs are missing
# - Configures systemd-timesyncd with the consolidated SNTP server list
# - Sets strict permissions on secrets.env

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "must be run as root" >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ETC_DIR="/etc/presence-logger"
LIB_DIR="/var/lib/presence-logger"
LOG_DIR="/var/log/presence-logger"
WALLETS_DIR="$ETC_DIR/wallets"

mkdir -p "$ETC_DIR" "$LIB_DIR" "$LOG_DIR" "$WALLETS_DIR"
chmod 0755 "$ETC_DIR" "$LIB_DIR" "$LOG_DIR"
chmod 0700 "$WALLETS_DIR"

# Copy example configs only if no real config exists yet.
copy_if_missing() {
    local src="$1"
    local dst="$2"
    local mode="$3"
    if [[ ! -f "$dst" ]]; then
        cp "$src" "$dst"
        chmod "$mode" "$dst"
        chown root:root "$dst"
        echo "installed: $dst"
    else
        echo "exists, skipped: $dst"
    fi
}

copy_if_missing "$REPO_DIR/config/device.yaml.example"     "$ETC_DIR/device.yaml"     0644
copy_if_missing "$REPO_DIR/config/detector.yaml.example"   "$ETC_DIR/detector.yaml"   0644
copy_if_missing "$REPO_DIR/config/bridge.yaml.example"     "$ETC_DIR/bridge.yaml"     0644
copy_if_missing "$REPO_DIR/config/profiles.yaml.example"   "$ETC_DIR/profiles.yaml"   0640
copy_if_missing "$REPO_DIR/config/secrets.env.example"     "$ETC_DIR/secrets.env"     0600

# Build a consolidated SNTP NTP= line from profiles.yaml.
# We use python (already required by the host for installation context); keep this dependency-free.
SNTP_SERVERS=$(python3 - <<'PY' "$ETC_DIR/profiles.yaml"
import sys, yaml
data = yaml.safe_load(open(sys.argv[1]))
seen = []
for p in (data.get("profiles") or {}).values():
    for s in (p.get("sntp", {}).get("servers") or []):
        if s not in seen:
            seen.append(s)
print(" ".join(seen))
PY
)

# Public NTP servers appended to the NTP= line (NOT FallbackNTP=). timesyncd
# only consults FallbackNTP= when NTP= is empty, so to get a real fallback on
# WiFis where the factory-internal SNTP is unreachable we must list public
# servers in NTP= itself. Order matters: profile (factory-internal) servers
# come first and win on the closed network; these are tried only on timeout.
PUBLIC_NTP_FALLBACK="ntp.nict.jp time.cloudflare.com"

if [[ -n "$SNTP_SERVERS" ]]; then
    cat >/etc/systemd/timesyncd.conf <<EOF
[Time]
NTP=$SNTP_SERVERS $PUBLIC_NTP_FALLBACK
FallbackNTP=$PUBLIC_NTP_FALLBACK
EOF
    systemctl restart systemd-timesyncd
    echo "configured timesyncd with: $SNTP_SERVERS $PUBLIC_NTP_FALLBACK"
else
    cat >/etc/systemd/timesyncd.conf <<EOF
[Time]
NTP=$PUBLIC_NTP_FALLBACK
EOF
    systemctl restart systemd-timesyncd
    echo "no SNTP servers in profiles.yaml; timesyncd configured with public NTP only: $PUBLIC_NTP_FALLBACK"
fi

# Install the systemd unit if present.
if [[ -f "$REPO_DIR/systemd/presence-logger.service" ]]; then
    cp "$REPO_DIR/systemd/presence-logger.service" /etc/systemd/system/presence-logger.service
    systemctl daemon-reload
    echo "installed systemd unit: /etc/systemd/system/presence-logger.service"
fi

echo
echo "Install complete. Edit configs in $ETC_DIR, then:"
echo "  systemctl enable --now presence-logger.service"
