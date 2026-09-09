#!/usr/bin/env bash
# docker-run.sh — docker compose プラグインの有無をはっきり失敗させる。
#
# `docker compose` (v2 プラグイン) だけを使う。ハイフン付きの v1 は
# プラグイン未導入と区別が付きにくく、現場で「無いのに有る」ように見える。
#
# 使い方: source scripts/lib/docker-run.sh; docker_compose up -d
docker_compose() {
    if docker compose version >/dev/null 2>&1; then
        docker compose "$@"
        return
    fi
    echo "docker compose プラグインが入っていません。" >&2
    echo "  sudo apt-get install -y docker-compose-plugin" >&2
    echo "  （bootstrap フェーズ20 が入れます。ここへ来たらフェーズ20 が失敗しています）" >&2
    return 1
}

# usermod -aG docker の直後でも動く。現セッションの supplementary group は
# 古いままだが、/etc/group は更新済みなので sg が通る。
docker_as_user() {
    if docker info >/dev/null 2>&1; then
        "$@"
        return
    fi
    if command -v sg >/dev/null 2>&1 && getent group docker >/dev/null 2>&1; then
        local me; me="$(id -un)"
        if getent group docker | grep -qw "$me"; then
            sg docker -c "$(printf '%q ' "$@")"
            return
        fi
    fi
    echo "docker グループに入っていないか、まだ反映されていません。" >&2
    echo "  sudo usermod -aG docker $(id -un)" >&2
    echo "  いまのシェルでは: sg docker -c '$*'" >&2
    return 1
}
