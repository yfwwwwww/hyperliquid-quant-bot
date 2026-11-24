#!/usr/bin/env bash
# Upload the learning bot code and restart it, then verify.
# Run on your Mac from the hyperbot directory:  ./deploy_learn.sh
set -eo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SRV="${DEPLOY_SRV:?请先设置 DEPLOY_SRV，例如 DEPLOY_SRV=root@your-server}"

echo "==> 1/2 upload code (enter server password)"
scp "$HERE/config.py" "$HERE/engines.py" "$HERE/hf_bot.py" \
    "$HERE/scanner.py" "$HERE/orderflow.py" "$HERE/oi.py" "$HERE/journal.py" \
    "$HERE/config.hf_learn.json" "$SRV:/root/hyperbot/"

echo "==> 2/2 restart + verify (enter password again)"
ssh "$SRV" 'bash -s' <<'REMOTE'
set -e
pkill -f -- "--hf-paper" || true
cd /root/hyperbot
nohup ./start.sh config.hf_learn.json --hf-paper > logs/hf_learn.log 2>&1 &
sleep 6
echo "--- code check (must be >=1) ---"
grep -c "hf_min_score" hf_bot.py || true
echo "--- config ---"
grep -E "hf_min_score|hf_mtf_min|hf_cooldown" config.hf_learn.json || true
echo "--- log ---"
tail -n 5 logs/hf_learn.log
echo "--- ledger ---"
wc -l ledger_hf_learn.jsonl || true
REMOTE
