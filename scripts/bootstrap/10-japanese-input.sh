#!/usr/bin/env bash
# 10-japanese-input.sh — 日本語入力(fcitx5 + mozc)を使えるようにする。
#
# 注意: 現行機の ~/.xinputrc は `run_im fcitx`(fcitx4)という残骸で、fcitx4 は
# 入っていない。im-config の auto フォールバックが偶然 fcitx5 を拾って動いて
# いるだけなので、あのファイルを複製してはいけない。im-config に生成させる。
set -uo pipefail

ime_packages() {
    cat <<'EOF'
fcitx5
fcitx5-mozc
fcitx5-frontend-gtk3
fcitx5-frontend-gtk4
fcitx5-frontend-qt5
fcitx5-frontend-qt6
fcitx5-config-qt
mozc-utils-gui
fonts-noto-cjk
EOF
}

ime_set_keyboard_layout() {
    # XKBLAYOUT行を"jp"に置換、行がなければ追加する
    local file="${1:-/etc/default/keyboard}"
    grep -q '^XKBLAYOUT="jp"' "$file" && return 0
    sed -i 's/^XKBLAYOUT=.*/XKBLAYOUT="jp"/' "$file"
    grep -q '^XKBLAYOUT="jp"' "$file" || echo 'XKBLAYOUT="jp"' >> "$file"
}

ime_render_fcitx5_profile() {
    cat <<'EOF'
[Groups/0]
Name=Default
Default Layout=jp
DefaultIM=mozc

[Groups/0/Items/0]
Name=keyboard-jp
Layout=

[Groups/0/Items/1]
Name=mozc
Layout=

[GroupOrder]
0=Default
EOF
}

main() {
    local user="${SUDO_USER:-$USER}" home
    home="$(getent passwd "$user" | cut -d: -f6)"

    echo "==> パッケージを導入"
    # shellcheck disable=SC2046
    apt-get install -y $(ime_packages | tr '\n' ' ') || return 1

    echo "==> キーボード配列を jp に"
    ime_set_keyboard_layout /etc/default/keyboard
    setupcon 2>/dev/null || true

    echo "==> im-config で fcitx5 を選択"
    su - "$user" -c "im-config -n fcitx5"

    echo "==> fcitx5 profile を配置"
    install -d -o "$user" -g "$user" -m 700 "$home/.config/fcitx5"
    ime_render_fcitx5_profile > "$home/.config/fcitx5/profile"
    chown "$user:$user" "$home/.config/fcitx5/profile"
    chmod 600 "$home/.config/fcitx5/profile"

    cat <<'EOF'

--------------------------------------------------------------
 ✅ 日本語入力の設定が終わりました。

 ⚠ ここで一度ログアウトして、ログインし直してください。
    labwc(Wayland)では GTK_IM_MODULE 等をセッション開始時の
    im-launch が撒くため、再ログインするまで入力できません。

 再ログイン後の確認:
    env | grep GTK_IM_MODULE      # fcitx と出ること
    pgrep -a fcitx5               # 動いていること
    記号キー(@ : _)が刻印どおり入ること
--------------------------------------------------------------
EOF
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
