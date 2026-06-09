#!/usr/bin/env bash
# connect-hime-h-reap.sh
# 工場WiFi「HIME-H-REAP」に接続して、繋ぎっぱなしで記録を続ける。
#   - 自動接続はしない（このファイルを実行したときだけ繋ぐ）
#   - 60秒で戻る live テスト(live_himereap_run.sh)とは違い、自動で元に戻さない
#   - 戻すときは Desktop の disconnect-hime-h-reap.sh を実行
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

PROFILE_NAME="${PROFILE_NAME:-HIME-H-REAP}"          # = profiles.yaml のキー = 実SSID
PROFILES_YAML="${PROFILES_YAML:-/etc/presence-logger/profiles.yaml}"
SECRETS_ENV="${SECRETS_ENV:-/etc/presence-logger/secrets.env}"
CONN_NAME="${CONN_NAME:-HIME-H-REAP}"                # nmcli 接続名（liveの "-live" とは別）
IFNAME="${IFNAME:-wlan0}"

say(){ printf '%s\n' "$*"; }
finish(){ [[ "$HOLD" == 1 ]] && { echo; read -rp 'Enterキーで閉じる... ' _; }; exit "${1:-0}"; }

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

[[ -n "${PCFG[psk]:-}" ]] || { say "FAIL: WIFI_PSK_HIMEREAP が secrets.env にありません"; finish 1; }
[[ -n "${PCFG[ip]:-}"  ]] || { say "FAIL: 静的IPが profiles.yaml にありません"; finish 1; }

# --- 失敗時に戻れるよう、今の接続を覚えておく ---
PREV="$(nmcli -t -f NAME connection show --active 2>/dev/null | head -1)"
say "現在の接続: ${PREV:-(なし)}"
say "→ $PROFILE_NAME に切り替えます（繋ぎっぱなし）"
say ""

# --- 規制ドメインを JP に（隠しSSIDのチャンネル用。起動時は 00 に戻るため毎回設定）---
iw reg set JP 2>/dev/null || true

# --- 永続 nmcli プロファイルを作り直す（autoconnect no）---
if nmcli -t -f NAME connection show | grep -Fxq "$CONN_NAME"; then
    nmcli connection delete "$CONN_NAME" >/dev/null 2>&1 || true
fi
nmcli connection add type wifi con-name "$CONN_NAME" ifname "$IFNAME" \
    ssid "$PROFILE_NAME" \
    802-11-wireless.hidden "${PCFG[hidden]}" \
    802-11-wireless-security.key-mgmt wpa-psk \
    802-11-wireless-security.psk "${PCFG[psk]}" \
    ipv4.method manual \
    ipv4.addresses "${PCFG[ip]}" \
    ipv4.gateway "${PCFG[gw]}" \
    ipv4.dns "${PCFG[dns]}" \
    ipv6.method disabled \
    connection.autoconnect no >/dev/null \
    || { say "FAIL: nmcli 接続の作成に失敗"; finish 1; }

# --- 接続（最大25秒待つ）---
say "接続中..."
if nmcli --wait 25 connection up "$CONN_NAME" >/dev/null 2>&1; then
    ACTUAL="$(nmcli -t -f ACTIVE,SSID dev wifi | awk -F: '$1=="yes"{print $2; exit}')"
    if [[ "$ACTUAL" == "$PROFILE_NAME" ]]; then
        # まず時刻同期（工場NTP 133.141.247.101 へ即時同期させ、最大15秒待つ）
        say "    時刻同期中（NTP 133.141.247.101）..."
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
        # 次に検知を開始（detector コンテナ起動 = カメラ取得＋ENTER/EXIT判定）
        say "    検知を開始します（detector 起動中...）"
        docker start presence-detector >/dev/null 2>&1 \
            || docker compose --project-directory /home/pi/projects/presence-logger up -d detector >/dev/null 2>&1 \
            || say "    ⚠ detector の起動に失敗（docker を確認してください）"
        say ""
        say "===================================================="
        say " ✅ HIME-H-REAP 接続＋時刻同期＋検知を開始しました"
        say "    SSID : $ACTUAL    IP : ${PCFG[ip]}"
        say "===================================================="
        say ""
        say " detector がカメラ判定を開始 → bridge が HHC001 に記録します。"
        say " （カメラ起動に数秒・最初のMERGEまで最大5秒）"
        say " 通信が一時的に切れても検知は継続し、復旧後にまとめて記録されます。"
        say ""
        say " ◆ 記録確認:  Desktop の「記録モニタ」"
        say " ◆ 停止:      Desktop の「HIME-H-REAP を切断」（検知も止まります）"
        finish 0
    fi
fi

say ""
say " ❌ HIME-H-REAP に繋がりませんでした（電波圏外の可能性）。"
if [[ -n "$PREV" ]]; then
    say "    元の接続「$PREV」に戻します..."
    nmcli connection up "$PREV" >/dev/null 2>&1 || true
fi
finish 1
