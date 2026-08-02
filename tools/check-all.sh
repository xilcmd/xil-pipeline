#!/bin/bash
# check-all.sh — run the full local gate, the same three things CI runs.
#
# The pre-push hook deliberately runs ruff only, so pushing is instant and CI is
# the real gate.  Run this when you want the whole answer before opening a PR —
# typically once, when a branch is ready to ship, rather than on every push.
#
#   tools/check-all.sh              # lint + tests + strict docs build
#   tools/check-all.sh --fast       # lint + tests, skip the docs build
#
# Exits non-zero on the first failure, and prints how long each stage took so a
# slow stage is obvious rather than merely annoying.
set -uo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

if [ -f "venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source venv/bin/activate
fi

FAST=0
[ "${1:-}" = "--fast" ] && FAST=1

failed=0

stage() {
    local name="$1"; shift
    local start elapsed
    printf '\n=== %s ===\n' "$name"
    start=$SECONDS
    if "$@"; then
        elapsed=$((SECONDS - start))
        printf -- '--- %s: ok (%ds)\n' "$name" "$elapsed"
    else
        elapsed=$((SECONDS - start))
        printf -- '--- %s: FAILED (%ds)\n' "$name" "$elapsed"
        failed=1
        return 1
    fi
}

stage "ruff" ruff check src/ tests/ || exit 1
stage "pytest" pytest tests/ -q || exit 1

if [ "$FAST" -eq 0 ]; then
    # Mirrors CI and Read the Docs: regenerate, then fail on any warning.
    stage "docs" bash -c 'python docs/build_docs.py && python -m mkdocs build --strict' || exit 1
else
    printf '\n(skipping docs build — --fast)\n'
fi

if [ "$failed" -eq 0 ]; then
    printf '\n=== all checks passed ===\n'
fi
exit "$failed"
