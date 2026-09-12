#!/usr/bin/env bash
# setup-children-wizard.sh — デスクトップの「子をこのハブへ付ける」から呼ばれる対話。
#
# ブラウザは使わない。順番に答えると、既存の子をホスト名と局番号そのままで
# このハブへ付ける。
set -uo pipefail

CHILD_WIZARD_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/site-env.sh
source "$CHILD_WIZARD_REPO/scripts/lib/site-env.sh"
# shellcheck source=scripts/prepare-child-sd.sh
source "$CHILD_WIZARD_REPO/scripts/prepare-child-sd.sh"

children_cli() {
    PYTHONPATH="$CHILD_WIZARD_REPO${PYTHONPATH:+:$PYTHONPATH}" \
        python3 -m fleet_ui.child_cli "$@"
}

children_wizard_validate_mode() {
    case "${1:-}" in
        1|2|3) return 0 ;;
        *)
            echo "1 / 2 / 3 で選んでください" >&2
            return 1
            ;;
    esac
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

children_ask() {
    local prompt="$1" default="${2:-}" reply
    if [ -n "$default" ]; then
        read -r -p "$prompt [$default]: " reply || return 1
        printf '%s\n' "${reply:-$default}"
    else
        read -r -p "$prompt: " reply || return 1
        printf '%s\n' "$reply"
    fi
}

children_ask_yn() {
    local prompt="$1" default="${2:-N}" reply
    read -r -p "$prompt [$default]: " reply || return 1
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
    local old_host payload entries entry yn failed=0
    children_wizard_show_hub || return 1
    echo
    while true; do
        old_host="$(children_ask "旧親のホスト名または工場網の IP")" || return 1
        children_wizard_validate_old_host "$old_host" && break
    done
    echo
    echo "先に、このハブから旧親へ公開鍵を1回入れてください:"
    echo "  ssh-copy-id -i ~/.ssh/id_ed25519.pub pi@$old_host"
    children_ask_yn "入れましたか？" N || {
        echo "鍵を入れてから、もう一度このアイコンを開いてください。"
        return 1
    }
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
        if children_ask_yn "$entry をこのハブへ移しますか？" Y; then
            echo "移しています（AP に現れるまで数分かかることがあります）…"
            if ! children_cli take "$old_host" "$entry" | children_json_message; then
                echo "この子の付け替えは失敗しました。残りの子は続けます。" >&2
                failed=1
            fi
        else
            echo "  スキップ: $entry"
        fi
    done 3<<< "$entries"
    return "$failed"
}

children_wizard_write_sd() {
    local root pub ssid
    children_wizard_show_hub || return 1
    echo
    echo "クローンした子の SD を USB カードリーダに挿してください。"
    children_ask_yn "挿しましたか？" Y || {
        echo "挿してから、もう一度このアイコンを開いてください。"
        return 1
    }
    root="$(children_ask "マウント先（空なら自動で探す）")"
    if [ -z "$root" ]; then
        root="$(child_sd_find_root /media)" || {
            echo "子PiのSDが見つかりません。マウント先を指定してください。" >&2
            return 1
        }
    fi
    children_wizard_validate_sd_root "$root" || return 1
    pub="${HOME}/.ssh/id_ed25519.pub"
    ssid="$(grep -E '^AP_SSID=' "$CHILD_WIZARD_REPO/.kit/ap-join.env" 2>/dev/null | head -1 | cut -d= -f2-)"
    if [ -z "$ssid" ] || ! grep -q '^WIFI_AP_PSK=.' "$CHILD_WIZARD_REPO/.kit/ap-join.env" 2>/dev/null; then
        echo ".kit/ap-join.env が読めません。ハブ初期設定が済んでいるか確認してください。" >&2
        return 1
    fi
    echo "SD: $root"
    echo "  ホスト名と局番号はそのまま、公開鍵と AP（$ssid）を書きます。"
    # PSK もマウント先も bash -c に埋め込まない。prepare-child-sd.sh が ap-join.env を読む。
    sudo env CHILD_SD_PUBKEY="$pub" HOME="$HOME" \
        bash "$CHILD_WIZARD_REPO/scripts/prepare-child-sd.sh" "$root" || return 1
    echo
    echo "SD を外して子Pi に挿し、電源を入れてください。"
}

children_wizard_adopt_on_ap() {
    local payload ip host ok failed=0
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
        if children_ask_yn "${host:-$ip} を名前と局番号そのままで取り込みますか？" Y; then
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

main() {
    echo "このハブへ子Pi を付けます。"
    echo "ホスト名と局番号はそのまま残します。"
    echo "（同じハブへのクローン増設で改名したいときだけ、フリート管理の登録ウィザードを使います）"
    echo
    echo "  1) 旧親が同じ工場網でまだ動いている"
    echo "  2) 旧親はもう使わない / 別の工場網 / 子のSDをクローンした"
    echo "  3) 子はもうこのハブの AP に繋がっている"
    echo
    local mode rc=0
    while true; do
        mode="$(children_ask "番号で選ぶ" "1")" || return 1
        children_wizard_validate_mode "$mode" && break
    done
    echo
    case "$mode" in
        1) children_wizard_from_old_parent || rc=$? ;;
        2)
            children_wizard_write_sd || rc=$?
            echo
            if children_ask_yn "子の電源を入れましたか？ AP から取り込みます" N; then
                children_wizard_adopt_on_ap || rc=$?
            else
                echo "起動したら、もう一度このアイコンを開いて 3 を選んでください。"
            fi
            ;;
        3) children_wizard_adopt_on_ap || rc=$? ;;
    esac
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
