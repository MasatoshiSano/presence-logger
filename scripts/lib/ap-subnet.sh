# shellcheck shell=bash
# ap-subnet.sh — 子Pi用 AP のサブネットが、この機械の別の経路・アドレスと重ならないかの検出。
# 50-ap.sh とハブ初期設定ウィザードが source する。単体実行はしない。
#
# 重なったまま AP を起動すると、この機械は AP のゲートウェイ(既定 10.42.0.1)を自分の
# アドレスとして名乗り、別インターフェース(例: wlan0 が繋がっている別ハブの子AP)宛ての
# 返信が AP 側へ出て、ping も SSH も通じなくなる。Wi-Fi の関連付けは生きたままなので
# 見た目では気づけない(2026-09-25 に再起動3回を要した事故)。
#
# `ip` は AP_SUBNET_IP_CMD で差し替えられる(テスト用。実機の経路を読まずに済ませる)。

# ドット区切りIPv4を10進整数へ。基数を明示して 08 のような桁を8進に誤読させない。
_ap_ip2int() {
    local IFS=.
    local -a o=($1)
    echo $(( (10#${o[0]} << 24) | (10#${o[1]} << 16) | (10#${o[2]} << 8) | 10#${o[3]} ))
}

# 2つの CIDR が重なるか。プレフィックスの短い方で切って比べる(広い側が狭い側を含めば重なり)。
#   ap_cidr_overlaps 10.42.0.1/24 10.42.0.0/24
ap_cidr_overlaps() {
    local a="${1%/*}" al="${1#*/}" b="${2%/*}" bl="${2#*/}" l m
    l=$(( al < bl ? al : bl ))
    m=$(( l == 0 ? 0 : (0xFFFFFFFF << (32 - l)) & 0xFFFFFFFF ))
    (( ($(_ap_ip2int "$a") & m) == ($(_ap_ip2int "$b") & m) ))
}

# AP のサブネット($1=AP_GW_IP, /24)と重なる相手を "dev cidr" で1行ずつ出す。
# 何も出なければ安全。$2=AP_IF は自分の AP なので除く(再実行時に自分の経路を誤検出しない)。
# 経路は `ip -4 -o route show table main`(default は宛先ではないので除く)、
# アドレスは `ip -4 -o addr show`(lo は除く)を読む。
ap_subnet_conflicts() {
    local gw="$1" apif="$2" net="$1/24" ipc="${AP_SUBNET_IP_CMD:-ip}" dst rest dev cidr
    {
        "$ipc" -4 -o route show table main | while read -r dst rest; do
            [ "$dst" = default ] && continue
            dev="$(sed -n 's/.* dev \([^ ]*\).*/\1/p' <<<" $rest")"
            [ "$dev" = "$apif" ] && continue
            [[ "$dst" == */* ]] || dst="$dst/32"
            ap_cidr_overlaps "$net" "$dst" && echo "$dev $dst"
        done
        "$ipc" -4 -o addr show | awk '{print $2, $4}' | while read -r dev cidr; do
            { [ "$dev" = "$apif" ] || [ "$dev" = lo ]; } && continue
            ap_cidr_overlaps "$net" "$cidr" && echo "$dev $cidr"
        done
    } | sort -u
    return 0
}

# 空いている 10.42.N.1(N=0..254)を1つ返す。候補の提示用。$1=AP_IF。
ap_suggest_free_gw() {
    local n
    for n in $(seq 0 254); do
        [ -z "$(ap_subnet_conflicts "10.42.$n.1" "$1")" ] && { echo "10.42.$n.1"; return 0; }
    done
    return 1
}

# 重なりを見つけたときに利用者へ出す文面。AP を起動しない理由と、直し方を示す。
#   $1=ap_subnet_conflicts の出力("dev cidr" の行)  $2=空き候補(無ければ空)
# AP_GW_IP / AP_IF は呼び出し側で site.env から読み込み済みの前提。
ap_print_overlap_message() {
    local conflicts="$1" cand="${2:-}" gw="${AP_GW_IP:-10.42.0.1}" line
    local base="${gw%.*}"
    cat <<EOF2
✖ 子Pi用の Wi-Fi(AP) を起動しません。
  この機械の別の接続と、AP のアドレスの範囲が重なっています。

  AP の予定        : ${AP_IF:-wlan1}  ${gw}  (${base}.0 〜 ${base}.255)
EOF2
    while read -r line; do
        [ -n "$line" ] && printf '  重なっている接続 : %s\n' "$line"
    done <<<"$conflicts"
    cat <<EOF2

  このまま起動すると、この機械は ${base}.x 宛ての通信をすべて「自分宛て」と
  取り違え、重なっている接続の先の相手(遠隔作業の中継に使っている別のハブなど)と
  ping も SSH も通じなくなります。Wi-Fi の表示は「接続中」のままなので、
  見た目では気づけません(2026-09-25 に再起動3回を要した事故と同じ形)。

  直し方(どちらか一つ):
    1) 別のハブの Wi-Fi を中継に使っているだけなら、作業を工場網に切り替えてから
       もう一度実行してください。子の設定は何も変わりません。
EOF2
    if [ -n "$cand" ]; then
        cat <<EOF2
    2) このハブの AP のアドレスを変えます。空いている候補: ${cand}
         site.env の AP_GW_IP=${cand} に書き換えて
         bash scripts/bootstrap-hub.sh 50 70
       このハブに子を付けるときの送信先は、「子をこのハブへ付ける」と
       「子SDをこのハブ用にする」が自動で ${cand} に揃えます。
EOF2
    else
        cat <<'EOF2'
    2) このハブの AP のアドレスを変えます。10.42.N.1 に空きが見つかりませんでした。
         site.env の AP_GW_IP を、他の接続と重ならない値にして
         bash scripts/bootstrap-hub.sh 50 70
EOF2
    fi
}
