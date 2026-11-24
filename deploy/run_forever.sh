#!/usr/bin/env bash
# Supervisor loop: runs the bot and restarts it on crash with exponential
# backoff. Use this if you do NOT have systemd / launchd.
#
# Usage:
#   CONFIG=config.json VENV=.venv ./deploy/run_forever.sh
#
# Logs are appended to logs/hyperbot.log.
set -uo pipefail

cd "$(dirname "$0")/.."
VENV="${VENV:-.venv}"
CONFIG="${CONFIG:-config.json}"
LOG_DIR="${LOG_DIR:-logs}"
LOG_FILE="$LOG_DIR/hyperbot.log"
mkdir -p "$LOG_DIR"

restart_delay=5
while true; do
  echo "=== $(date -u +%FT%TZ) starting bot (config=$CONFIG) ===" >> "$LOG_FILE"
  "$VENV/bin/python" -m hyperbot.run --config "$CONFIG" >> "$LOG_FILE" 2>&1
  code=$?
  if [ "$code" -eq 0 ]; then
    echo "=== $(date -u +%FT%TZ) bot exited cleanly; stopping supervisor ===" >> "$LOG_FILE"
    break
  fi
  echo "=== $(date -u +%FT%TZ) bot exited code=$code; restarting in ${restart_delay}s ===" >> "$LOG_FILE"
  sleep "$restart_delay"
  restart_delay=$(( restart_delay * 2 ))
  [ "$restart_delay" -gt 60 ] && restart_delay=60
done
