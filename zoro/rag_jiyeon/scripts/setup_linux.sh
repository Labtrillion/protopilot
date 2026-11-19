#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
command -v uv >/dev/null || { echo 'uv is required: https://docs.astral.sh/uv/getting-started/installation/'; exit 1; }
if [[ ! -x .venv/bin/python ]]; then
  if [[ -e .venv ]]; then
    echo 'An incompatible .venv already exists. Move it aside before creating the Linux environment.'
    exit 1
  fi
  uv venv --python 3.11 .venv
fi
uv pip install --python .venv/bin/python 'torch==2.8.0' --index-url https://download.pytorch.org/whl/cu128
uv pip install --python .venv/bin/python -e '.[dev]' -c constraints-linux.txt
.venv/bin/python scripts/download_models.py
printf '\nReady. Run: source .venv/bin/activate\n'
