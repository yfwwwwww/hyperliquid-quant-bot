#!/usr/bin/env bash
# Upload dashboard files, restart the service, and print diagnostics.
# Run on your Mac from the hyperbot directory:  ./deploy_dashboard.sh
set -eo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SRV="${DEPLOY_SRV:?请先设置 DEPLOY_SRV，例如 DEPLOY_SRV=root@your-server}"

echo "==> 1/2 upload dashboard.py client.py config.py (enter server password)"
scp "$HERE/dashboard.py" "$HERE/client.py" "$HERE/config.py" "$SRV:/root/hyperbot/"

echo "==> 2/2 restart + diagnose (enter password again)"
ssh "$SRV" 'bash -s' <<'REMOTE'
set -e
systemctl restart hyperbot-dashboard
sleep 2
echo "--- status ---"
systemctl status hyperbot-dashboard | grep Active || true
echo "--- api state ---"
curl -s -m 10 -o /tmp/st.json -w "HTTP %{http_code}\n" http://127.0.0.1/api/state || true
head -c 300 /tmp/st.json || true
echo
echo "--- api scan ---"
curl -s -m 10 http://127.0.0.1/api/scan | head -c 200 || true
echo
echo "--- dashboard log ---"
tail -n 15 /root/hyperbot/logs/dashboard.log || true
REMOTE
