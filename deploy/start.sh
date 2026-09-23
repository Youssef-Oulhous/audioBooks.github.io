#!/usr/bin/env bash
# Starts the audiobook server (and, with --tunnel, a free HTTPS tunnel via Cloudflare).
#   bash deploy/start.sh            # http://<this machine>:8000
#   bash deploy/start.sh --tunnel   # also prints an https://….trycloudflare.com address for your iPhone
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE/server"
[ -f .env ] || { echo "Run deploy/setup_gpu.sh first (no server/.env)." >&2; exit 1; }
set -a; source .env; set +a
PORT="${PORT:-8000}"

pair_link() {
  echo
  echo "────────────────────────────────────────────────────────────"
  echo " Open this on your iPhone in Safari, then Share → Add to Home Screen:"
  echo "   $1/?pair=${AUDIOBOOK_TOKEN}"
  echo " (access code: ${AUDIOBOOK_TOKEN})"
  echo "────────────────────────────────────────────────────────────"
  echo
}

if [ -n "${RUNPOD_POD_ID:-}" ]; then
  pair_link "https://${RUNPOD_POD_ID}-${PORT}.proxy.runpod.net"
fi

if [ "${1:-}" = "--tunnel" ]; then
  if ! command -v cloudflared >/dev/null && [ ! -x ./cloudflared ]; then
    curl -fsSL -o cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
    chmod +x cloudflared
  fi
  CF=$(command -v cloudflared || echo ./cloudflared)
  "$CF" tunnel --no-autoupdate --url "http://localhost:$PORT" > cloudflared.log 2>&1 &
  CF_PID=$!
  trap 'kill $CF_PID 2>/dev/null || true' EXIT
  for _ in $(seq 1 30); do
    URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' cloudflared.log | head -1 || true)
    [ -n "$URL" ] && break
    sleep 1
  done
  [ -n "${URL:-}" ] && pair_link "$URL" || echo "Tunnel didn't start; see server/cloudflared.log" >&2
fi

exec .venv/bin/python -m audiobook --port "$PORT"
