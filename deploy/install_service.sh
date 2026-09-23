#!/usr/bin/env bash
# Install the audiobook server as a background service on this computer, so it starts by itself
# after a reboot and you never have to run it from a terminal again.
#
#   bash deploy/install_service.sh              # server only (reachable on your Wi-Fi)
#   bash deploy/install_service.sh --tunnel     # + a free https address for the phone app
#
# Uninstall:  bash deploy/install_service.sh --remove
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
UNITS="$HOME/.config/systemd/user"
CONF="$HOME/.config/audiobook"
PORT="${PORT:-8000}"

if [ "${1:-}" = "--remove" ]; then
  systemctl --user disable --now audiobook.service audiobook-tunnel.service 2>/dev/null || true
  rm -f "$UNITS/audiobook.service" "$UNITS/audiobook-tunnel.service"
  systemctl --user daemon-reload
  echo "Removed. The library in $CONF and your books are untouched."
  exit 0
fi

mkdir -p "$UNITS" "$CONF"

# Dependencies, same as run_local.sh, so the service starts with everything in place.
cd "$HERE/server"
command -v uv >/dev/null || { curl -LsSf https://astral.sh/uv/install.sh | sh; export PATH="$HOME/.local/bin:$PATH"; }
[ -x .venv/bin/python ] || uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -q -r requirements.txt -r requirements-kokoro.txt

# Settings live in one file you can edit later (the access code, where books are kept).
if [ ! -f "$CONF/env" ]; then
  cat > "$CONF/env" <<ENV
AUDIOBOOK_DATA=${AUDIOBOOK_DATA:-$HERE/server/local-data}
AUDIOBOOK_TOKEN=${AUDIOBOOK_TOKEN:-$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(9))')}
AUDIOBOOK_ENGINE=${AUDIOBOOK_ENGINE:-kokoro}
PORT=$PORT
ENV
fi
# shellcheck disable=SC1091
set -a; . "$CONF/env"; set +a

cat > "$UNITS/audiobook.service" <<UNIT
[Unit]
Description=Audiobook server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$HERE/server
EnvironmentFile=$CONF/env
ExecStart=$HERE/server/.venv/bin/python -m audiobook --port \${PORT}
Restart=always
RestartSec=5
# Narration is a long background job: keep it off the cores you are working on.
Nice=10
IOSchedulingClass=idle

[Install]
WantedBy=default.target
UNIT

cat > "$UNITS/audiobook-tunnel.service" <<UNIT
[Unit]
Description=Audiobook https address
After=audiobook.service
BindsTo=audiobook.service

[Service]
Type=simple
WorkingDirectory=$HERE
EnvironmentFile=$CONF/env
ExecStart=$HERE/deploy/tunnel.sh
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
UNIT

loginctl enable-linger "$USER" >/dev/null 2>&1 || true   # keeps it running when you are logged out
systemctl --user daemon-reload
systemctl --user enable --now audiobook.service
[ "${1:-}" = "--tunnel" ] && systemctl --user enable --now audiobook-tunnel.service

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
sleep 2
echo
echo "────────────────────────────────────────────────────────────"
echo " The server now starts by itself, including after a reboot."
echo "   on this computer : http://localhost:$PORT"
echo "   on your Wi-Fi    : http://${IP:-<this-computer-ip>}:$PORT"
echo "   access code      : ${AUDIOBOOK_TOKEN:-none}"
[ "${1:-}" = "--tunnel" ] && echo "   https address    : see  cat $CONF/address  (a few seconds after start)"
echo
echo " status:  systemctl --user status audiobook"
echo " log:     journalctl --user -u audiobook -f"
echo " stop:    systemctl --user stop audiobook"
echo "────────────────────────────────────────────────────────────"
