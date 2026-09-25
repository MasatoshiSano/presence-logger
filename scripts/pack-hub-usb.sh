#!/usr/bin/env bash
# pack-hub-usb.sh — 親機から USB へ、2台目ハブ用のキットを書き出す。
#
#   bash scripts/pack-hub-usb.sh /media/pi/USB
#
# キットには親の子一覧・Oracle パスワード・AP パスワードを載せない。
set -uo pipefail

PACK_REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/kit-copy.sh
source "$PACK_REPO_DIR/scripts/lib/kit-copy.sh"

# pack_hub_kit が実際に読んだ secrets のパス。エラー文の案内に使う。
PACK_SECRETS_PATH="${PACK_SECRETS:-/etc/presence-logger/secrets.env}"

pack_rsync_excludes() {
    cat <<'EOF'
.venv
venv
site.env
wifi-switch.conf
fleet/children.conf
fleet/known_macs.json
.kit
*.db
*.db-wal
*.db-shm
*.db-journal
__pycache__
.pytest_cache
.ruff_cache
EOF
}

pack_hub_images() {
    cat <<'EOF'
eclipse-mosquitto:2
presence-logger-oracle-jdbc:latest
presence-logger-bridge:latest
EOF
}

pack_strip_secrets() {
    local re='^(ORACLE_PASSWORD_|WALLET_PASSWORD_|WIFI_AP_PSK=)'
    if [ -n "${1:-}" ] && [ -f "$1" ]; then
        grep -vE "$re" "$1" || true
    else
        grep -vE "$re" || true
    fi
}

# secrets.env は root:docker の 600。手順書どおり pi で走らせると読めない。
# ここを黙って素通りさせると、工場WiFi の PSK が載っていないキットが
# 「✅」付きで現場へ出て、3工程あと(新機が工場網へ繋がろうとしたとき)に
# 初めて失敗する。読めないときだけ sudo へ昇格して読み直す。
#
# 戻り値: 0=読めた(標準出力に中身) / 1=読めなかった / 2=ファイルが無い
pack_read_secrets() {
    local path="$1"
    [ -e "$path" ] || return 2
    if [ -r "$path" ] && cat "$path" 2>/dev/null; then
        return 0
    fi
    echo "secrets.env は root しか読めません: $path" >&2
    if [ "$(id -u)" -eq 0 ]; then
        echo "  root なのに読めませんでした。ファイルの状態を確認してください" >&2
        return 1
    fi
    if ! command -v sudo >/dev/null 2>&1; then
        echo "  sudo がありません。root で実行し直してください" >&2
        return 1
    fi
    # まず聞かずに試す。sudo が NOPASSWD かキャッシュ済みならここで通る。
    sudo -n cat "$path" 2>/dev/null && return 0
    # 端末が無い場所(テスト・cron・ランチャー経由)でパスワードを聞くと
    # 応答できないまま固まる。聞けるときだけ聞く。
    if [ ! -t 0 ] || [ ! -t 2 ]; then
        echo "  端末が無いためパスワードを聞けません。次で実行し直してください:" >&2
        echo "    sudo bash $PACK_REPO_DIR/scripts/pack-hub-usb.sh <USBのマウント先>" >&2
        return 1
    fi
    echo "  管理者権限で読み直します（パスワードを聞かれます）" >&2
    sudo -p "  [sudo] %p のパスワード: " cat "$path" && return 0
    echo "  読めませんでした。sudo を通すか、root で実行し直してください:" >&2
    echo "    sudo bash $PACK_REPO_DIR/scripts/pack-hub-usb.sh <USBのマウント先>" >&2
    return 1
}

# 工場WiFi の PSK がテンプレに残っているかを見る。pack_strip_secrets は
# Oracle と旧AP のパスワードだけを落とすので、ここに WIFI_PSK_* が
# 1つも無いキットは「新機が工場網へ繋がれない」ことが確定している。
#
# 権限バグ(pack_read_secrets)とは別の関所。PSK が載らない経路は他にもある
# (親の secrets.env にそもそも無い / キー名が違う別工場 / strip の正規表現が
# 将来広がりすぎる)。原因ではなく結果を見るので、それら全部を受け止める。
#
# 既定は中止。現場へ着いてから「繋がらない」で気づくのが最悪なので、
# USB を書いた人の目の前で止める。PSK を意図的に載せない運用
# (別工場へ持っていく / 後から手で入れる)は PACK_ALLOW_NO_PSK=1 で明示する。
pack_check_factory_psk() {
    local tmpl="$1"
    grep -qE '^WIFI_PSK_' "$tmpl" 2>/dev/null && return 0
    if [ "${PACK_ALLOW_NO_PSK:-}" = "1" ]; then
        echo "⚠ 工場WiFi の PSK がキットにありません（PACK_ALLOW_NO_PSK=1 のため続行）" >&2
        echo "  新機では初期設定のあと、手で PSK を入れないと工場網へ繋がりません" >&2
        return 0
    fi
    echo "工場WiFi の PSK がキットに載りません（WIFI_PSK_* が1つもない）" >&2
    echo "  このまま渡すと、新機は工場網へ繋がれません。" >&2
    echo "  親機の $PACK_SECRETS_PATH に WIFI_PSK_* があるか確認してください。" >&2
    echo "  意図的に載せないなら: PACK_ALLOW_NO_PSK=1 bash $PACK_REPO_DIR/scripts/pack-hub-usb.sh <USBのマウント先>" >&2
    return 1
}

pack_copy_tree() {
    local src="$1" dst="$2"
    pack_rsync_excludes | kit_copy_tree_filtered "$src" "$dst"
}

pack_write_empty_children() {
    local repo="$1"
    mkdir -p "$repo/fleet"
    cat > "$repo/fleet/children.conf" <<'EOF'
# このハブの子Pi。親機の一覧はコピーしていない。
# デスクトップの「子をこのハブへ付ける」から追加する。
EOF
}

_pack_get() {
    local key="$1" file="$2" line
    line="$(grep -E "^${key}=" "$file" 2>/dev/null | head -1 || true)"
    line="${line#*=}"
    line="${line%%#*}"
    line="${line%"${line##*[![:space:]]}"}"
    line="${line#"${line%%[![:space:]]*}"}"
    if [ "${#line}" -ge 2 ]; then
        case "$line" in
            \"*\") line="${line:1:-1}" ;;
            \'*\') line="${line:1:-1}" ;;
        esac
    fi
    printf '%s\n' "$line"
}

_pack_origin_kv() {
    local k="$1" v="$2"
    if [[ "$v" == *" "* ]]; then
        printf '%s="%s"\n' "$k" "$v"
    else
        printf '%s=%s\n' "$k" "$v"
    fi
}

pack_write_origin() {
    local site="$1" dest="$2"
    mkdir -p "$(dirname "$dest")"
    {
        _pack_origin_kv ORIGIN_HOSTNAME "$(_pack_get HUB_HOSTNAME "$site")"
        _pack_origin_kv ORIGIN_FACTORY_IP "$(_pack_get FACTORY_IP "$site")"
        _pack_origin_kv ORIGIN_FACTORY_SSID "$(_pack_get FACTORY_SSID "$site")"
        _pack_origin_kv ORIGIN_FACTORY_GW "$(_pack_get FACTORY_GW "$site")"
        _pack_origin_kv ORIGIN_FACTORY_DNS "$(_pack_get FACTORY_DNS "$site")"
        _pack_origin_kv ORIGIN_FACTORY_SUBNETS "$(_pack_get FACTORY_SUBNETS "$site")"
        _pack_origin_kv ORIGIN_SNTP_SERVERS "$(_pack_get SNTP_SERVERS "$site")"
        _pack_origin_kv ORIGIN_AP_SSID "$(_pack_get AP_SSID "$site")"
        _pack_origin_kv ORIGIN_ORACLE_HOST "$(_pack_get ORACLE_HOST "$site")"
        _pack_origin_kv ORIGIN_ORACLE_PORT "$(_pack_get ORACLE_PORT "$site")"
        _pack_origin_kv ORIGIN_ORACLE_SERVICE "$(_pack_get ORACLE_SERVICE "$site")"
        _pack_origin_kv ORIGIN_ORACLE_USER "$(_pack_get ORACLE_USER "$site")"
        _pack_origin_kv ORIGIN_ORACLE_TABLE "$(_pack_get ORACLE_TABLE "$site")"
        _pack_origin_kv ORIGIN_PARENT_STA_NO1 "$(_pack_get PARENT_STA_NO1 "$site")"
        _pack_origin_kv ORIGIN_PARENT_STA_NO2 "$(_pack_get PARENT_STA_NO2 "$site")"
        _pack_origin_kv ORIGIN_PARENT_STA_NO3 "$(_pack_get PARENT_STA_NO3 "$site")"
    } > "$dest"
}

pack_blank_identity() {
    local src="$1" dest="$2"
    sed -e 's/^HUB_HOSTNAME=.*/HUB_HOSTNAME=/' \
        -e 's/^AP_SSID=.*/AP_SSID=/' \
        -e 's/^FACTORY_IP=.*/FACTORY_IP=/' \
        "$src" > "$dest"
}

# 親機でビルドした成果物は載せない。新機はカーネルが違うことがあり、make は
# ソースより新しい .o を見ると再コンパイルを省く。そうして出来た .ko は
# vermagic 不一致で読み込めず、原因が「USB に古い成果物が入っていた」ことだとは
# 分からない形で失敗する。容量も 55MB -> 15MB に落ちる。
pack_driver_excludes() {
    cat <<'EOF'
.git
*.o
*.ko
*.mod
*.mod.c
*.cmd
.*.cmd
*.a
*.symvers
Module.symvers
modules.order
.tmp_versions
EOF
}

# ドライバソースの場所。sudo で走らせると Debian の sudo は HOME を /root に
# 差し替えるので、${HOME}/8821au だけを見ると /home/pi/8821au を見失う。
# 呼び出したユーザ(SUDO_USER)のホームも見る。見つからなければ何も出さない。
pack_find_driver_src() {
    local user_home="" d
    if [ -n "${PACK_DRIVER_SRC-}" ]; then
        printf '%s\n' "$PACK_DRIVER_SRC"
        return 0
    fi
    if [ -n "${SUDO_USER:-}" ]; then
        user_home="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
    fi
    for d in "${PACK_SYSTEM_DRIVER_DIR:-/usr/local/src/8821au}" \
        "${user_home:+$user_home/8821au}" "${HOME:+$HOME/8821au}"; do
        [ -n "$d" ] && [ -d "$d" ] && { printf '%s\n' "$d"; return 0; }
    done
    return 0
}

# ドライバの無いキットは「成功」にしない。新機はフェーズ30 で GitHub から
# clone できないとドングルが使えないのに、以前は黙って ✅ まで進んでいた。
# 意図的に載せないとき(回線のある現場で作る等)だけ PACK_ALLOW_NO_DRIVER=1。
pack_check_driver() {
    local driver="${1:-}"
    [ -n "$driver" ] && [ -d "$driver" ] && return 0
    if [ "${PACK_ALLOW_NO_DRIVER:-}" = "1" ]; then
        echo "⚠ ドングルのドライバソースをキットに載せていません（PACK_ALLOW_NO_DRIVER=1 のため続行）" >&2
        echo "  新機のフェーズ30 は GitHub から clone します。インターネットが要ります。" >&2
        return 0
    fi
    echo "ドングルのドライバソース(8821au)が見つかりません。" >&2
    echo "  見た場所: /usr/local/src/8821au${SUDO_USER:+, ~$SUDO_USER/8821au}, ${HOME:-}/8821au" >&2
    echo "  場所を指定する: sudo PACK_DRIVER_SRC=/home/pi/8821au bash $PACK_REPO_DIR/scripts/pack-hub-usb.sh <USBのマウント先>" >&2
    echo "  載せずに作るなら: PACK_ALLOW_NO_DRIVER=1 を付ける" >&2
    return 1
}

pack_copy_driver() {
    local src="${1:-}" dest="$2"
    [ -n "$src" ] && [ -d "$src" ] || return 0
    pack_driver_excludes | kit_copy_tree_filtered "$src" "$dest"
}

pack_save_images() {
    local dest="$1" img resolved name
    mkdir -p "$dest"
    while IFS= read -r img; do
        [ -n "$img" ] || continue
        if docker image inspect "$img" >/dev/null 2>&1; then
            resolved="$img"
        elif docker image inspect "${img%:latest}" >/dev/null 2>&1; then
            resolved="${img%:latest}"
        else
            echo "イメージが見つかりません: $img （親で docker compose up --build 済みか確認）" >&2
            return 1
        fi
        name="${resolved##*/}"
        name="${name%%:*}"
        echo "==> docker save $resolved"
        docker save -o "$dest/${name}.tar" "$resolved" || return 1
    done < <(pack_hub_images)
}

pack_write_readme() {
    cat > "$1" <<'EOF'
presence-hub-kit — 2台目ハブの USB キット
========================================

この USB には親機の設定一式とコンテナイメージが入っています。
工場WiFi のパスワードも入っています。鍵と同じ扱いをし、使い終わったら消してください。
Oracle のパスワードと新しい AP のパスワードは載せていません（初期設定で聞きます）。

新機（Raspberry Pi OS Desktop が入った素の Pi）での手順:

1. この USB を挿す（下記の USB8G は実際の USB 名に置き換える）
   別のアプリが既に入っている Pi なら、先に点検する（何も変更しません）:
     bash /media/pi/USB8G/presence-hub-kit/preflight-new-hub.sh
   最後の行に「進めてはいけません」があれば、ここで止めて相談してください
2. ファイルマネージャで presence-hub-kit を開き、
   「このUSBからコピー」をダブルクリックする
   （開かないときはターミナルで）
     bash /media/pi/USB8G/presence-hub-kit/copy-to-this-pi.sh
3. デスクトップに「ハブ初期設定」が現れるのでクリックする
4. 工場の SSID・ゲートウェイ・Oracle なども順に答える（Enter でコピー元の値）
5. 終わったら再起動し、デスクトップの「子をこのハブへ付ける」で子を追加する

親機はそのまま運転したままで大丈夫です。新しいハブは別のホスト名・IP・AP名になります。
EOF
}

pack_write_copy_desktop() {
    cat > "$1" <<'EOF'
[Desktop Entry]
Type=Application
Version=1.0
Name=このUSBからコピー
Name[ja]=このUSBからコピー
Comment=この Raspberry Pi にハブ一式をコピーし、初期設定アイコンを置きます
Exec=lxterminal -t "ハブをコピー" -e bash -c "kit=$(find /media /run/media \"$HOME\" -name copy-to-this-pi.sh 2>/dev/null | head -1); if [ -z \"$kit\" ]; then echo USB の copy-to-this-pi.sh が見つかりません; else bash \"$kit\"; fi; echo; read -r -p 'Enterで閉じる '"
Icon=media-removable
Terminal=false
Categories=Utility;
EOF
}

pack_hub_kit() {
    local src="${1:-$PACK_REPO_DIR}" dest_root="${2:?USB のマウント先を指定してください}"
    local kit="$dest_root/presence-hub-kit"
    local site="${PACK_SITE_ENV:-$src/site.env}"
    local secrets="${PACK_SECRETS:-/etc/presence-logger/secrets.env}"
    local driver
    driver="$(pack_find_driver_src)"

    if [ ! -f "$site" ]; then
        echo "親の site.env がありません: $site" >&2
        echo "  親機で site.env を用意してから pack してください" >&2
        return 1
    fi
    pack_check_driver "$driver" || return 1

    mkdir -p "$kit/payload/presence-logger" "$kit/.kit"
    pack_copy_tree "$src" "$kit/payload/presence-logger"
    pack_write_empty_children "$kit/payload/presence-logger"
    pack_write_origin "$site" "$kit/.kit/origin.env"
    pack_blank_identity "$site" "$kit/.kit/site.env.template"
    local secrets_tmpl="$kit/.kit/secrets.env.template" secrets_raw secrets_rc
    # エラー文で実際に見にいったパスを出すため、関数の外からも参照できるようにする。
    PACK_SECRETS_PATH="$secrets"
    secrets_raw="$(pack_read_secrets "$secrets")"
    secrets_rc=$?
    case "$secrets_rc" in
        0)
            printf '%s\n' "$secrets_raw" | pack_strip_secrets > "$secrets_tmpl"
            ;;
        2)
            printf '# 親の secrets.env がありませんでした。工場WiFi の PSK は後で入れてください。\n' \
                > "$secrets_tmpl"
            ;;
        *)
            # 読めないまま続けると、中身が空のテンプレを載せた USB が
            # 「✅」付きで出来上がる。ここで止めるのが唯一の防波堤。
            echo "キットを作れませんでした（secrets.env を読めていません）" >&2
            return 1
            ;;
    esac
    chmod 600 "$secrets_tmpl" "$kit/.kit/origin.env" "$kit/.kit/site.env.template"
    pack_check_factory_psk "$secrets_tmpl" || return 1
    pack_copy_driver "$driver" "$kit/.kit/driver/8821au"
    pack_write_readme "$kit/README.txt"
    pack_write_copy_desktop "$kit/このUSBからコピー.desktop"
    cp "$PACK_REPO_DIR/scripts/copy-hub-from-usb.sh" "$kit/copy-to-this-pi.sh"
    cp "$PACK_REPO_DIR/scripts/preflight-new-hub.sh" "$kit/preflight-new-hub.sh"
    chmod 644 "$kit/このUSBからコピー.desktop" "$kit/copy-to-this-pi.sh" "$kit/README.txt" \
        "$kit/preflight-new-hub.sh"

    if [ "${PACK_SKIP_DOCKER:-}" = "1" ]; then
        echo "PACK_SKIP_DOCKER=1 のためイメージは載せていません"
    else
        pack_save_images "$kit/.kit/docker-images" || return 1
    fi

    echo "✅ USB キットを書きました: $kit"
    echo "   新機で copy-to-this-pi.sh を実行してください"
}

main() {
    local dest="${1:-}"
    if [ -z "$dest" ]; then
        echo "使い方: bash $0 /media/pi/USBのマウント先" >&2
        return 2
    fi
    pack_hub_kit "$PACK_REPO_DIR" "$dest"
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
