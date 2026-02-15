#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLASSIFIER="$ROOT_DIR/scripts/classify-compose-flake.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

assert_eq() {
  local expected="$1"
  local actual="$2"
  local msg="$3"
  if [[ "$expected" != "$actual" ]]; then
    echo "[FAIL] $msg expected=$expected actual=$actual"
    exit 1
  fi
}

run_case() {
  local name="$1"
  local log_content="$2"
  local ps_content="${3:-}"
  local expected="$4"
  local log_file="$TMP_DIR/${name}.log"
  local ps_file="$TMP_DIR/${name}.ps"
  local out_file="$TMP_DIR/${name}.out"

  printf '%s\n' "$log_content" > "$log_file"
  printf '%s\n' "$ps_content" > "$ps_file"

  "$CLASSIFIER" "$log_file" "$ps_file" "$out_file" >/dev/null
  local actual
  actual="$(grep '^classification=' "$out_file" | cut -d'=' -f2)"
  assert_eq "$expected" "$actual" "$name"
  echo "[OK] $name => $actual"
}

run_case "port_conflict" \
  "failed to create endpoint: Bind for 0.0.0.0:3005 failed: port is already allocated" \
  "" \
  "PORT_CONFLICT"

run_case "health_timeout" \
  "ERROR health check failed: http://127.0.0.1:3005" \
  "" \
  "HEALTHCHECK_TIMEOUT"

run_case "build_failure" \
  "ERROR: failed to solve: process \"/bin/sh -c npm ci\" did not complete successfully" \
  "" \
  "BUILD_FAILURE"

run_case "runtime_crash" \
  "startup complete" \
  "ootd-app-backend-1   Restarting (1) 3 seconds ago" \
  "RUNTIME_CRASH"

run_case "unknown" \
  "everything looks fine" \
  "ootd-app-backend-1   Up 10 seconds" \
  "UNKNOWN"

echo "[OK] classifier regression checks passed."
