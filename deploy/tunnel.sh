#!/usr/bin/env bash
# Gives the server a free https address, so the app (hosted on Vercel/GitHub Pages, or on your
# phone's home screen) can reach it from anywhere. Started by audiobook-tunnel.service.
#
# Two kinds of address:
#   * Tailscale Funnel — always the same address, survives reboots. Set it up once with
#     deploy/setup_tailscale.sh, and this script uses it from then on.
#   * Cloudflare quick tunnel — no account needed, but a new random address every restart.
# Whatever is in use, the current address is written to ~/.config/audiobook/address.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
CONF="$HOME/.config/audiobook"
BIN="$HERE/deploy/bin"
PORT="${PORT:-8000}"
mkdir -p "$CONF"

if [ -f "$CONF/ts/tailscaled.state" ] && [ -x "$BIN/tailscaled" ]; then
  (
    for _ in $(seq 1 60); do
      NAME=$("$BIN/tailscale" --socket="$CONF/ts/sock" status --json 2>/dev/null \
             | sed -n 's/.*"DNSName": *"\([^"]*\)\..*/\1/p' | head -1)
      [ -n "$NAME" ] && { "$BIN/tailscale" --socket="$CONF/ts/sock" status --json \
          | sed -n 's/.*"DNSName": *"\([^"]*\)\.",.*/https:\/\/\1/p' | head -1 > "$CONF/address"; break; }
      sleep 2
    done
  ) &
  exec "$BIN/tailscaled" --tun=userspace-networking --statedir="$CONF/ts" \
       --socket="$CONF/ts/sock" --socks5-server=localhost:1055
fi

CF="$(command -v cloudflared || echo "$HERE/server/cloudflared")"
if [ ! -x "$CF" ]; then
  curl -fsSL -o "$HERE/server/cloudflared" \
    https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
  chmod +x "$HERE/server/cloudflared"
  CF="$HERE/server/cloudflared"
fi
LOG="$CONF/cloudflared.log"
: > "$LOG"
"$CF" tunnel --no-autoupdate --url "http://localhost:$PORT" >> "$LOG" 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null || true' EXIT
for _ in $(seq 1 45); do
  URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | head -1 || true)
  [ -n "$URL" ] && { echo "$URL" > "$CONF/address"; echo "address: $URL"; break; }
  sleep 1
done
wait $PID
