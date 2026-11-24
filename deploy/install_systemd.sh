#!/usr/bin/env bash
# Install the bot as a systemd service on a Linux VPS.
# Usage (from the hyperbot directory, as root):
#   sudo bash deploy/install_systemd.sh
#
# It writes /etc/systemd/system/hyperbot.service, enables it, and starts it.
# Review this file before running.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: run as root (sudo bash deploy/install_systemd.sh)" >&2
  exit 1
fi

HERE="$(cd "$(dirname "$0")/.." && pwd)"
PARENT="$(dirname "$HERE")"
SERVICE=/etc/systemd/system/hyperbot.service
mkdir -p "$HERE/logs"

if [ ! -f "$HERE/.env" ]; then
  cp "$HERE/deploy/env.example" "$HERE/.env"
  echo "Created $HERE/.env from template. EDIT IT with your keys, then re-run:"
  echo "  sudo bash deploy/install_systemd.sh"
  exit 0
fi

if [ ! -x "$HERE/.venv/bin/python" ]; then
  echo "ERROR: $HERE/.venv not found. Run ./setup.sh first." >&2
  exit 1
fi

if [ ! -f "$HERE/config.json" ]; then
  echo "ERROR: $HERE/config.json not found. Copy an example config first." >&2
  exit 1
fi

cat > "$SERVICE" <<EOF
[Unit]
Description=Hyperliquid trading bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$HERE
EnvironmentFile=$HERE/.env
Environment=PYTHONPATH=$PARENT
ExecStart=$HERE/.venv/bin/python -m hyperbot.run --config $HERE/config.json
Restart=always
RestartSec=10
StandardOutput=append:$HERE/logs/hyperbot.log
StandardError=append:$HERE/logs/hyperbot.log

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now hyperbot
echo "Installed. Commands:"
echo "  systemctl status hyperbot"
echo "  journalctl -u hyperbot -f"
echo "  tail -f $HERE/logs/hyperbot.log"
