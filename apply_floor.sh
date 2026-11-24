#!/usr/bin/env bash
# One-shot: upload bot.py/config.py, set the $200 equity floor, restart, show log.
# Run on your Mac from the hyperbot directory:  ./apply_floor.sh
set -eo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SRV="${DEPLOY_SRV:?请先设置 DEPLOY_SRV，例如 DEPLOY_SRV=root@your-server}"

echo "==> 1/2 uploading bot.py config.py (enter server password)"
scp "$HERE/bot.py" "$HERE/config.py" "$SRV:/root/hyperbot/"

echo "==> 2/2 set min_equity_stop=200, restart, show log (enter password again)"
ssh "$SRV" 'bash -s' <<'REMOTE'
set -e
cd /root/hyperbot
python3 - <<'PY'
import json
p = 'config.json'
d = json.load(open(p))
d['min_equity_stop'] = 200.0
json.dump(d, open(p, 'w'), indent=2)
print('min_equity_stop =', d['min_equity_stop'])
PY
systemctl restart hyperbot
sleep 4
echo "--- log ---"
tail -n 20 logs/hyperbot.log
echo "--- status ---"
systemctl status hyperbot | grep Active || true
REMOTE
