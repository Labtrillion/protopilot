#!/usr/bin/env bash
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/server-runtime.sh"
prepare_runtime
cd "$repo_root/vendor/trida-stack/inference/zoro-1"

uv_bin="$(command -v uv || printf '%s' "$HOME/.local/bin/uv")"
zoro_python="$runtime/zoro-venv/bin/python"
if [[ ! -x "$zoro_python" ]]; then
    "$uv_bin" venv -q -p 3.12 "$runtime/zoro-venv"
fi
"$uv_bin" pip install -p "$zoro_python" "sglang>=0.5.22" fastapi uvicorn -r requirements.txt huggingface_hub
"$zoro_python" -c 'import torch, sglang; print("torch", torch.__version__, "cuda", torch.version.cuda, torch.cuda.is_available()); print("sglang", sglang.__version__)'
