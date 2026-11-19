#!/usr/bin/env bash
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/server-runtime.sh"
prepare_runtime
cd "$repo_root"

command=("$repo_root/.venv/bin/python" -m server.app --port "${PROTOPILOT_PORT:-8765}")
if [[ ${1:-} == --foreground ]]; then
    shift
    exec "${command[@]}" "$@"
fi
start_background protopilot '-m server.app' "${command[@]}" "$@"
