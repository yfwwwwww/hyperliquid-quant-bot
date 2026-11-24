#!/usr/bin/env bash
# One-shot setup: venv + dependencies + default config.
# Then use ./start.sh or ./run_daemon.sh.
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3.10}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "ERROR: $PY not found. Install Python 3.10 first (official SDK needs 3.10)." >&2
  exit 1
fi

echo "[1/3] creating venv (.venv) ..."
"$PY" -m venv .venv

echo "[2/3] installing dependencies ..."
.venv/bin/pip install --upgrade pip >/dev/null
.venv/bin/pip install -r requirements.txt

echo "[3/3] preparing config ..."
if [ ! -f config.json ]; then
  cp config.paper.lowrisk.json config.json
  echo "created config.json from config.paper.lowrisk.json (paper mode)"
fi

echo
echo "OK. Next:"
echo "  ./start.sh config.json --report     # 看业绩"
echo "  ./start.sh config.json              # 前台跑（Ctrl+C 停）"
echo "  ./run_daemon.sh start               # 后台常驻"
