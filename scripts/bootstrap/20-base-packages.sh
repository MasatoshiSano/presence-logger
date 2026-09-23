#!/usr/bin/env bash
# 20-base-packages.sh — docker・python3-yaml・ビルド環境・venv・ホスト名・SSH鍵。
#
# python3-yaml は「システムの python3」に要る。install.sh /
# connect-hime-h-reap.sh / show-recent-records.sh / pipeline_monitor は
# いずれも venv ではなくシステム側の python3 で yaml を読む。
#
# docker グループへの追加もこの場で行う。ただし残りのフェーズは root で
# docker を話すので、反映のための再ログインを待たずに先へ進む。
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$HERE/../.." && pwd)"
# shellcheck source=scripts/lib/site-env.sh
source "$REPO_DIR/scripts/lib/site-env.sh"

# apt-get install は1つでも名前が見つからないと何も入れずに失敗する。
# ここには trixie(Debian 13)に実在する名前だけを置くこと。
# raspberrypi-kernel-headers は bookworm までの名前で、trixie では
# linux-headers-rpi-2712(Pi 5 用)になった。
base_packages() {
    base_docker_packages
    cat <<'EOF'
python3-yaml
python3-venv
mosquitto-clients
git
rsync
dkms
build-essential
bc
linux-headers-rpi-2712
EOF
}

# docker は Debian 自身の版を使う。docker-compose-plugin / docker-ce は
# Docker 社の apt リポジトリ(download.docker.com)にしか無く、素の Pi OS には
# そのリポジトリが無い。Debian の docker-compose は
# /usr/libexec/docker/cli-plugins/ に入り `docker compose` として動く。
#
# docker.io は docker-ce と Conflicts。docker-ce が既に入った機械(今の親機)で
# 頼むと、apt は docker-ce を外して入れ替える。動いているエンジンには触らない。
base_docker_packages() {
    if dpkg-query -W -f='${Status}' docker-ce 2>/dev/null | grep -q 'install ok installed'; then
        return 0
    fi
    printf '%s\n' docker.io docker-cli docker-compose
}

base_set_hostname() {
    local name="$1" hn="${2:-/etc/hostname}" hosts="${3:-/etc/hosts}"
    printf '%s\n' "$name" > "$hn"
    # 旧ホスト名では置換しない。`\b` はハイフン手前でも単語境界になり、
    # pizero2w の置換が pizero2w-2 の行を壊す(DEPLOY.md に記録済み)。
    # 127.0.1.1 行はローカルホスト名専用なので丸ごと差し替える。
    if grep -q '^127\.0\.1\.1[[:space:]]' "$hosts"; then
        sed -i "s/^127\.0\.1\.1[[:space:]].*/127.0.1.1\t$name/" "$hosts"
    else
        printf '127.0.1.1\t%s\n' "$name" >> "$hosts"
    fi
}

base_ensure_venv() {
    local repo="${1:-$REPO_DIR}"
    [ -x "$repo/.venv/bin/python" ] && return 0
    # fleet-ui.service が .venv/bin/python を絶対パスで叩くため必須。
    # fleet_ui 自体は標準ライブラリのみだが、システムの python3-yaml も
    # 見えるようにしておく。
    python3 -m venv --system-site-packages "$repo/.venv"
}

# linux-headers-rpi-2712 が連れてくるのはアーカイブ最新版のヘッダだけ。
# 書いたばかりの SD は古いカーネルで動いているので、フェーズ30 の DKMS が
# 要る「今のカーネル」のヘッダは別に頼む。旧版はアーカイブから消えることが
# あるので本体の並びには混ぜない(混ぜると全部入らなくなる)。
base_install_packages() {
    # shellcheck disable=SC2046
    apt-get install -y $(base_packages | tr '\n' ' ') || return 1
    local running="linux-headers-$(uname -r)"
    if ! apt-get install -y "$running"; then
        echo "⚠ $running が入りませんでした。フェーズ30 のドライバ作成が失敗します。" >&2
        echo "  apt full-upgrade → 再起動のあと、フェーズ20 から流し直してください。" >&2
    fi
}

# docker.io を入れただけでは daemon が上がっていないことがある。フェーズ60 が
# 「イメージは load 済みなのにコンテナが上がらない」で転ぶ前に、ここで弾く。
base_enable_docker() {
    systemctl enable --now docker 2>/dev/null || service docker start 2>/dev/null || true
    if ! docker compose version >/dev/null 2>&1; then
        echo "docker を入れても docker compose が使えません" >&2
        return 1
    fi
}

main() {
    site_env_require
    local user="${SUDO_USER:-$USER}" home
    home="$(getent passwd "$user" | cut -d: -f6)"

    echo "==> パッケージを導入"
    apt-get update
    base_install_packages || return 1
    base_enable_docker || return 1

    echo "==> ホスト名を $HUB_HOSTNAME に"
    base_set_hostname "$HUB_HOSTNAME" /etc/hostname /etc/hosts
    # ここを握り潰すと device_id と MQTT client_id が旧名のままになり、
    # 親子が相互に切断し合う。黙って進めず落とす。
    hostnamectl set-hostname "$HUB_HOSTNAME"

    echo "==> $user を docker グループへ"
    getent group docker >/dev/null || groupadd docker
    usermod -aG docker "$user"

    echo "==> venv を用意"
    base_ensure_venv "$REPO_DIR"
    chown -R "$user:$user" "$REPO_DIR/.venv"

    if [ ! -f "$home/.ssh/id_ed25519" ]; then
        echo "==> SSH 鍵を生成"
        su - "$user" -c "ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519"
    fi

    cat <<EOF

--------------------------------------------------------------
 ⚠ 子Pi へ入るための公開鍵です。**旧親がまだ子に到達できるうちに**
   各子の ~/.ssh/authorized_keys へ入れてください。
   子が新APへ移った後では、どちらの親からも入れられなくなります。

$(cat "$home/.ssh/id_ed25519.pub" 2>/dev/null)

 docker はこのあとも root で操作するので、残りのフェーズは再ログインを
 待たずに進みます。デスクトップから $user で docker を叩けるのは再起動後です。
--------------------------------------------------------------
EOF
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
