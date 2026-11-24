#!/usr/bin/env bash
# Deploy the 1h breakout 4%/5x config + the required code files to live.
# Run on your Mac from the hyperbot directory:  ./deploy_live.sh
set -eo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SRV="${DEPLOY_SRV:?请先设置 DEPLOY_SRV，例如 DEPLOY_SRV=root@your-server}"

echo "==> 1/2 upload code + config (enter server password)"
scp "$HERE/breakout_bot.py" "$HERE/config.py" "$HERE/run.py" \
    "$HERE/config.breakout_1h_4pct.example.json" "$SRV:/root/hyperbot/"

echo "==> 2/2 flatten old position, switch config, restart (enter password again)"
ssh "$SRV" 'bash -s' <<'REMOTE'
set -e
cd /root/hyperbot
./start.sh config.json --flat || true
cp config.breakout_1h_4pct.example.json config.json
sudo systemctl restart hyperbot
sleep 4
echo "--- config ---"
grep -E 'interval|strategy_mode|coins|risk_per_trade|leverage|min_equity_stop' config.json
echo "--- log ---"
tail -n 10 logs/hyperbot.log
echo "--- status ---"
systemctl status hyperbot | grep Active || true
REMOTE
