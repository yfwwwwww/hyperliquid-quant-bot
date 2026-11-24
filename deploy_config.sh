#!/usr/bin/env bash
# Deploy any config file to the server and restart the bot.
# Usage (on your Mac, from the hyperbot dir):
#   ./deploy_config.sh config.ema_multicoin.example.json
set -eo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SRV="${DEPLOY_SRV:?请先设置 DEPLOY_SRV，例如 DEPLOY_SRV=root@your-server}"
CFG="${1:?usage: ./deploy_config.sh <config-file>}"

echo "==> 1/2 upload $CFG (enter server password)"
scp "$HERE/$CFG" "$SRV:/root/hyperbot/"

echo "==> 2/2 flatten old position, switch config, restart (enter password again)"
ssh "$SRV" 'bash -s' <<REMOTE
set -e
cd /root/hyperbot
./start.sh config.json --flat || true
cp /root/hyperbot/$CFG config.json
sudo systemctl restart hyperbot
sleep 4
echo "--- config ---"
grep -E 'strategy_mode|coins|interval|risk_per_trade|leverage|min_equity_stop' config.json
echo "--- log ---"
tail -n 10 logs/hyperbot.log
echo "--- status ---"
systemctl status hyperbot | grep Active || true
REMOTE
