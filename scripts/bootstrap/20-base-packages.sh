#!/usr/bin/env bash
# 20-base-packages.sh — docker・python3-yaml・ビルド環境・venv・ホスト名・SSH鍵。
#
# python3-yaml は「システムの python3」に要る。install.sh /
# connect-hime-h-reap.sh / show-recent-records.sh / pipeline_monitor は
# いずれも venv ではなくシステム側の python3 で yaml を読む。
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$HERE/../.." && pwd)"
# shellcheck source=scripts/lib/site-env.sh
source "$REPO_DIR/scripts/lib/site-env.sh"

base_packages() {
    cat <<'EOF'
docker.io
docker-compose-plugin
python3-yaml
python3-venv
mosquitto-clients
git
rsync
dkms
build-essential
bc
raspberrypi-kernel-headers
EOF
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

main() {
    site_env_require
    echo "==> パッケージを導入"
    apt-get update
    # shellcheck disable=SC2046
    apt-get install -y $(base_packages | tr '\n' ' ') || return 1

    echo "==> ホスト名を $HUB_HOSTNAME に"
    base_set_hostname "$HUB_HOSTNAME" /etc/hostname /etc/hosts
    hostnamectl set-hostname "$HUB_HOSTNAME"

    local user="${SUDO_USER:-$USER}" home
    home="$(getent passwd "$user" | cut -d: -f6)"
    echo "==> $user を docker グループへ"
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

$(cat "$home/.ssh/id_ed25519.pub")

 ⚠ docker グループの反映には再ログイン(または reboot)が必要です。
--------------------------------------------------------------
EOF
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
