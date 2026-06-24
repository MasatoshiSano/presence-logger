#!/usr/bin/env bash
# connect-taden-ot-ap.sh
# 内蔵WiFi(wlan0)で工場WiFi「taden-ot-ap」に接続し、繋ぎっぱなしで記録を続ける。
#   - taden-ot-ap は MAC 許可制で、登録済みの内蔵WiFi(wlan0)の MAC でしか繋がらない。
#     そのため工場側は wlan0、インターネット(スマホ網)はドングル wlan1 に割り当てる（役割入れ替え）。
#   - インターネット(wlan1)の接続はそのまま維持する（2系統 同時接続）。
#   - wlan0 にはデフォルト経路を持たせない（never-default）。工場の宛先だけ wlan0 経由:
#       Oracle 10.168.252.0/24 と DNS/NTP 192.168.250.0/24 → via ゲートウェイ(dev wlan0)
#     → インターネット=wlan1(ドングル) / 工場(Oracle・NTP)=wlan0(内蔵) が同時に成立する。
#   - 自動接続はしない（このファイルを実行したときだけ繋ぐ）。戻すときは切断スクリプト。
#
# PSK はこのファイルには書かない。実行時に root で secrets.env から読む。
set -uo pipefail

# root で実行（nmcli と secrets.env(0600) の読み取りに必要）。
# pi で起動されたら sudo で同じ引数のまま再実行する（パスワードを聞かれる）。
if [[ $EUID -ne 0 ]]; then
    exec sudo bash "$0" "$@"
fi

HOLD=0
[[ "${1:-}" == "--hold" ]] && HOLD=1   # デスクトップ起動時に最後で一時停止する

PROFILE_NAME="${PROFILE_NAME:-taden-ot-ap}"          # = profiles.yaml のキー = 実SSID
PROFILES_YAML="${PROFILES_YAML:-/etc/presence-logger/profiles.yaml}"
SECRETS_ENV="${SECRETS_ENV:-/etc/presence-logger/secrets.env}"
CONN_NAME="${CONN_NAME:-taden-ot-ap}"                # nmcli 接続名
IFNAME="${IFNAME:-wlan0}"                            # 内蔵WiFi（taden-ot-ap の許可済みMAC）
# 工場側の宛先サブネット（これらだけ wlan0 経由。デフォルト経路=インターネットは wlan1 のまま）
FACTORY_SUBNETS="${FACTORY_SUBNETS:-10.168.252.0/24 192.168.250.0/24}"

say(){ printf '%s\n' "$*"; }
finish(){ [[ "$HOLD" == 1 ]] && { echo; read -rp 'Enterキーで閉じる... ' _; }; exit "${1:-0}"; }

# --- 接続に使う内蔵WiFi(wlan0)の存在確認 ---
if ! ip link show "$IFNAME" >/dev/null 2>&1; then
    say "FAIL: $IFNAME が見つかりません（内蔵WiFiが無効化されていないか確認してください）。"
    finish 1
fi

# --- profiles.yaml + secrets.env から接続情報を取得（PSKは画面に出さない）---
declare -A PCFG
raw="$(python3 - "$PROFILES_YAML" "$PROFILE_NAME" "$SECRETS_ENV" <<'PY'
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
data = yaml.safe_load(open(profiles_path))
p = expand((data.get("profiles") or {}).get(name) or {})
if not p:
    print(f"ERR no such profile: {name}", file=sys.stderr); sys.exit(2)
w = p.get("wifi") or {}; s = w.get("static_ipv4") or {}
def emit(k, v): print(f"PCFG[{k}]={str(v)!r}")
emit("psk", w.get("psk", ""))
emit("hidden", "yes" if w.get("hidden") else "no")
emit("ip", s.get("address", ""))
emit("gw", s.get("gateway", ""))
emit("dns", " ".join(s.get("dns") or []))
PY
)" || { say "FAIL: profiles.yaml を読めませんでした"; finish 1; }
eval "$raw"

[[ -n "${PCFG[psk]:-}" ]] || { say "FAIL: WIFI_PSK_TADEN が secrets.env にありません"; finish 1; }
[[ -n "${PCFG[ip]:-}"  ]] || { say "FAIL: 静的IPが profiles.yaml にありません"; finish 1; }
[[ -n "${PCFG[gw]:-}"  ]] || { say "FAIL: ゲートウェイが profiles.yaml にありません"; finish 1; }

say "→ $PROFILE_NAME に接続します（wlan0。インターネットは wlan1/ドングルのまま維持）"
say ""

# --- 工場宛ルートを ipv4.routes 形式 "dst gw, dst gw, ..." で組み立てる ---
ROUTES=""
for sub in $FACTORY_SUBNETS; do
    ROUTES+="${ROUTES:+, }$sub ${PCFG[gw]}"
done

# --- 規制ドメインを JP に（隠しSSIDのチャンネル用。起動時は 00 に戻るため毎回設定）---
iw reg set JP 2>/dev/null || true

# --- 永続 nmcli プロファイル: 既存なら modify（seen-bssids の記憶を残す）/ 無ければ add ---
# wlan0 専用・never-default（デフォルト経路を奪わない）・工場サブネットのみ個別ルート・
# DNS は付けない（宛先は全部IP。インターネットのDNSは wlan1 のまま）。
NM_ARGS=(
    802-11-wireless.ssid "$PROFILE_NAME"
    802-11-wireless.hidden "${PCFG[hidden]}"
    802-11-wireless-security.key-mgmt wpa-psk
    802-11-wireless-security.psk "${PCFG[psk]}"
    connection.interface-name "$IFNAME"
    ipv4.method manual
    ipv4.addresses "${PCFG[ip]}"
    ipv4.gateway ""
    ipv4.never-default yes
    ipv4.routes "$ROUTES"
    ipv4.dns ""
    ipv4.ignore-auto-dns yes
    ipv6.method disabled
    connection.autoconnect no
)
if nmcli -t -f NAME connection show | grep -Fxq "$CONN_NAME"; then
    nmcli connection modify "$CONN_NAME" "${NM_ARGS[@]}" >/dev/null \
        || { say "FAIL: nmcli 接続の更新に失敗"; finish 1; }
else
    nmcli connection add type wifi con-name "$CONN_NAME" ifname "$IFNAME" \
        "${NM_ARGS[@]}" >/dev/null \
        || { say "FAIL: nmcli 接続の作成に失敗"; finish 1; }
fi

# --- 接続前に scan cache を更新（古い cache だと「見つかりません」になることがある）---
say "電波スキャン中..."
nmcli device wifi rescan ifname "$IFNAME" >/dev/null 2>&1 || true
sleep 3

# --- 接続（最大60秒待つ）。wlan0 に固定IPが付いたかで成否を判定 ---
say "接続中..."
if nmcli --wait 60 connection up "$CONN_NAME" ifname "$IFNAME" >/dev/null 2>&1 \
   && ip -4 addr show "$IFNAME" 2>/dev/null | grep -q "${PCFG[ip]%%/*}"; then
    # まず時刻同期（工場NTP 192.168.250.1 へ即時同期させ、最大15秒待つ）
    say "    時刻同期中（NTP 192.168.250.1）..."
    systemctl restart systemd-timesyncd 2>/dev/null || true
    synced="no"
    for _i in $(seq 1 15); do
        [[ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" == "yes" ]] && { synced="yes"; break; }
        sleep 1
    done
    if [[ "$synced" == "yes" ]]; then
        say "    ✅ 時刻同期 完了（$(date '+%Y-%m-%d %H:%M:%S')）"
    else
        say "    ⚠ 時刻同期はまだ（背後で継続。記録は後で自動補正されます）"
    fi
    # 次にコンテナを起動（土台 mosquitto/oracle-jdbc/bridge ＋ detector）。
    # docker compose up -d は冪等：既に動いているものはそのまま、止まっているものだけ起動。
    say "    コンテナを起動します（mosquitto / oracle-jdbc / bridge / detector）..."
    docker compose --project-directory /home/pi/projects/presence-logger up -d >/dev/null 2>&1 \
        || say "    ⚠ コンテナの起動に失敗（docker を確認してください）"
    say ""
    say "===================================================="
    say " ✅ taden-ot-ap 接続＋時刻同期＋検知を開始しました"
    say "    IF : $IFNAME    IP : ${PCFG[ip]}    （インターネットは wlan1/ドングルで継続）"
    say "===================================================="
    say ""
    say " detector がカメラ判定を開始 → bridge が HHS001 に記録します。"
    say " （カメラ起動に数秒・最初のMERGEまで最大5秒）"
    say " 通信が一時的に切れても検知は継続し、復旧後にまとめて記録されます。"
    say ""
    say " ◆ 記録確認:  Desktop の「記録モニタ」"
    say " ◆ 停止:      Desktop の「taden-ot-ap を切断」（検知も止まります）"
    finish 0
fi

say ""
say " ❌ taden-ot-ap に繋がりませんでした（圏外 / ドングル / PSK を確認してください）。"
say "    インターネット(wlan1/ドングル)はそのまま使えています。"
nmcli connection down "$CONN_NAME" >/dev/null 2>&1 || true
finish 1
