#!/usr/bin/env bash
# Simple background daemon for the bot (nohup + pidfile).
# For true 24/7 on a server, prefer systemd/launchd (see deploy/README.md).
#
# Usage:
#   ./run_daemon.sh start            # default: config.paper.lowrisk.json
#   CONFIG=config.lowrisk.example.json ./run_daemon.sh start
#   ./run_daemon.sh status
#   ./run_daemon.sh report
#   ./run_daemon.sh stop
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
VENV="${VENV:-$HERE/.venv}"
if [ -z "${CONFIG:-}" ]; then
  if [ -f "$HERE/config.json" ]; then
    CONFIG="$HERE/config.json"
  else
    CONFIG="$HERE/config.paper.lowrisk.json"
  fi
fi
LOG_DIR="$HERE/logs"
LOG="$LOG_DIR/bot.log"
PIDFILE="$LOG_DIR/bot.pid"
mkdir -p "$LOG_DIR"

start() {
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "already running: pid=$(cat "$PIDFILE")"
    return 0
  fi
  nohup env VENV="$VENV" "$HERE/start.sh" "$CONFIG" >> "$LOG" 2>&1 &
  echo $! > "$PIDFILE"
  sleep 3
  if kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "started: pid=$(cat "$PIDFILE")  config=$CONFIG"
    echo "log: $LOG"
  else
    echo "failed to start; last log lines:"
    tail -n 20 "$LOG" || true
    rm -f "$PIDFILE"
    exit 1
  fi
}

stop() {
  if [ -f "$PIDFILE" ]; then
    pid="$(cat "$PIDFILE")"
    kill "$pid" 2>/dev/null || true
    sleep 1
    kill -9 "$pid" 2>/dev/null || true
    rm -f "$PIDFILE"
    echo "stopped (pid=$pid)"
  else
    echo "not running (no pidfile)"
  fi
}

status() {
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    pid="$(cat "$PIDFILE")"
    echo "running: pid=$pid"
    ps -p "$pid" -o pid,etime,command | tail -n +2 || true
  else
    echo "not running"
  fi
}

report() {
  exec env VENV="$VENV" "$HERE/start.sh" "$CONFIG" --report
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  report) report ;;
  *)
    echo "usage: $0 {start|stop|status|report}"
    exit 1
    ;;
esac
