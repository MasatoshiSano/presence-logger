#!/usr/bin/env bash
# setup-children-wizard.sh — デスクトップの「子をこのハブへ付ける」から呼ばれる対話。
#
# ブラウザは使わない。樹形図で答え、既存の子の引っ越しか新規クローン増設かを選ぶ。
set -uo pipefail

CHILD_WIZARD_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHILD_WIZ_BACK='__WIZ_BACK__'
# shellcheck source=scripts/lib/site-env.sh
source "$CHILD_WIZARD_REPO/scripts/lib/site-env.sh"
# shellcheck source=scripts/prepare-child-sd.sh
source "$CHILD_WIZARD_REPO/scripts/prepare-child-sd.sh"

children_wizard_is_back() {
    [ "${1:-}" = "$CHILD_WIZ_BACK" ]
}

children_cli() {
    PYTHONPATH="$CHILD_WIZARD_REPO${PYTHONPATH:+:$PYTHONPATH}" \
        python3 -m fleet_ui.child_cli "$@"
}

children_wizard_validate_kind() {
    case "${1:-}" in
        1|2) return 0 ;;
        *)
            echo "1 / 2 で選んでください" >&2
            return 1
            ;;
    esac
}

children_wizard_validate_keep_path() {
    case "${1:-}" in
        1|2|3) return 0 ;;
        *)
            echo "1 / 2 / 3 で選んでください" >&2
            return 1
            ;;
    esac
}

# 旧名。テストと呼び出しの互換用。
children_wizard_validate_mode() {
    children_wizard_validate_keep_path "$@"
}

children_wizard_validate_old_host() {
    PYTHONPATH="$CHILD_WIZARD_REPO${PYTHONPATH:+:$PYTHONPATH}" python3 -c \
        'import sys; from fleet_ui.migrate import validate_old_host
e = validate_old_host(sys.argv[1])
if e:
    print(e, file=sys.stderr)
    sys.exit(1)
' "$1"
}

children_reply_is_back() {
    case "${1:-}" in
        0|戻る|"<<") return 0 ;;
        *) return 1 ;;
    esac
}

children_ask() {
    local prompt="$1" default="${2:-}" reply
    if [ -n "$default" ]; then
        read -r -p "$prompt [$default] (0=戻る): " reply || return 1
    else
        read -r -p "$prompt (0=戻る): " reply || return 1
    fi
    if children_reply_is_back "$reply"; then
        printf '%s\n' "$CHILD_WIZ_BACK"
        return 0
    fi
    if [ -n "$default" ]; then
        printf '%s\n' "${reply:-$default}"
    else
        printf '%s\n' "$reply"
    fi
}

# 0=yes  1=no  2=戻る
children_ask_yn() {
    local prompt="$1" default="${2:-N}" reply
    read -r -p "$prompt [$default] (0=戻る): " reply || return 1
    if children_reply_is_back "$reply"; then
        return 2
    fi
    reply="${reply:-$default}"
    [[ "$reply" =~ ^[yY] ]]
}

children_json_entries() {
    python3 -c 'import json,sys; d=json.load(sys.stdin); print("\n".join(c.get("entry","") for c in d.get("children") or [] if c.get("entry")))'
}

children_json_message() {
    python3 -c '
import json, sys
d = json.load(sys.stdin)
msg = d.get("message") or ""
if not d.get("ok"):
    print(msg or "失敗しました", file=sys.stderr)
    sys.exit(1)
print(msg or "完了")
'
}

children_wizard_validate_sd_root() {
    local r="${1:-}"
    case "$r" in
        /*) ;;
        *)
            echo "マウント先は絶対パスで指定してください" >&2
            return 1
            ;;
    esac
    case "$r" in
        *..*|*[\'\"$\`\;]*)
            echo "マウント先に使えない文字があります" >&2
            return 1
            ;;
    esac
    return 0
}

children_wizard_show_hub() {
    local st ssid pubkey
    st="$(children_cli status)" || true
    ssid="$(printf '%s\n' "$st" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("ap_ssid") or "")')"
    pubkey="$(printf '%s\n' "$st" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("pubkey") or "")')"
    if [ -n "$ssid" ]; then
        echo "このハブの AP 名: $ssid"
    fi
    if [ -n "$pubkey" ]; then
        echo "このハブの公開鍵:"
        echo "$pubkey"
    else
        echo "このハブに SSH 公開鍵がありません。先に ssh-keygen -t ed25519 を実行してください。" >&2
        return 1
    fi
}

children_wizard_from_old_parent() {
    local old_host payload entries entry yn rc failed=0
    children_wizard_show_hub || return 1
    echo
    while true; do
        old_host="$(children_ask "旧親のホスト名または工場網の IP")" || return 1
        children_wizard_is_back "$old_host" && return 2
        children_wizard_validate_old_host "$old_host" && break
    done
    echo
    echo "先に、このハブから旧親へ公開鍵を1回入れてください:"
    echo "  ssh-copy-id -i ~/.ssh/id_ed25519.pub pi@$old_host"
    children_ask_yn "入れましたか？" N
    rc=$?
    [ "$rc" -eq 2 ] && return 2
    if [ "$rc" -ne 0 ]; then
        echo "鍵を入れてから、もう一度このアイコンを開いてください。"
        return 1
    fi
    echo
    echo "旧親の子一覧を取っています…"
    payload="$(children_cli list "$old_host")" || true
    if ! printf '%s\n' "$payload" | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("ok") else 1)'; then
        printf '%s\n' "$payload" | children_json_message >&2
        return 1
    fi
    entries="$(printf '%s\n' "$payload" | children_json_entries)"
    if [ -z "$entries" ]; then
        echo "旧親の一覧は空です。"
        return 0
    fi
    echo "移す子（ホスト名と局番号はそのまま）:"
    printf '%s\n' "$entries" | sed 's/^/  - /'
    echo
    while IFS= read -r entry <&3; do
        [ -n "$entry" ] || continue
        children_ask_yn "$entry をこのハブへ移しますか？" Y
        yn=$?
        if [ "$yn" -eq 2 ]; then
            return 2
        fi
        if [ "$yn" -eq 0 ]; then
            echo "移しています（AP に現れるまで数分かかることがあります）…"
            if ! children_cli take "$old_host" "$entry" | children_json_message; then
                echo "この子の付け替えは失敗しました。残りの子は続けます。" >&2
                echo "  子がこのハブの AP に居るなら、終わったあと 1 → 3 で取り込んでください。" >&2
                failed=1
            fi
        else
            echo "  スキップ: $entry"
        fi
    done 3<<< "$entries"
    return "$failed"
}

children_wizard_find_sd_root() {
    child_sd_find_root /media || child_sd_find_root /mnt
}

children_wizard_wait_for_child_sd() {
    local root tries=0 rc
    while [ "$tries" -lt 2 ]; do
        root="$(children_wizard_find_sd_root)" && {
            printf '%s\n' "$root"
            return 0
        }
        tries=$((tries + 1))
        echo >&2
        echo "カードの中身がまだ見えません。" >&2
        echo "  ・カードリーダのランプは点いていますか" >&2
        echo "  ・デスクトップにカードのアイコンやフォルダは出ましたか（中身は触らなくてよい）" >&2
        echo "  ・ハブ用ではなく、子ラズパイをコピーしたカードですか" >&2
        echo >&2
        if [ "$tries" -ge 2 ]; then
            break
        fi
        children_ask_yn "挿し直して、フォルダが出るまで待ったあと、続けますか？" Y
        rc=$?
        [ "$rc" -eq 2 ] && return 2
        [ "$rc" -eq 0 ] || return 1
    done
    echo "まだ見つかりません。カードを挿したまま、もう一度このアイコンを開いてください。" >&2
    return 1
}

children_wizard_write_sd() {
    local root pub ssid rc
    children_wizard_show_hub || return 1
    echo
    echo "クローンした子ラズパイの SD カードを、USB カードリーダに挿してください。"
    echo "デスクトップにカードのフォルダが開いても、中身を触る必要はありません。"
    echo "カードがどこに付いたかは聞かず、こちらで探します。"
    children_ask_yn "挿しましたか？" Y
    rc=$?
    [ "$rc" -eq 2 ] && return 2
    if [ "$rc" -ne 0 ]; then
        echo "挿してから、もう一度このアイコンを開いてください。"
        return 1
    fi
    root="$(children_wizard_wait_for_child_sd)" || return $?
    children_wizard_validate_sd_root "$root" || return 1
    pub="${HOME}/.ssh/id_ed25519.pub"
    ssid="$(grep -E '^AP_SSID=' "$CHILD_WIZARD_REPO/.kit/ap-join.env" 2>/dev/null | head -1 | cut -d= -f2-)"
    if [ -z "$ssid" ] || ! grep -q '^WIFI_AP_PSK=.' "$CHILD_WIZARD_REPO/.kit/ap-join.env" 2>/dev/null; then
        echo ".kit/ap-join.env が読めません。ハブ初期設定が済んでいるか確認してください。" >&2
        return 1
    fi
    echo "カードが見つかりました。このハブの鍵と Wi-Fi（$ssid）を書きます。"
    echo "名前と局番号はそのままです。"
    # PSK もパスも bash -c に埋め込まない。prepare-child-sd.sh が ap-join.env を読む。
    sudo env CHILD_SD_PUBKEY="$pub" HOME="$HOME" \
        bash "$CHILD_WIZARD_REPO/scripts/prepare-child-sd.sh" "$root" || return 1
    echo
    echo "書き終わりました。カードを外して子ラズパイに挿し、電源を入れてください。"
}

children_wizard_adopt_on_ap() {
    local payload ip host ok failed=0 yn
    echo "このハブの AP に居る未登録の子を探します…"
    payload="$(children_cli candidates)" || true
    python3 -c 'import json,sys; d=json.load(sys.stdin); rows=d.get("candidates") or []; sys.exit(0 if rows else 1)' <<<"$payload" || {
        echo "未登録の端末はまだ見えません。子の電源と AP を確認してから、もう一度選んでください。"
        return 1
    }
    python3 -c '
import json,sys
d=json.load(sys.stdin)
for c in d.get("candidates") or []:
    extra = c.get("hostname") or ("鍵なし" if not c.get("ssh_ok") else "")
    print("%s %s %s" % (c.get("ip") or "", c.get("mac") or "", extra))
' <<<"$payload"
    echo
    while IFS=$'\t' read -r ip host ok <&3; do
        [ -n "$ip" ] || continue
        if [ "$ok" != "1" ]; then
            echo "$ip はこのハブの鍵では SSH できません。先に SD へ鍵を書いてください。"
            continue
        fi
        children_ask_yn "${host:-$ip} を名前と局番号そのままで取り込みますか？" Y
        yn=$?
        if [ "$yn" -eq 2 ]; then
            return 2
        fi
        if [ "$yn" -eq 0 ]; then
            if ! children_cli adopt "$ip" | children_json_message; then
                echo "取り込みに失敗しました。" >&2
                failed=1
            fi
        fi
    done 3< <(python3 -c '
import json,sys
d=json.load(sys.stdin)
for c in d.get("candidates") or []:
    print("%s\t%s\t%s" % (c.get("ip") or "", c.get("hostname") or "", "1" if c.get("ssh_ok") else "0"))
' <<<"$payload")
    return "$failed"
}

children_wizard_register_new() {
    local payload ip mac host ok failed=0 suggest name rc yn
    echo "新しい子は、このハブの AP に繋いでから登録します。"
    echo "SD クローンなら、先にこのハブの鍵と AP を SD へ書いておいてください。"
    echo "登録するとホスト名が変わり、局番号は空になります。"
    echo
    children_ask_yn "先にクローンSDへ鍵と AP を書きますか？" N
    rc=$?
    [ "$rc" -eq 2 ] && return 2
    if [ "$rc" -eq 0 ]; then
        children_wizard_write_sd || return $?
        echo
        children_ask_yn "子の電源を入れましたか？" Y
        rc=$?
        [ "$rc" -eq 2 ] && return 2
        if [ "$rc" -ne 0 ]; then
            echo "起動したら、もう一度このアイコンを開いて 2 を選んでください。"
            return 1
        fi
    fi
    echo "このハブの AP に居る未登録の子を探します…"
    payload="$(children_cli candidates)" || true
    python3 -c 'import json,sys; d=json.load(sys.stdin); rows=d.get("candidates") or []; sys.exit(0 if rows else 1)' <<<"$payload" || {
        echo "未登録の端末はまだ見えません。子の電源と AP を確認してから、もう一度選んでください。"
        return 1
    }
    python3 -c '
import json,sys
d=json.load(sys.stdin)
for c in d.get("candidates") or []:
    extra = c.get("hostname") or ("鍵なし" if not c.get("ssh_ok") else "")
    print("%s %s %s" % (c.get("ip") or "", c.get("mac") or "", extra))
' <<<"$payload"
    echo
    echo "新しい名前の既定は「このハブのホスト名-001」のように3桁です。Enter ならそのまま。"
    suggest="$(children_cli suggest | python3 -c 'import json,sys; print(json.load(sys.stdin).get("hostname") or "child-001")')"
    while IFS=$'\t' read -r ip mac host ok <&3; do
        [ -n "$ip" ] || continue
        if [ "$ok" != "1" ]; then
            echo "$ip はこのハブの鍵では SSH できません。先に SD へ鍵を書いてください。"
            continue
        fi
        while true; do
            children_ask_yn "${host:-$ip} を新しい子として改名登録しますか？" Y
            yn=$?
            if [ "$yn" -eq 2 ]; then
                return 2
            fi
            if [ "$yn" -ne 0 ]; then
                break
            fi
            name="$(children_ask "新しいホスト名（局番号は空になります）" "$suggest")" || return 1
            if children_wizard_is_back "$name"; then
                continue
            fi
            if ! children_cli register "$ip" "$mac" "$name" | children_json_message; then
                echo "登録に失敗しました。" >&2
                failed=1
            else
                suggest="$(children_cli suggest | python3 -c 'import json,sys; print(json.load(sys.stdin).get("hostname") or "child-001")')"
            fi
            break
        done
    done 3< <(python3 -c '
import json,sys
d=json.load(sys.stdin)
for c in d.get("candidates") or []:
    print("%s\t%s\t%s\t%s" % (c.get("ip") or "", c.get("mac") or "", c.get("hostname") or "", "1" if c.get("ssh_ok") else "0"))
' <<<"$payload")
    return "$failed"
}

children_wizard_keep_identity() {
    local path rc
    while true; do
        echo "既存の子はホスト名と局番号を残します。"
        echo
        echo "  1) 旧親が同じ工場網でまだ動いている"
        echo "  2) 旧親はもう使わない / 別の工場網 / 子のSDをクローンした"
        echo "  3) 子はもうこのハブの AP に繋がっている"
        echo
        path="$(children_ask "番号で選ぶ" "1")" || return 1
        children_wizard_is_back "$path" && return 2
        children_wizard_validate_keep_path "$path" || continue
        echo
        case "$path" in
            1)
                children_wizard_from_old_parent
                rc=$?
                [ "$rc" -eq 2 ] && continue
                return "$rc"
                ;;
            2)
                children_wizard_write_sd
                rc=$?
                [ "$rc" -eq 2 ] && continue
                [ "$rc" -eq 0 ] || return "$rc"
                echo
                children_ask_yn "子の電源を入れましたか？ AP から取り込みます" N
                rc=$?
                [ "$rc" -eq 2 ] && continue
                if [ "$rc" -eq 0 ]; then
                    children_wizard_adopt_on_ap
                    rc=$?
                    [ "$rc" -eq 2 ] && continue
                    return "$rc"
                fi
                echo "起動したら、もう一度このアイコンを開いて、1 → 3 を選んでください。"
                return 0
                ;;
            3)
                children_wizard_adopt_on_ap
                rc=$?
                [ "$rc" -eq 2 ] && continue
                return "$rc"
                ;;
        esac
    done
}

main() {
    echo "このハブへ子Pi を付けます。"
    echo "間違えたら 0 で直前の質問に戻れます。"
    echo
    echo "  1) すでに動いている子を移す（名前と局番号はそのまま）"
    echo "  2) 新しい子を増やす（クローンして改名する。局番号は空になる）"
    echo
    local kind rc=0
    while true; do
        kind="$(children_ask "番号で選ぶ" "1")" || return 1
        children_wizard_is_back "$kind" && continue
        children_wizard_validate_kind "$kind" || continue
        echo
        case "$kind" in
            1) children_wizard_keep_identity; rc=$? ;;
            2) children_wizard_register_new; rc=$? ;;
        esac
        [ "$rc" -eq 2 ] && continue
        break
    done
    echo
    if [ "$rc" -ne 0 ]; then
        echo "途中で失敗しました。上のメッセージを確認してください。"
        echo "Enterで閉じる"
        read -r -p "" _ || true
        return "$rc"
    fi
    echo "終わりです。Enterで閉じる"
    read -r -p "" _ || true
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
