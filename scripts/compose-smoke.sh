#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/docker-compose.yml"
FRONTEND_PORT="${FRONTEND_PORT:-3005}"
AUTO_DOWN="${AUTO_DOWN:-1}"

cleanup() {
  if [[ "$AUTO_DOWN" == "1" ]]; then
    docker compose -f "$COMPOSE_FILE" down --remove-orphans >/dev/null
  fi
}
trap cleanup EXIT

wait_http_200() {
  local url="$1"
  local tries="${2:-60}"
  local sleep_sec="${3:-2}"

  for _ in $(seq 1 "$tries"); do
    if curl -s -o /dev/null -w '%{http_code}' "$url" | grep -q '^200$'; then
      return 0
    fi
    sleep "$sleep_sec"
  done

  echo "[ERROR] health check failed: $url"
  return 1
}

echo "[INFO] preflight check for frontend port: $FRONTEND_PORT"
"$ROOT_DIR/scripts/preflight-port.sh" "$FRONTEND_PORT"

echo "[INFO] starting docker compose stack"
FRONTEND_PORT="$FRONTEND_PORT" docker compose -f "$COMPOSE_FILE" up -d --build

echo "[INFO] waiting for backend and frontend readiness"
wait_http_200 "http://127.0.0.1:8000/docs"
wait_http_200 "http://127.0.0.1:$FRONTEND_PORT"

tmp_img="$(mktemp --suffix=.jpg)"
printf 'fake-image-bytes' > "$tmp_img"
trap 'rm -f "$tmp_img"; cleanup' EXIT

echo "[INFO] running API smoke: POST /v1/jobs"
api_resp="$(curl -sS -X POST "http://127.0.0.1:8000/v1/jobs" \
  -F "image=@${tmp_img};type=image/jpeg" \
  -F "look_count=3" \
  -F "quality_mode=auto_gate")"

if ! echo "$api_resp" | grep -q '"job_id"'; then
  echo "[ERROR] API smoke failed: $api_resp"
  exit 1
fi

echo "[OK] Compose smoke test passed."
echo "[INFO] frontend=http://localhost:$FRONTEND_PORT backend=http://localhost:8000/docs"
