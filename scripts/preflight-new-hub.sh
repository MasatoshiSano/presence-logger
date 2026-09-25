#!/usr/bin/env bash
# preflight-new-hub.sh — 新しいハブにする Pi で、bootstrap が何を壊すかを先に見せる。
#
#   bash preflight-new-hub.sh              （USB キット直下でも、リポジトリの scripts/ でも）
#
# 読み取り専用。何も入れず、何も書き換えない。sudo も要らない。
# 別のアプリが既に動いている Pi をハブにすると、docker・ホスト名・
# NetworkManager・systemd・~/Desktop を bootstrap が上書きする。
# 各項目を BLOCK（進めてはいけない）/ WARN（人が判断する）/ INFO / OK で出し、
# 最後に「このまま進めてよいか」を1行で結論する。
#
# 値の判定関数は引数だけを見る。ファイルを読む判定にはパスを注入でき、
# テストは一時ディレクトリを使う。実機のコマンドを呼ぶのは main のみ。
set -uo pipefail

# 1件の判定を出力する。続く引数は字下げした補足行になる。
_pf() {
    local level="$1" area="$2" msg="$3"
    shift 3
    printf '[%s] %s: %s\n' "$level" "$area" "$msg"
    local line
    for line in "$@"; do
        printf '        %s\n' "$line"
    done
}

# $1: 入っている docker 系パッケージ名（空白・改行区切り）
# 現行フェーズ20 は docker-ce があれば保持する。エンジン無しで CLI /
# containerd.io だけが残る場合は Debian 版と競合するため BLOCK。
preflight_docker_verdict() {
    local installed=" $(printf '%s' "${1:-}" | tr '\n' ' ') " hit="" p
    case "$installed" in
        *" docker-ce "*)
            _pf WARN docker "docker-ce を保持します（現行フェーズ20 は Docker パッケージ導入をスキップ）" \
                "既存コンテナへの影響を確認してください。Docker サービスの enable/start は実行します。"
            return 0 ;;
    esac
    for p in docker-ce-cli containerd.io docker-ce-rootless-extras; do
        case "$installed" in *" $p "*) hit="$hit $p" ;; esac
    done
    if [ -n "$hit" ]; then
        _pf BLOCK docker "Docker 社版が入っています:${hit}" \
            "フェーズ20 の docker.io はこれと排他です。apt が既存の Docker を削除し、" \
            "その上で動いているコンテナが止まります。" \
            "既存アプリの持ち主と相談し、docker.io へ移すか別の Pi を使ってください。"
        return 0
    fi
    case "$installed" in
        *" docker.io "*) _pf OK docker "docker.io が既に入っています（フェーズ20 と同じもの）" ;;
        *)               _pf OK docker "Docker は入っていません（フェーズ20 で docker.io を入れます）" ;;
    esac
}

# $1: 今のホスト名  $2: site.env の HUB_HOSTNAME（未作成なら空）
preflight_hostname_verdict() {
    local current="${1:-}" new="${2:-}"
    if [ -z "$new" ]; then
        _pf INFO hostname "今のホスト名は ${current} です。新しい名はまだ決まっていません（site.env 未作成）" \
            "フェーズ20 がホスト名を変えます。別アプリがこの名前で呼ばれていないか確認してください。"
    elif [ "$current" = "$new" ]; then
        _pf OK hostname "ホスト名は ${current} のまま変わりません"
    else
        _pf WARN hostname "ホスト名が変わります: ${current} → ${new}" \
            "/etc/hostname と /etc/hosts を書き換えます（フェーズ20）。" \
            "別アプリがこの名前（mDNS の ${current}.local など）で呼ばれていないか確認してください。"
    fi
}

# $1: ~/projects/presence-logger
# copy-hub-from-usb.sh は同名ディレクトリへ上書きコピーする。中身が別物なら
# 元のアプリのファイルが混ざって壊れる。印は presence-logger にしかない3点。
preflight_repo_verdict() {
    local dir="$1" listing
    if [ ! -e "$dir" ] && [ ! -L "$dir" ]; then
        _pf OK repo "${dir} はまだありません"
        return 0
    fi
    if [ -d "$dir" ] && [ -r "$dir" ] && [ -x "$dir" ] && [ -z "$(ls -A "$dir" 2>/dev/null)" ]; then
        _pf OK repo "${dir} は空です"
        return 0
    fi
    if [ -e "$dir/docker-compose.yml" ] && [ -e "$dir/services/bridge" ] \
        && [ -e "$dir/scripts/bootstrap-hub.sh" ]; then
        _pf OK repo "${dir} は presence-logger です（キットの内容で更新されます）"
        return 0
    fi
    listing="$(ls -A "$dir" 2>/dev/null | head -5 | tr '\n' ' ')"
    _pf BLOCK repo "${dir} に presence-logger ではない中身があります" \
        "先頭: ${listing}" \
        "キットのコピーがこの上に重なり、元のアプリのファイルと混ざります。" \
        "別の場所へ退避してから進めてください。"
}

# $1: ~/Desktop/presence-tools  $2: 配るほうの desktop/presence-tools（不明なら空）
# 70-desktop.sh は cp -r で重ねる。同名で中身が違うものだけが失われる。
preflight_desktop_tools_verdict() {
    local dst="$1" src="${2:-}" rel changed=()
    if [ ! -d "$dst" ] || [ -z "$(ls -A "$dst" 2>/dev/null)" ]; then
        _pf OK desktop "${dst} はまだありません"
        return 0
    fi
    if [ -z "$src" ] || [ ! -d "$src" ]; then
        _pf WARN desktop "${dst} が既にあります。フェーズ70 が cp -r で上書きします" \
            "配る側の中身が見つからないので、どのファイルが変わるかは比べられません。"
        return 0
    fi
    while IFS= read -r rel; do
        [ -e "$dst/$rel" ] || continue
        cmp -s "$src/$rel" "$dst/$rel" || changed+=("$rel")
    done < <(cd "$src" && find . -type f -not -path '*/__pycache__/*' | sed 's|^\./||' | sort)
    if [ "${#changed[@]}" -eq 0 ]; then
        _pf OK desktop "${dst} に内容が変わる既存ファイルはありません（新規ファイルは追加されます）"
        return 0
    fi
    local shown=("${changed[@]:0:10}")
    [ "${#changed[@]}" -gt 10 ] && shown+=("…ほか $(( ${#changed[@]} - 10 )) 個")
    _pf WARN desktop "${dst} の ${#changed[@]} 個が上書きされます（フェーズ70 の cp -r）" \
        "${shown[@]}"
}

# $1: /etc/modprobe.d/8821au.conf
preflight_modprobe_verdict() {
    local conf="$1"
    if [ -f "$conf" ]; then
        local lines=() l
        while IFS= read -r l; do lines+=("今の中身: $l"); done < <(head -5 "$conf")
        _pf WARN modprobe "${conf} を上書きします（フェーズ30）" "${lines[@]}" \
            "8821au ドライバも入れ替え、modprobe -r 8821au で一度外します。"
    else
        _pf WARN modprobe "${conf} を新しく置きます（フェーズ30）" \
            "USB Wi-Fi ドングル用の 8821au ドライバを DKMS で入れ、modprobe し直します。"
    fi
}

# $1: AP にする IF  $2: 戻り先 Wi-Fi（HOME_SSID。不明なら空）
# $3: 今つながっている接続（nmcli -t -f NAME,DEVICE connection show --active）
# 50-ap.sh は UFI_CONN を空にして戻り先を保持する。AP_IF は切断し、
# 同名の AP 接続を作り直す。
preflight_nm_verdict() {
    local ap_if="${1:-wlan1}" home="${2:-}" active="${3:-}" name dev users=()
    while IFS=: read -r name dev; do
        [ -n "$name" ] || continue
        [ "$dev" = "$ap_if" ] || continue
        users+=("今 ${ap_if} を使っている接続「${name}」は作成・作り直す場合に切断されます")
    done <<< "$active"
    _pf WARN network "${ap_if} に子Pi用の AP を作ります（フェーズ50）" \
        "戻り先 Wi-Fi「${home:-（site.env の HOME_SSID。未作成）}」の autoconnect は変更しません（UFI_CONN は空）。" \
        "AP を作成・作り直す場合、${ap_if} を nmcli device disconnect します（既存の自APは通常スキップ）。" \
        "${users[@]}"
}

# $1: /etc/systemd/system/presence-logger.service
preflight_systemd_verdict() {
    local unit="$1" wd
    if [ -f "$unit" ]; then
        wd="$(sed -n 's/^WorkingDirectory=//p' "$unit" | head -1)"
        _pf WARN systemd "${unit} が既にあります。上書きします（install.sh）" \
            "今の WorkingDirectory: ${wd:-（無し）}" \
            "setup-autostart.sh が drop-in を置き、enable と start をします（フェーズ60）。"
    else
        _pf WARN systemd "presence-logger.service を入れて自動起動にします" \
            "install.sh が ${unit} を置き、フェーズ60 の setup-autostart.sh が" \
            "drop-in を置いて enable と start をします。"
    fi
}

# $1: /etc/systemd/timesyncd.conf
# install.sh はこのファイルを丸ごと書き直して systemd-timesyncd を再起動する。
preflight_timesyncd_verdict() {
    local conf="$1" kept=() l
    if [ -f "$conf" ]; then
        while IFS= read -r l; do kept+=("消える設定: $l"); done \
            < <(grep -E '^[[:space:]]*[A-Za-z]+=' "$conf" 2>/dev/null)
    fi
    _pf WARN time "${conf} を書き直し、systemd-timesyncd を再起動します（install.sh）" \
        "NTP サーバは profiles.yaml の SNTP と公開 NTP になります。" \
        "${kept[@]}"
}

# $1: 入っている入力メソッド系パッケージ名（ibus / fcitx / uim など）
preflight_ime_verdict() {
    local others
    others="$(printf '%s' "${1:-}" | tr '\n' ' ' | sed 's/  */ /g; s/^ //; s/ $//')"
    _pf WARN ime "日本語入力 fcitx5 + mozc を入れ、キーボードを jp106 にします（フェーズ10）" \
        "/etc/default/keyboard を XKBLAYOUT=jp / XKBMODEL=pc105 に書き換えます。" \
        "im-config で入力メソッドを fcitx5 に切り替えます。" \
        ${others:+"今入っている入力メソッド: ${others}（fcitx5 に切り替わります）"}
}

# $1: / の空き（KiB）
# イメージを docker load すると展開後およそ 3GB、apt でさらに 1GB 程度。
preflight_disk_verdict() {
    local kib="${1:-0}" gib
    gib=$(( kib / 1024 / 1024 ))
    if [ "$kib" -lt $(( 5 * 1024 * 1024 )) ]; then
        _pf WARN disk "/ の空きが ${gib}GiB です。5GiB 未満だとイメージの展開で詰まります"
    else
        _pf INFO disk "/ の空きは ${gib}GiB です"
    fi
}

# $1: AP にする IF  $2: その IF があれば名前、無ければ空
preflight_wlan1_verdict() {
    local ap_if="${1:-wlan1}" found="${2:-}"
    if [ -n "$found" ]; then
        _pf INFO wifi "${ap_if} があります"
    else
        _pf INFO wifi "${ap_if} がまだありません（USB Wi-Fi ドングルはフェーズ30 までに挿す）"
    fi
}

# $1: 動いているコンテナ名（改行区切り）  $2: docker ps の終了コード
preflight_containers_verdict() {
    local names="${1:-}" rc="${2:-0}" list=() n
    if [ "$rc" -ne 0 ]; then
        _pf INFO containers "コンテナ一覧を読めません（docker が無いか、権限が要ります: sudo docker ps）"
        return 0
    fi
    while IFS= read -r n; do [ -n "$n" ] && list+=("$n"); done <<< "$names"
    if [ "${#list[@]}" -eq 0 ]; then
        _pf INFO containers "動いているコンテナはありません"
    else
        _pf INFO containers "動いているコンテナが ${#list[@]} 個あります" "${list[@]}"
    fi
}

# $1: ss -ltnp の 1883 番の行（無ければ空）
preflight_port1883_verdict() {
    local lines="${1:-}" rc="${2:-0}" l out=()
    if [ "$rc" -ne 0 ]; then
        _pf INFO mqtt "1883 番の情報を読めません（ss の有無・権限を確認してください）"
        return 0
    fi
    while IFS= read -r l; do [ -n "$l" ] && out+=("$l"); done <<< "$lines"
    if [ "${#out[@]}" -eq 0 ]; then
        _pf INFO mqtt "1883 番の TCP LISTEN はありません"
    else
        _pf INFO mqtt "1883 番を既に使っているものがあります（ハブの mosquitto と衝突し得ます）" \
            "${out[@]}" "プロセス名は権限により非表示の場合があります。完全な確認には root が必要です。"
    fi
}

# $1: 導入済み Compose の版（無ければ空）  $2: Debian docker-compose の候補版
preflight_compose_verdict() {
    local installed="${1:-}" candidate="${2:-}" engine=" ${3:-} "
    if [ -n "$installed" ]; then
        _pf OK compose "Compose ${installed} が入っています"
    elif [[ "$engine" == *" docker-ce "* ]]; then
        _pf WARN compose "docker-ce を保持するため Compose 導入もスキップされます" \
            "Compose パッケージが見つかりません。docker compose の動作を手動で確認・準備してください。"
    elif [ -z "$candidate" ] || [ "$candidate" = "(none)" ]; then
        _pf WARN compose "docker-compose を入れられる apt リポジトリがありません" \
            "フェーズ20 の apt-get install がまるごと失敗します。"
    else
        _pf OK compose "docker-compose ${candidate} を入れられます"
    fi
}

# 標準入力: 判定の出力。先頭の [BLOCK] / [WARN] だけを数える（補足行は数えない）。
# 戻り値: BLOCK があれば 2、なければ 0。
preflight_conclude() {
    local verdicts blocks warns
    verdicts="$(cat)"
    blocks="$(grep -c '^\[BLOCK\]' <<< "$verdicts")"
    warns="$(grep -c '^\[WARN\]' <<< "$verdicts")"
    if [ "$blocks" -gt 0 ]; then
        echo "結論: ❌ このまま進めてはいけません（BLOCK ${blocks}件・WARN ${warns}件）"
        return 2
    elif [ "$warns" -gt 0 ]; then
        echo "結論: ⚠ 進めてよいが、先に WARN ${warns}件 を人が確認して了承すること"
    else
        echo "結論: ✅ このまま進めてよい"
    fi
}

# ---------------------------------------------------------------------------
# ここから下は実機から値を集める。読むだけで、書き換えるコマンドは呼ばない。

# KEY=value 形式のファイルから値を1つ取り出す。source はしない（中身を実行しない）。
preflight_env_value() {
    local file="$1" key="$2"
    [ -f "$file" ] || return 0
    sed -n "s/^${key}=//p" "$file" 2>/dev/null | tail -1 | sed "s/^[\"']//; s/[\"']\$//"
}

# 配る側のリポジトリを探す。リポジトリでは scripts/ の1つ上、
# USB キット直下では payload/presence-logger。
preflight_find_source() {
    local here="$1"
    if [ -d "$here/../desktop/presence-tools" ] && [ -f "$here/bootstrap-hub.sh" ]; then
        (cd "$here/.." && pwd)
    elif [ -d "$here/payload/presence-logger" ]; then
        printf '%s\n' "$here/payload/presence-logger"
    fi
}

# site.env があればそれ、無ければキットの雛形から値を読む。
preflight_site_value() {
    local key="$1" src="$2" here="$3" home="$4" f v
    for f in "${src:+$src/site.env}" "$home/projects/presence-logger/site.env" \
        "$here/.kit/site.env.template" "${src:+$src/.kit/site.env.template}"; do
        v="$(preflight_env_value "$f" "$key")"
        if [ -n "$v" ]; then
            printf '%s\n' "$v"
            return 0
        fi
    done
}

main() {
    local here src etc home ap_if home_ssid pkgs installed compose_ver compose_cand
    local names rc report port_lines port_rc
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    src="$(preflight_find_source "$here")"
    etc="${PREFLIGHT_ETC:-/etc}"
    home="${HOME}"
    ap_if="$(preflight_site_value AP_IF "$src" "$here" "$home")"
    ap_if="${ap_if:-wlan1}"
    home_ssid="$(preflight_site_value HOME_SSID "$src" "$here" "$home")"

    pkgs="$(dpkg-query -W -f='${db:Status-Abbrev}\t${Package}\t${Version}\n' \
        docker.io docker-ce docker-ce-cli containerd.io docker-ce-rootless-extras \
        docker-compose-plugin docker-compose ibus fcitx fcitx5 uim 2>/dev/null)"
    installed="$(awk -F'\t' '$1 ~ /^ii/ {print $2}' <<< "$pkgs")"
    compose_ver="$(awk -F'\t' '$1 ~ /^ii/ && ($2 == "docker-compose-plugin" || $2 == "docker-compose") {print $3}' <<< "$pkgs")"
    compose_cand=""
    if [ -z "$compose_ver" ]; then
        compose_cand="$(apt-cache policy docker-compose 2>/dev/null \
            | sed -n 's/^ *Candidate: *//p' | head -1)"
    fi
    names="$(docker ps --format '{{.Names}}' 2>/dev/null)"; rc=$?

    port_lines="$(ss -ltnpH 'sport = :1883' 2>/dev/null)"; port_rc=$?

    report="$(
        preflight_docker_verdict "$installed"
        preflight_compose_verdict "$compose_ver" "$compose_cand" "$(tr '\n' ' ' <<< "$installed")"
        preflight_repo_verdict "$home/projects/presence-logger"
        preflight_hostname_verdict "$(hostname 2>/dev/null)" \
            "$(preflight_site_value HUB_HOSTNAME "$src" "$here" "$home")"
        preflight_nm_verdict "$ap_if" "$home_ssid" \
            "$(nmcli -t -f NAME,DEVICE connection show --active 2>/dev/null)"
        preflight_systemd_verdict "$etc/systemd/system/presence-logger.service"
        preflight_timesyncd_verdict "$etc/systemd/timesyncd.conf"
        preflight_desktop_tools_verdict "$home/Desktop/presence-tools" \
            "${src:+$src/desktop/presence-tools}"
        preflight_modprobe_verdict "$etc/modprobe.d/8821au.conf"
        preflight_ime_verdict "$(grep -xE 'ibus|fcitx|uim' <<< "$installed")"
        preflight_disk_verdict "$(df -Pk / 2>/dev/null | awk 'NR==2 {print $4}')"
        preflight_wlan1_verdict "$ap_if" \
            "$([ -e "/sys/class/net/$ap_if" ] && echo "$ap_if")"
        preflight_containers_verdict "$names" "$rc"
        preflight_port1883_verdict "$port_lines" "$port_rc"
    )"

    echo "==> 新しいハブにする前の点検（$(hostname 2>/dev/null)）。何も変更しません"
    echo
    printf '%s\n' "$report"
    echo
    preflight_conclude <<< "$report"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
