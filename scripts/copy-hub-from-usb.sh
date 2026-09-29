#!/usr/bin/env bash
# copy-hub-from-usb.sh — USB キットをこの Pi のホームへコピーし、初期設定アイコンを置く。
#
# コピーの後、画面で操作しているときは事前点検をしてから、ハブ初期設定をそのまま始める
# （COPY_NO_WIZARD=1 で止められる）。
# USB 上では copy-to-this-pi.sh という名前でも同じファイル。
# FAT は実行ビットを落とすので、必ず `bash このファイル` で呼ぶ。
set -uo pipefail

COPY_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# USB 上ではキット直下、リポジトリでは scripts/ 配下。
if [ -f "$COPY_HERE/lib/kit-copy.sh" ]; then
    # shellcheck source=scripts/lib/kit-copy.sh
    source "$COPY_HERE/lib/kit-copy.sh"
elif [ -f "$COPY_HERE/scripts/lib/kit-copy.sh" ]; then
    # shellcheck source=scripts/lib/kit-copy.sh
    source "$COPY_HERE/scripts/lib/kit-copy.sh"
elif [ -f "$COPY_HERE/payload/presence-logger/scripts/lib/kit-copy.sh" ]; then
    # shellcheck source=scripts/lib/kit-copy.sh
    source "$COPY_HERE/payload/presence-logger/scripts/lib/kit-copy.sh"
fi

copy_find_kit() {
    local root="${1:-/media}"
    local origin
    origin="$(find "$root" -maxdepth 5 -type f -path '*/presence-hub-kit/.kit/origin.env' 2>/dev/null | head -1 || true)"
    [ -n "$origin" ] || return 1
    dirname "$(dirname "$origin")"
}

copy_fix_permissions() {
    local dest="$1" user="${2:-}"
    find "$dest" -type f \( -name '*.sh' -o -name '*.desktop' -o -name '*.py' \) \
        -exec chmod a+x {} + 2>/dev/null || true
    if [ -n "$user" ] && [ "$(id -u)" -eq 0 ]; then
        chown -R "$user:$user" "$dest"
    fi
}

copy_trust_desktop() {
    local f="$1"
    chmod a+x "$f" 2>/dev/null || true
    if command -v gio >/dev/null 2>&1; then
        gio set "$f" metadata::trusted true 2>/dev/null || true
    fi
}

copy_install_setup_icon() {
    local dest="$1" desk="$2"
    local tmpl="$dest/desktop/launchers/ハブ初期設定.desktop"
    mkdir -p "$desk"
    if [ ! -f "$tmpl" ]; then
        echo "ハブ初期設定.desktop がキットにありません" >&2
        return 1
    fi
    sed -e "s|__REPO_DIR__|$dest|g" -e "s|__TOOLS_DIR__|$desk/presence-tools|g" \
        "$tmpl" > "$desk/ハブ初期設定.desktop"
    copy_trust_desktop "$desk/ハブ初期設定.desktop"
}

# $1: コピー先。無い・空・presence-logger なら 0。別物なら理由を出して 1。
# 印は preflight_repo_verdict と同じ3点（docker-compose.yml / services/bridge / scripts/bootstrap-hub.sh）。
copy_dest_verdict() {
    local dir="$1" listing
    [ -e "$dir" ] || [ -L "$dir" ] || return 0
    if [ -d "$dir" ] && [ -z "$(ls -A "$dir" 2>/dev/null)" ]; then return 0; fi
    if [ -e "$dir/docker-compose.yml" ] && [ -e "$dir/services/bridge" ] \
        && [ -e "$dir/scripts/bootstrap-hub.sh" ]; then
        return 0
    fi
    listing="$(ls -A "$dir" 2>/dev/null | head -5 | tr '\n' ' ')"
    echo "❌ $dir に presence-logger ではない中身があります（先頭: $listing）" >&2
    echo "   コピーすると元のアプリのファイルと混ざるので、何もせずに止めました。" >&2
    echo "   別の場所へ退避してから、もう一度「このUSBからコピー」を開いてください。" >&2
    return 1
}

copy_is_interactive() {
    case "${COPY_FORCE_TTY:-}" in
        1) return 0 ;;
        0) return 1 ;;
    esac
    [ -t 0 ] && [ -t 1 ]
}

# 利用者本人として argv を実行する前置きを、1行に1語で出す（純粋関数）。
# $1: 今の uid  $2: 利用者  $3: 利用者のホーム
copy_as_user_prefix() {
    local uid="$1" user="$2" home="$3"
    if [ "$uid" -eq 0 ] && [ "$user" != "root" ]; then
        printf '%s\n' runuser -u "$user" -- env \
            -u SUDO_USER -u SUDO_UID -u SUDO_GID -u SUDO_COMMAND \
            "HOME=$home" "USER=$user" "LOGNAME=$user"
    else
        printf '%s\n' env "HOME=$home"
    fi
}

copy_hub_from_usb() {
    local kit="$1" dest="$2" desk="$3"
    if [ ! -f "$kit/.kit/origin.env" ]; then
        echo "キットではありません（.kit/origin.env が無い）: $kit" >&2
        return 1
    fi
    copy_dest_verdict "$dest" || return 1
    mkdir -p "$dest" "$desk"
    if [ -d "$kit/payload/presence-logger" ]; then
        kit_copy_dir "$kit/payload/presence-logger" "$dest" || return 1
    else
        echo "payload/presence-logger がありません: $kit" >&2
        return 1
    fi
    mkdir -p "$dest/.kit"
    kit_copy_dir "$kit/.kit" "$dest/.kit" || return 1
    copy_fix_permissions "$dest"
    echo "✅ $dest へコピーしました"
    echo "   （この時点では AP もコンテナも起動していません）"
    if ! copy_install_setup_icon "$dest" "$desk"; then
        echo "⚠ デスクトップにアイコンを置けませんでした" >&2
        return 1
    fi
}

copy_find_preflight() {
    local kit="$1" dest="$2" f
    if [ -n "${COPY_PREFLIGHT_CMD:-}" ]; then
        [ "$COPY_PREFLIGHT_CMD" = none ] && return 1
        printf '%s\n' "$COPY_PREFLIGHT_CMD"
        return 0
    fi
    for f in "$kit/preflight-new-hub.sh" "$dest/scripts/preflight-new-hub.sh"; do
        if [ -f "$f" ]; then printf '%s\n' "$f"; return 0; fi
    done
    return 1
}

# 戻り値: 0=始めてよい  1=始めない（理由は表示済み）
copy_preflight_gate() {
    local kit="$1" dest="$2" user="$3" home="$4" pf out rc
    local -a pre
    if ! pf="$(copy_find_preflight "$kit" "$dest")"; then
        echo "   事前点検（preflight-new-hub.sh）がキットに無いため、点検せずに初期設定を始めます。"
        return 0
    fi
    mapfile -t pre < <(copy_as_user_prefix "$(id -u)" "$user" "$home")
    echo "── 事前点検（読み取りのみ）──"
    out="$("${pre[@]}" bash "$pf" 2>&1)"; rc=$?
    printf '%s\n' "$out"
    case "$rc" in
        0) ;;
        2)
            echo "🛑 事前点検で BLOCK が出たため、初期設定は始めません。"
            echo "   上の [BLOCK] の行を解消してから、デスクトップの「ハブ初期設定」をクリックしてください。"
            echo "   （コピーしたファイルはそのまま残っています。AP もコンテナも起動していません）"
            return 1
            ;;
        *)
            echo "🛑 事前点検を最後まで実行できませんでした（戻り値 $rc）。初期設定は始めません。"
            echo "   手で bash $pf を実行して確認してから、デスクトップの「ハブ初期設定」をクリックしてください。"
            return 1
            ;;
    esac
    if grep -q '^\[WARN\]' <<< "$out"; then
        # read -p は stdin が端末でないと問いかけを出さないので、自分で出す。
        printf '%s' "⚠ 上の WARN を読んで、了承できるなら Enter で初期設定を始めます（やめるときは Ctrl+C。後でアイコンからも始められます）: "
        if ! read -r _; then
            echo
            echo "   入力を読めなかったので始めません。デスクトップの「ハブ初期設定」をクリックしてください。"
            return 1
        fi
    fi
    return 0
}

# コピーが成功した後に呼ぶ。戻り値は使わない（いつも 0）。
copy_maybe_launch_wizard() {
    local kit="$1" dest="$2" user="$3" home="$4" wrc
    local -a pre cmd
    local click="   デスクトップの「ハブ初期設定」をクリックして設定を始めてください"
    if [ "${COPY_NO_WIZARD:-}" = 1 ] || ! copy_is_interactive; then echo "$click"; return 0; fi
    if [ -f "$dest/.kit/setup-complete" ]; then
        echo "   このハブは設定済みです（$dest/.kit/setup-complete）。自動では始めません。"
        echo "   AP パスワードだけやり直すときは、デスクトップの「ハブ初期設定」から p を選んでください。"
        return 0
    fi
    if [ -z "$user" ] || [ "$user" = root ]; then
        echo "   root で動いているため自動では始めません。pi でログインしてデスクトップの「ハブ初期設定」をクリックしてください。"
        return 0
    fi
    if [ -n "${COPY_WIZARD_CMD:-}" ]; then
        cmd=("$COPY_WIZARD_CMD")
    elif [ -f "$dest/scripts/setup-hub-wizard.sh" ]; then
        cmd=(bash "$dest/scripts/setup-hub-wizard.sh")
    else
        echo "$click"
        return 0
    fi
    copy_preflight_gate "$kit" "$dest" "$user" "$home" || return 0
    echo
    echo "▶ このままハブ初期設定を始めます（ウィンドウを閉じれば中断できます。後でアイコンからやり直せます）"
    mapfile -t pre < <(copy_as_user_prefix "$(id -u)" "$user" "$home")
    ( cd "$dest" && "${pre[@]}" "${cmd[@]}" ); wrc=$?
    if [ "$wrc" -ne 0 ]; then
        echo "⚠ ハブ初期設定は完了していません（戻り値 $wrc）。デスクトップの「ハブ初期設定」からやり直せます。"
    fi
    echo "（もう一度 Enter を押すとこのウィンドウが閉じます）"
    return 0
}

copy_self_kit() {
    local here
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [ -f "$here/.kit/origin.env" ]; then
        printf '%s\n' "$here"
        return 0
    fi
    return 1
}

main() {
    local kit="${1:-}" dest desk user home
    user="${SUDO_USER:-$USER}"
    home="$(getent passwd "$user" | cut -d: -f6)"
    home="${home:-$HOME}"
    dest="${COPY_DEST:-$home/projects/presence-logger}"
    desk="${COPY_DESKTOP:-$home/Desktop}"

    if [ -z "$kit" ]; then
        kit="$(copy_self_kit || true)"
    fi
    if [ -z "$kit" ]; then
        kit="$(copy_find_kit /media || true)"
    fi
    if [ -z "$kit" ]; then
        kit="$(copy_find_kit /run/media || true)"
    fi
    if [ -z "$kit" ]; then
        echo "USB キットが見つかりません。presence-hub-kit を挿してから再実行してください" >&2
        echo "  手動: bash copy-to-this-pi.sh /media/pi/USB名/presence-hub-kit" >&2
        return 1
    fi
    copy_hub_from_usb "$kit" "$dest" "$desk" || return $?
    copy_maybe_launch_wizard "$kit" "$dest" "$user" "$home"
    return 0
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
