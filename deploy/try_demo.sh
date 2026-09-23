#!/usr/bin/env bash
# Try the whole app on any computer, no GPU: the server uses placeholder tones instead of AuK.
#   bash deploy/try_demo.sh        then open http://localhost:8000 (or http://<this-computer-ip>:8000 on your phone)
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE/server"
command -v uv >/dev/null || { curl -LsSf https://astral.sh/uv/install.sh | sh; export PATH="$HOME/.local/bin:$PATH"; }
[ -x .venv/bin/python ] || uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -q -r requirements.txt
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo "Demo server: http://localhost:8000   (phone on the same Wi-Fi: http://${IP:-<your-ip>}:8000)"
AUDIOBOOK_DATA="$HERE/server/demo-data" exec .venv/bin/python -m audiobook --engine demo --port "${PORT:-8000}"
