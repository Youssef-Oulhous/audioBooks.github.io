#!/usr/bin/env bash
# One-time: give this computer a permanent https address (Tailscale Funnel), free, no domain and
# no admin rights needed — Tailscale runs in user space here.
#
#   bash deploy/setup_tailscale.sh
#
# It prints a link to sign in (Google/GitHub/email), then the address of your server, which never
# changes again. After that the audiobook-tunnel service uses it on every boot.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
CONF="$HOME/.config/audiobook"
BIN="$HERE/deploy/bin"
PORT="${PORT:-8000}"
mkdir -p "$BIN" "$CONF/ts"
chmod 700 "$CONF/ts"

if [ ! -x "$BIN/tailscaled" ]; then
  ARCH=$(uname -m); case "$ARCH" in x86_64) ARCH=amd64;; aarch64) ARCH=arm64;; esac
  VER=$(curl -fsSL "https://pkgs.tailscale.com/stable/?mode=json" \
        | grep -oE "tailscale_[0-9.]+_${ARCH}\.tgz" | head -1)
  [ -n "$VER" ] || { echo "Could not find the Tailscale download." >&2; exit 1; }
  echo "Downloading $VER …"
  curl -fsSL "https://pkgs.tailscale.com/stable/$VER" -o "$BIN/ts.tgz"
  tar -xzf "$BIN/ts.tgz" -C "$BIN" --strip-components=1 --wildcards '*/tailscale' '*/tailscaled'
  rm -f "$BIN/ts.tgz"
fi

"$BIN/tailscaled" --tun=userspace-networking --statedir="$CONF/ts" --socket="$CONF/ts/sock" \
  --socks5-server=localhost:1055 > "$CONF/tailscaled.log" 2>&1 &
DPID=$!
trap 'kill $DPID 2>/dev/null || true' EXIT
sleep 3

TS=("$BIN/tailscale" --socket="$CONF/ts/sock")
"${TS[@]}" up --hostname=audiobooks --accept-dns=false
"${TS[@]}" funnel --bg "$PORT"

URL=$("${TS[@]}" status --json | sed -n 's/.*"DNSName": *"\([^"]*\)\.",.*/https:\/\/\1/p' | head -1)
echo "$URL" > "$CONF/address"
CODE=$(sed -n 's/^AUDIOBOOK_TOKEN=//p' "$CONF/env" 2>/dev/null | head -1)

kill $DPID 2>/dev/null || true
systemctl --user restart audiobook-tunnel.service 2>/dev/null || true

echo
echo "────────────────────────────────────────────────────────────"
echo " Permanent address for your server (it will not change):"
echo "   $URL"
[ -n "$CODE" ] && echo " Open this once on your phone, in the hosted app:"
[ -n "$CODE" ] && echo "   <your app address>/?server=$URL&pair=$CODE"
echo "────────────────────────────────────────────────────────────"
