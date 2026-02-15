#!/usr/bin/env bash
set -euo pipefail

LOG_FILE="${1:-artifacts/compose-logs.txt}"
PS_FILE="${2:-artifacts/compose-ps.txt}"
OUT_FILE="${3:-artifacts/compose-flake-classification.txt}"

mkdir -p "$(dirname "$OUT_FILE")"

if [[ ! -f "$LOG_FILE" ]]; then
  {
    echo "classification=UNKNOWN"
    echo "reason=compose logs not found"
    echo "log_file=$LOG_FILE"
    echo "ps_file=$PS_FILE"
  } | tee "$OUT_FILE"
  exit 0
fi

lc_log="$(tr '[:upper:]' '[:lower:]' < "$LOG_FILE")"
lc_ps=""
if [[ -f "$PS_FILE" ]]; then
  lc_ps="$(tr '[:upper:]' '[:lower:]' < "$PS_FILE")"
fi

classification="UNKNOWN"
reason="no matching rule"

if grep -Eq "port [0-9]+ is already in use|address already in use|bind: address already in use" <<< "$lc_log"; then
  classification="PORT_CONFLICT"
  reason="detected already-in-use port/bind failure"
elif grep -Eq "health check failed|timed out|timeout|unhealthy|context deadline exceeded" <<< "$lc_log"; then
  classification="HEALTHCHECK_TIMEOUT"
  reason="detected health/readiness timeout signal"
elif grep -Eq "failed to solve|error: failed to|executor failed running|buildx failed|failed to compute cache key|npm err!|pip .*error" <<< "$lc_log"; then
  classification="BUILD_FAILURE"
  reason="detected image/dependency build failure"
elif grep -Eq "restarting|unhealthy|exit [1-9]" <<< "$lc_ps"; then
  classification="RUNTIME_CRASH"
  reason="container restart/crash signal from compose ps"
fi

{
  echo "classification=$classification"
  echo "reason=$reason"
  echo "log_file=$LOG_FILE"
  echo "ps_file=$PS_FILE"
} | tee "$OUT_FILE"
