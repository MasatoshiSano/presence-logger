#!/usr/bin/env bash
# 50-ap.sh — 子Pi 用の AP を立てる。実処理は既存の setup-dongle-ap.sh に任せ、
# ここは site.env の値を環境変数として渡すことと、同一SSID の重複検出を担う。
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$HERE/../.." && pwd)"
# shellcheck source=scripts/lib/site-env.sh
source "$REPO_DIR/scripts/lib/site-env.sh"

ap_own_connection_active() {
    nmcli -t -f NAME connection show --active 2>/dev/null \
        | grep -qFx "${AP_SSID}-ap"
}

ap_duplicate_ssid_present() {
    local ssid="${1:-$AP_SSID}"
    nmcli -t -f SSID,SIGNAL,SECURITY dev wifi list 2>/dev/null \
        | cut -d: -f1 | grep -qFx "$ssid"
}

ap_apply_explicit_address() {
    nmcli connection modify "${AP_SSID}-ap" ipv4.addresses "${AP_GW_IP}/24" || return 1
    nmcli connection up "${AP_SSID}-ap" || return 1
    return 0
}

# setup-dongle-ap.sh は ipv4.method shared を使うため、GW IP は NetworkManager が
# 既定の 10.42.0.1/24 を自動で付ける。それ以外にするには ipv4.addresses の
# 明示指定が要る(=増設時。子側の send_target_config.json の変更も必要)。
ap_needs_explicit_address() {
    [ "${AP_GW_IP:-10.42.0.1}" != "10.42.0.1" ]
}

# UFI_CONN は空で渡す。setup-dongle-ap.sh は UFI_CONN の接続の autoconnect を
# 切る(ドングルを子機にしていた旧構成向け)。以前は HOME_SSID を渡していたため、
# F66 に繋いだ新機が再起動後に F66 へ戻らず、遠隔から触れなくなっていた。
ap_env_args() {
    printf 'AP_IF=%s\n'      "$AP_IF"
    printf 'AP_SSID=%s\n'    "$AP_SSID"
    printf 'AP_BAND=%s\n'    "$AP_BAND"
    printf 'AP_CHANNEL=%s\n' "$AP_CHANNEL"
    printf 'UFI_CONN=\n'
    printf 'AP_CONN=%s-ap\n' "$AP_SSID"
}

# 何をするかだけを決める: skip(自APが既に動いている) / abort(同じ SSID の
# 他APが見える) / build(作る、または作り直す)。
#
# 強制は引数 --force と環境変数 AP_FORCE=1 のどちらでも受ける。ウィザードの
# 「p: AP パスワードだけやり直す」は AP_FORCE=1 で呼ぶ。以前はこれを見ておらず、
# しかも自APが動いていればスキップしていたので、secrets だけ新しいパスワードに
# なり、実際の AP は古いパスワードのままだった。強制時は動いていても作り直す
# (setup-dongle-ap.sh はプロファイルを消して作り直すので新しい PSK が載る)。
#
# AP_FORCE=1 が越えるのは「自APが動いているからスキップ」だけ。自APが落ちていて
# 同じ SSID が見えるなら、それは別のハブなので止める。同名APの確認まで越えるのは
# 明示の --force だけ。
ap_plan() {
    local rebuild=0 override_dup=0
    if [ "${1:-}" = "--force" ]; then
        rebuild=1
        override_dup=1
    elif [ "${AP_FORCE:-}" = "1" ]; then
        rebuild=1
    fi
    if ap_own_connection_active; then
        if [ "$rebuild" = 1 ]; then echo build; else echo skip; fi
    elif [ "$override_dup" != 1 ] && ap_duplicate_ssid_present "$AP_SSID"; then
        echo abort
    else
        echo build
    fi
}

main() {
    site_env_require
    local plan
    plan="$(ap_plan "$@")"
    if [ "$plan" = skip ]; then
        echo "${AP_SSID}-ap は既に起動しています。スキップします"
        return 0
    fi
    if [ "$plan" = abort ]; then
        cat >&2 <<EOF
⚠ 同じ SSID の AP が既に見えています: $AP_SSID

  このまま起動すると同名の AP が2つになり、子Piがどちらに繋ぐか不定になります
  (DEPLOY.md に記録のある相互切断事故と同じ構図)。

  引っ越しなら、先に旧ハブの AP を落としてください:
      旧ハブで: sudo nmcli connection down ${AP_SSID}-ap
                sudo nmcli connection modify ${AP_SSID}-ap connection.autoconnect no

  増設なら、site.env の AP_SSID と AP_GW_IP を別の値にしてください。

  それでも続けるなら: bash $0 --force
EOF
        return 1
    fi

    local kv
    while IFS= read -r kv; do export "${kv?}"; done < <(ap_env_args)
    bash "$REPO_DIR/desktop/presence-tools/setup-dongle-ap.sh" || return 1

    if ap_needs_explicit_address; then
        echo "==> AP のゲートウェイIPを $AP_GW_IP に固定"
        ap_apply_explicit_address || return 1
        cat <<EOF

⚠ AP のIPが既定(10.42.0.1)ではありません。各子Pi の
  ~/send_target_config.json の "host" を $AP_GW_IP へ変更する必要があります。
EOF
    fi
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
