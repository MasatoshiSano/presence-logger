#!/usr/bin/env bash
# Run pytest + ruff inside Docker containers (the same images that ship to prod).
#
# Usage:
#   bash scripts/test.sh                       # full suite (both services + integration)
#   bash scripts/test.sh detector              # only detector tests
#   bash scripts/test.sh bridge                # only bridge tests
#   bash scripts/test.sh -- pytest -k fsm -v   # forward args to pytest in both services
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

# Make sure dev images exist locally (build is fast after first time).
ensure_image() {
    local svc="$1"
    if ! docker image inspect "presence-${svc}:dev" >/dev/null 2>&1; then
        echo ">>> Building presence-${svc}:dev image (first run only)..."
        docker compose -f docker-compose.dev.yml build "${svc}-dev"
    fi
}

# Install dev deps inside the image at runtime (kept out of the image so the
# Dockerfile stays small for production). The .pyc cache lives in /tmp.
DEV_INSTALL='pip install --quiet --root-user-action=ignore -r /app/requirements-dev.txt 2>&1 | tail -1'

run_service() {
    local svc="$1"
    shift
    ensure_image "$svc"
    echo ""
    echo "=================================================================="
    echo " ${svc} — pytest + ruff (in Docker)"
    echo "=================================================================="
    docker compose -f docker-compose.dev.yml run --rm "${svc}-dev" \
        bash -c "${DEV_INSTALL} && pytest services/${svc}/tests/ $* && ruff check services/${svc}/"
}

run_integration() {
    ensure_image bridge
    echo ""
    echo "=================================================================="
    echo " integration — end-to-end tests (in bridge image)"
    echo "=================================================================="
    docker compose -f docker-compose.dev.yml run --rm bridge-dev \
        bash -c "${DEV_INSTALL} && pytest tests/integration/ -v"
}

# Argument routing.
case "${1:-all}" in
    detector)
        shift
        run_service detector "$@"
        ;;
    bridge)
        shift
        run_service bridge "$@"
        ;;
    integration)
        run_integration
        ;;
    all|"")
        run_service detector
        run_service bridge
        run_integration
        ;;
    --)
        # Pass remaining args to both services
        shift
        run_service detector "$@"
        run_service bridge "$@"
        ;;
    *)
        echo "usage: $0 [detector|bridge|integration|all] [-- pytest-args...]"
        exit 2
        ;;
esac

echo ""
echo "✓ all checks passed"
