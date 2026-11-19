#!/usr/bin/env bash
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/server-runtime.sh"
prepare_runtime
cd "$repo_root/vendor/trida-stack/inference/zoro-1"

export DECIDE_PYTHON="$runtime/zoro-venv/bin/python"
export PATH="$runtime/zoro-venv/bin:$PATH"
command=("$DECIDE_PYTHON" "$PWD/serve.py" --mode decide --host 127.0.0.1 --port "${ZORO_PORT:-30001}")
case "${1:-}" in
    --dry-run) exec "${command[@]}" "$@" ;;
    --foreground) shift; exec "${command[@]}" "$@" ;;
esac
start_background zoro serve_decide.py "${command[@]}" "$@"
