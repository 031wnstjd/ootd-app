#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-3005}"

if command -v ss >/dev/null 2>&1; then
  if ss -ltn "( sport = :$PORT )" | tail -n +2 | grep -q .; then
    echo "[ERROR] Port $PORT is already in use."
    echo "Try: FRONTEND_PORT=39005 docker compose up -d --build"
    exit 1
  fi
elif command -v lsof >/dev/null 2>&1; then
  if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "[ERROR] Port $PORT is already in use."
    echo "Try: FRONTEND_PORT=39005 docker compose up -d --build"
    exit 1
  fi
fi

echo "[OK] Port $PORT is available."
