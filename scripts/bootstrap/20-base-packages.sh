#!/usr/bin/env bash
# 20-base-packages.sh — docker・compose プラグイン・venv・ホスト名・SSH鍵。
#
# docker グループへの追加はこの場で行う。残りのフェーズは root で docker を
# 話すので、再ログインを待たずに先へ進む。
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$HERE/../.." && pwd)"
# shellcheck source=scripts/lib/site-env.sh
source "$REPO_DIR/scripts/lib/site-env.sh"
# shellcheck source=scripts/lib/docker-run.sh
source "$REPO_DIR/scripts/lib/docker-run.sh"

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
    if grep -q '^127\.0\.1\.1[[:space:]]' "$hosts"; then
        sed -i "s/^127\.0\.1\.1[[:space:]].*/127.0.1.1\t$name/" "$hosts"
    else
        printf '127.0.1.1\t%s\n' "$name" >> "$hosts"
    fi
}

base_ensure_venv() {
    local repo="${1:-$REPO_DIR}"
    [ -x "$repo/.venv/bin/python" ] && return 0
    python3 -m venv --system-site-packages "$repo/.venv"
}

base_enable_docker() {
    systemctl enable --now docker 2>/dev/null || service docker start 2>/dev/null || true
    if ! docker compose version >/dev/null 2>&1; then
        echo "docker-compose-plugin を入れても docker compose が使えません" >&2
        return 1
    fi
}

main() {
    site_env_require
    local user="${SUDO_USER:-$USER}" home
    home="$(getent passwd "$user" | cut -d: -f6)"

    echo "==> パッケージを導入"
    apt-get update
    # shellcheck disable=SC2046
    apt-get install -y $(base_packages | tr '\n' ' ') || return 1
    base_enable_docker || return 1

    echo "==> ホスト名を $HUB_HOSTNAME に"
    base_set_hostname "$HUB_HOSTNAME" /etc/hostname /etc/hosts
    hostnamectl set-hostname "$HUB_HOSTNAME" 2>/dev/null || true

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
 公開鍵（子Pi の authorized_keys へ。新しく足す子用）:

$(cat "$home/.ssh/id_ed25519.pub" 2>/dev/null)

 docker はこのあとも root で操作するので、再ログインを待たずに進みます。
 デスクトップから pi ユーザーで docker を叩くのは再起動後です。
--------------------------------------------------------------
EOF
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
