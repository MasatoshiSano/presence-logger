#!/usr/bin/env bash
# prepare-child-sd.sh — クローンした子PiのSDに、このハブの公開鍵と AP 参加情報を書く。
#
# 旧親が止まっている・別工場網だと、フリート管理の「他のハブから引き継ぐ」は
# 工場網経由で旧親へ SSH できない。子のSDを手元で直すのが残る道。
# ホスト名と id_names_config.json（局番号）は触らない。
set -uo pipefail

PREPARE_REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/site-env.sh
source "$PREPARE_REPO_DIR/scripts/lib/site-env.sh"

child_sd_find_root() {
    local base="${1:-/media}"
    local f
    f="$(find "$base" -maxdepth 6 -type f -path '*/home/pi/id_names_config.json' 2>/dev/null | head -1 || true)"
    [ -n "$f" ] || return 1
    dirname "$(dirname "$(dirname "$f")")"
}

child_sd_is_child_root() {
    local root="${1:?}"
    if [ ! -f "$root/home/pi/id_names_config.json" ]; then
        echo "子PiのSDではありません（id_names_config.json が無い）: $root" >&2
        return 1
    fi
    if [ -f "$root/etc/presence-logger/profiles.yaml" ]; then
        echo "これはハブのSDです。子Piのカードを挿してください: $root" >&2
        return 1
    fi
    return 0
}

child_sd_install_pubkey() {
    local root="${1:?}" pub="${2:?}" dest owner_uid
    [ -f "$pub" ] || { echo "公開鍵がありません: $pub" >&2; return 1; }
    mkdir -p "$root/home/pi/.ssh"
    dest="$root/home/pi/.ssh/authorized_keys"
    touch "$dest"
    if grep -qxF "$(tr -d '\r' < "$pub" | head -1)" "$dest" 2>/dev/null; then
        echo "公開鍵は既に入っています"
    else
        cat "$pub" >> "$dest"
        echo "公開鍵を authorized_keys に足しました"
    fi
    chmod 700 "$root/home/pi/.ssh"
    chmod 600 "$dest"
    owner_uid="$(stat -c %u "$root/home/pi" 2>/dev/null || echo 1000)"
    chown "$owner_uid:$owner_uid" "$root/home/pi/.ssh" "$dest" 2>/dev/null || true
}

# NetworkManager の keyfile(GLib key-file) は引用符を特別扱いしない。囲むと引用符ごと
# SSID/PSK になり、子は存在しない AP を探し続ける(2026-09-25 に発症)。値の途中の # は
# コメントにならない。書式は `nmcli --offline connection add` の出力に合わせる。
# 規則は fleet_ui/migrate.py と同じ(別実装)。テストベクタは scripts/tests/nm_vectors.py。
# 設計: docs/2026-09-29-child-join-fix-design.md §1.2

# 0=OK。WPA-PSK: 表示可能ASCII 8..63 文字、または16進64文字。
child_sd_psk_valid() {
    local LC_ALL=C v="${1:-}"
    [[ ${#v} -eq 64 && "$v" =~ ^[0-9A-Fa-f]+$ ]] && return 0
    (( ${#v} >= 8 && ${#v} <= 63 )) || return 1
    [[ "$v" =~ ^[\ -~]+$ ]]
}

# バックスラッシュは二重に、先頭の空白は1文字ごとに \s (NM は先頭空白を黙って落とす)。
child_sd_nm_escape_psk() {
    local v="${1:-}" lead=""
    v="${v//\\/\\\\}"
    while [ "${v:0:1}" = " " ]; do lead+='\s'; v="${v:1}"; done
    printf '%s%s' "$lead" "$v"
}

# 1..32 バイトでなければ非0。素直な ASCII は文字列形式(; だけ二重バックスラッシュ付き)、
# それ以外は NM 自身と同じバイト列形式(10進を ; 区切り、末尾にも ;)。
child_sd_nm_ssid() {
    local LC_ALL=C v="${1:-}" n
    n=${#v}
    (( n >= 1 && n <= 32 )) || return 1
    if [[ "$v" =~ ^[!-~]([\ -~]*[!-~])?$ && "$v" != *\\* ]]; then
        printf '%s' "${v//;/\\\\;}"
    else
        printf '%s' "$v" | od -An -v -tu1 | tr -s ' \n' '\n\n' | grep . | tr '\n' ';'
    fi
}

# 書き込み前の旧ファイルが引用符つき(不具合版の書式)なら知らせる。上書きで直る。
child_sd_report_legacy_quotes() {
    local f="${1:?}"
    [ -f "$f" ] || return 0
    if grep -qE '^(ssid|psk)=".*"$' "$f"; then
        echo "以前の書式（引用符つき）で書かれていたので直しました。この子は以前このハブに繋がれなかったはずです。"
    fi
}

child_sd_write_wifi() {
    local root="${1:?}" ssid="${2:?}" psk="${3:?}"
    local dir="$root/etc/NetworkManager/system-connections"
    local dest="$dir/presence-hub-join.nmconnection"
    local uuid qssid qpsk
    # 書く前に検証する。外れた値は AP 側でも使えず、ファイルを壊すだけ。PSK の値は表示しない。
    child_sd_psk_valid "$psk" || {
        echo "このハブの AP パスワードは子Pi が使えない形式です(8〜63文字の半角英数記号、または16進64文字)" >&2
        return 1
    }
    qssid="$(child_sd_nm_ssid "$ssid")" || {
        echo "AP 名の長さが不正です(1〜32バイト)" >&2
        return 1
    }
    qpsk="$(child_sd_nm_escape_psk "$psk")"
    mkdir -p "$dir"
    child_sd_report_legacy_quotes "$dest"
    uuid="$(cat /proc/sys/kernel/random/uuid 2>/dev/null || python3 -c 'import uuid; print(uuid.uuid4())')"
    umask 077
    cat > "$dest" <<EOF
[connection]
id=presence-hub-join
uuid=$uuid
type=wifi
autoconnect=true
autoconnect-priority=200
# 0 = 無限に再試行する。既定の4回で諦めると、電波が正しく戻っても
# 子は自力で復帰せず、物理的な電源再投入が要る(2026-09-23 に2台で発生)。
autoconnect-retries=0

[wifi]
mode=infrastructure
ssid=$qssid

[wifi-security]
key-mgmt=wpa-psk
psk=$qpsk

[ipv4]
method=auto

[ipv6]
method=ignore
EOF
    chmod 600 "$dest"
    chown 0:0 "$dest" 2>/dev/null || true
}

# 旧ハブの Wi-Fi プロファイルが残ると、同居時にクローンが古い AP へ戻る。
child_sd_disable_other_wifi() {
    local root="${1:?}"
    local dir="$root/etc/NetworkManager/system-connections"
    local f tmp
    [ -d "$dir" ] || return 0
    shopt -s nullglob
    for f in "$dir"/*.nmconnection; do
        [ "$(basename "$f")" = "presence-hub-join.nmconnection" ] && continue
        grep -qE '^type=(wifi|802-11-wireless)$' "$f" || continue
        tmp="$(mktemp)"
        awk '
            BEGIN { in_c=0; seen=0 }
            /^\[connection\]/ { in_c=1; print; next }
            /^\[/ {
                if (in_c && !seen) print "autoconnect=false"
                in_c=0
            }
            in_c && /^autoconnect=/ { print "autoconnect=false"; seen=1; next }
            { print }
            END { if (in_c && !seen) print "autoconnect=false" }
        ' "$f" > "$tmp" && cat "$tmp" > "$f"
        rm -f "$tmp"
        chmod 600 "$f" 2>/dev/null || true
    done
}

# このハブの AP_GW_IP(site.env)。無ければ既定。IPv4 でなければ非0。
# fleet_ui/send_target.py の load_ap_gw_ip と同じ規則(別実装)。
child_sd_hub_gw_ip() {
    local repo="${1:?}" f v=""
    f="$repo/site.env"
    if [ -f "$f" ]; then
        v="$(grep -E '^AP_GW_IP=' "$f" | tail -1 | cut -d= -f2-)"
        v="${v%$'\r'}"
        if [[ ${#v} -ge 2 && "${v:0:1}" == "${v: -1}" && ( "${v:0:1}" == '"' || "${v:0:1}" == "'" ) ]]; then
            v="${v:1:${#v}-2}"
        fi
    fi
    [ -n "$v" ] || v=10.42.0.1
    if [[ "$v" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] && _site_env_ipv4_octets_valid "$v"; then
        printf '%s\n' "$v"
        return 0
    fi
    echo "site.env の AP_GW_IP が IPv4 ではありません: $v" >&2
    return 1
}

# 記録の送り先を付け替え先ハブの AP_GW_IP に揃える。子の記録送信は2経路あり、
#   - カメラ内蔵の送信: ~/send_target_config.json の host
#   - child-csv-to-mqtt(ACK付き): 環境変数 MQTT_HOST(JSON は読まない) ← systemd drop-in で渡す
# 片方だけ変えるともう片方が旧 IP へ送り続ける。既定(10.42.0.1)のときは drop-in を消す。
# JSON は host 以外のキー(password を含む)を変えない。壊れた JSON は上書きせずエラー。
# 設計: docs/2026-09-29-child-join-fix-design.md §2.4 (ii)
# 何かを書く前に呼ぶ。send_target_config.json が壊れている(または object でない)なら失敗する。
# 無ければ問題なし。ここで止めれば、SD 上のネットワーク設定を半端に変えたまま残さない。
child_sd_check_send_target() {
    local root="${1:?}"
    python3 - "$root/home/pi/send_target_config.json" <<'PY' || return 1
import json
import os
import sys

p = sys.argv[1]
if not os.path.exists(p):
    sys.exit(0)
try:
    with open(p, encoding="utf-8") as fh:
        cfg = json.load(fh)
except (ValueError, OSError):
    sys.exit("send_target_config.json が壊れているので書き換えません。SD は何も変更していません")
if not isinstance(cfg, dict):
    sys.exit("send_target_config.json が壊れているので書き換えません(オブジェクトではない)。"
             "SD は何も変更していません")
PY
}

child_sd_align_send_target() {
    local root="${1:?}" gw="${2:?}"
    local f="$root/home/pi/send_target_config.json"
    local dropin="$root/etc/systemd/system/child-csv-to-mqtt.service.d/10-hub-gw.conf"
    if ! [[ "$gw" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || ! _site_env_ipv4_octets_valid "$gw"; then
        echo "AP_GW_IP が IPv4 ではありません: $gw" >&2
        return 1
    fi
    python3 - "$f" "$gw" <<'PY' || return 1
import json
import os
import sys
import tempfile

p, gw = sys.argv[1], sys.argv[2]
try:
    st = os.stat(p)
except FileNotFoundError:
    cfg = {}
    mode = 0o644
    d = os.stat(os.path.dirname(p))
    uid, gid = d.st_uid, d.st_gid
else:
    try:
        with open(p, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except ValueError:
        sys.exit("send_target_config.json が壊れているので書き換えません")
    if not isinstance(cfg, dict):
        sys.exit("send_target_config.json が壊れているので書き換えません(オブジェクトではない)")
    mode, uid, gid = st.st_mode & 0o777, st.st_uid, st.st_gid
old = cfg.get("host", "")
cfg["host"] = gw
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p))
try:
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.chmod(tmp, mode)
    os.chown(tmp, uid, gid)
    os.replace(tmp, p)
except BaseException:
    if os.path.exists(tmp):
        os.unlink(tmp)
    raise
print(f"  記録の送り先: {old or '(未設定)'} → {gw}")
PY
    if [ "$gw" = 10.42.0.1 ]; then
        rm -f "$dropin"
    else
        mkdir -p "$(dirname "$dropin")" || return 1
        printf '[Service]\nEnvironment=MQTT_HOST=%s\n' "$gw" > "$dropin" || return 1
        chmod 644 "$dropin"
    fi
}

child_sd_prepare() {
    local root="${1:?}" pub="${2:?}" ssid="${3:?}" psk="${4:?}"
    local gw="${5:-10.42.0.1}"
    child_sd_is_child_root "$root" || return 1
    child_sd_check_send_target "$root" || return 1  # 何か書く前に JSON を検査
    child_sd_install_pubkey "$root" "$pub" || return 1
    child_sd_write_wifi "$root" "$ssid" "$psk" || return 1
    child_sd_disable_other_wifi "$root" || return 1
    child_sd_align_send_target "$root" "$gw" || return 1
}

main() {
    local root="${1:-}" gw="${2:-}" pub ssid psk
    pub="${CHILD_SD_PUBKEY:-$HOME/.ssh/id_ed25519.pub}"
    if [ -z "$root" ]; then
        root="$(child_sd_find_root /media)" || {
            echo "子PiのSDが見つかりません。USBカードリーダで挿してから再実行するか、" >&2
            echo "  bash scripts/prepare-child-sd.sh /media/pi/rootfs" >&2
            echo "のようにマウント先を渡してください。" >&2
            return 1
        }
    fi
    if [[ "$root" != /* || "$root" == *..* ]]; then
        echo "マウント先が不正です: $root" >&2
        return 1
    fi
    child_sd_is_child_root "$root" || return 1

    ssid=""
    psk=""
    if [ -f "$PREPARE_REPO_DIR/.kit/ap-join.env" ]; then
        ssid="$(grep -E '^AP_SSID=' "$PREPARE_REPO_DIR/.kit/ap-join.env" | head -1 | cut -d= -f2-)"
        psk="$(grep -E '^WIFI_AP_PSK=' "$PREPARE_REPO_DIR/.kit/ap-join.env" | head -1 | cut -d= -f2-)"
    fi
    if [ -z "$ssid" ] || [ -z "$psk" ]; then
        echo "このハブの AP 名またはパスワードが読めません。.kit/ap-join.env を確認してください。" >&2
        return 1
    fi
    if [ ! -f "$pub" ]; then
        echo "このハブの公開鍵がありません: $pub" >&2
        echo "  ssh-keygen -t ed25519 を実行してください。" >&2
        return 1
    fi

    # 記録の送り先はこのハブの AP_GW_IP に揃える。値は1回だけ読み、sudo の再実行には引数で渡す。
    if [ -z "$gw" ]; then
        gw="$(child_sd_hub_gw_ip "$PREPARE_REPO_DIR")" || return 1
    fi

    echo "子PiのSD: $root"
    echo "  ホスト名と局番号はそのままにします。"
    echo "  このハブの公開鍵と AP（$ssid）を書き込みます。"
    echo "  記録の送り先は、このハブの $gw に揃えます。"
    if [ "$(id -u)" -ne 0 ]; then
        # sudo すると HOME が /root になる。公開鍵のパスと HOME を明示して渡す。
        sudo env CHILD_SD_PUBKEY="$pub" HOME="$HOME" \
            bash "$PREPARE_REPO_DIR/scripts/prepare-child-sd.sh" "$root" "$gw" || return 1
        echo
        echo "SD を外して子Pi に挿し、電源を入れてください。"
        echo "起動したらデスクトップの「子をこのハブへ付ける」を開き、"
        echo "  1) すでに動いている子を移す → 3) 子はもうこのハブの AP に繋がっている"
        echo "を選んでください（トップの番号 3 はありません）。"
        echo "2) 新しい子を増やす は局番号が空になり名前が変わるので、既存の子には使わないでください。"
        read -r -p "Enterで閉じる " _
        return 0
    fi
    child_sd_prepare "$root" "$pub" "$ssid" "$psk" "$gw" || return 1
    echo "✅ 書き込みました。SD を外して子Pi に挿してください。"
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
