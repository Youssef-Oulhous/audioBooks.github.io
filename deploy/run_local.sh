#!/usr/bin/env bash
# Real voices on this computer, no GPU needed: Pipecat's Kokoro TTS on the CPU.
#   bash deploy/run_local.sh     then open http://localhost:8000 (or http://<this-computer-ip>:8000 on your phone)
# Kokoro reads at roughly real time on a laptop CPU, so a 10-hour book takes several hours (it keeps going
# in the background and resumes after a restart). The model (~330 MB) downloads the first time.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE/server"
command -v uv >/dev/null || { curl -LsSf https://astral.sh/uv/install.sh | sh; export PATH="$HOME/.local/bin:$PATH"; }
[ -x .venv/bin/python ] || uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -q -r requirements.txt -r requirements-kokoro.txt
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo "Kokoro voices (Pipecat): http://localhost:8000   (phone on the same Wi-Fi: http://${IP:-<your-ip>}:8000)"
AUDIOBOOK_DATA="${AUDIOBOOK_DATA:-$HERE/server/local-data}" exec .venv/bin/python -m audiobook --engine kokoro --port "${PORT:-8000}"
