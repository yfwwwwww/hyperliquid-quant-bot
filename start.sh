#!/usr/bin/env bash
# Convenience launcher: works from any directory.
#
# Usage:
#   ./hyperbot/start.sh                                  # paper, config.json
#   ./hyperbot/start.sh hyperbot/config.lowrisk.example.json
#   ./hyperbot/start.sh hyperbot/config.scalp_paper.example.json --scalp-paper
#   ./hyperbot/start.sh hyperbot/config.json --report
#
# VENV defaults to <hyperbot>/.venv; override with VENV=/path/to/venv
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
VENV="${VENV:-$HERE/.venv}"
CONFIG="${1:-$HERE/config.json}"
if [ $# -gt 0 ]; then shift; fi

# Resolve a relative config path against the caller's directory.
if [[ "$CONFIG" != /* ]]; then
  CONFIG="$PWD/$CONFIG"
fi

# Load .env if present (systemd uses EnvironmentFile instead).
if [ -f "$HERE/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$HERE/.env"
  set +a
fi

# Run with cwd=hyperbot so state.json / ledger.jsonl land there, while
# PYTHONPATH points at the parent so `-m hyperbot.run` resolves.
cd "$HERE"
exec env PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" "$VENV/bin/python" -m hyperbot.run --config "$CONFIG" "$@"
