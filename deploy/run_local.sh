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
PORT="${PORT:-8000}"
export AUDIOBOOK_DATA="${AUDIOBOOK_DATA:-$HERE/server/local-data}"
echo "Kokoro voices (Pipecat): http://localhost:$PORT   (phone on the same Wi-Fi: http://${IP:-<your-ip>}:$PORT)"

# --tunnel: a free https address, needed by the app hosted on GitHub Pages (an https page cannot
# talk to a plain http server) and handy for listening from outside your Wi-Fi.
if [ "${1:-}" = "--tunnel" ]; then
  CODE="${AUDIOBOOK_TOKEN:-$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(9))')}"
  export AUDIOBOOK_TOKEN="$CODE"
  if ! command -v cloudflared >/dev/null && [ ! -x ./cloudflared ]; then
    curl -fsSL -o cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
    chmod +x cloudflared
  fi
  CF=$(command -v cloudflared || echo ./cloudflared)
  "$CF" tunnel --no-autoupdate --url "http://localhost:$PORT" > cloudflared.log 2>&1 &
  trap 'kill %1 2>/dev/null || true' EXIT
  for _ in $(seq 1 30); do
    URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' cloudflared.log | head -1 || true)
    [ -n "$URL" ] && break
    sleep 1
  done
  if [ -n "${URL:-}" ]; then
    echo
    echo "────────────────────────────────────────────────────────────"
    echo " From anywhere, including the app on GitHub Pages:"
    echo "   $URL/?pair=$CODE"
    echo " Or in the hosted app's Settings: server $URL, code $CODE"
    echo "────────────────────────────────────────────────────────────"
    echo
  else
    echo "The tunnel didn't start; see server/cloudflared.log" >&2
  fi
fi

exec .venv/bin/python -m audiobook --engine kokoro --port "$PORT"
