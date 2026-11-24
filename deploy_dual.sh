#!/usr/bin/env bash
# Deploy TWO live bots: 4h EMA (BTC/ETH) + 1h breakout (SOL).
# Run on your Mac from the hyperbot directory:  ./deploy_dual.sh
set -eo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SRV="${DEPLOY_SRV:?请先设置 DEPLOY_SRV，例如 DEPLOY_SRV=root@your-server}"

echo "==> 1/2 upload configs (enter server password)"
scp "$HERE/config.4h_multicoin.json" "$HERE/config.1h_sol.json" "$SRV:/root/hyperbot/"

echo "==> 2/2 install dual services (enter password again)"
ssh "$SRV" 'bash -s' <<'REMOTE'
set -e
cd /root/hyperbot

# flatten any position under the currently configured bot
./start.sh config.json --flat || true

# main service -> 4h BTC/ETH
cp config.4h_multicoin.json config.json

# second service -> 1h SOL breakout
cat > /etc/systemd/system/hyperbot-1h.service <<EOF
[Unit]
Description=Hyperliquid 1h breakout bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/root/hyperbot
EnvironmentFile=/root/hyperbot/.env
Environment=PYTHONPATH=/root
ExecStart=/root/hyperbot/.venv/bin/python -m hyperbot.run --config /root/hyperbot/config.1h_sol.json
Restart=always
RestartSec=10
StandardOutput=append:/root/hyperbot/logs/bot-1h.log
StandardError=append:/root/hyperbot/logs/bot-1h.log

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl restart hyperbot
systemctl enable --now hyperbot-1h
sleep 4

echo "--- main (4h BTC/ETH) ---"
systemctl status hyperbot | grep Active || true
echo "--- 1h (SOL) ---"
systemctl status hyperbot-1h | grep Active || true
echo "--- logs ---"
tail -n 5 logs/hyperbot.log
echo "..."
tail -n 5 logs/bot-1h.log
REMOTE
