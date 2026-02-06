#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

API_HOST="${API_HOST:-127.0.0.1}"
WEB_HOST="${WEB_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-}"
WEB_PORT="${WEB_PORT:-}"
API_BASE_URL="http://${API_HOST}:${API_PORT}"
WEB_ADMIN_KEY="${WEB_ADMIN_KEY:-${PHARMASSIST_ADMIN_API_KEY:-}}"
WEB_API_KEY="${WEB_API_KEY:-${PHARMASSIST_API_KEY:-}}"

API_PID=""
WEB_PID=""
API_LOG=""
WEB_LOG=""

is_port_busy() {
  local host="$1"
  local port="$2"
  lsof -nP -iTCP:"${port}" -sTCP:LISTEN 2>/dev/null | awk -v h="${host}" -v p="${port}" '
    NR == 1 { next }
    $9 ~ (h ":" p "$") { found = 1 }
    END { exit(found ? 0 : 1) }
  '
}

pick_free_port() {
  local host="$1"
  local start="$2"
  local port="${start}"
  while is_port_busy "${host}" "${port}"; do
    port="$((port + 1))"
  done
  echo "${port}"
}

wait_http_ready() {
  local name="$1"
  local url="$2"
  local pid="$3"
  local logfile="$4"

  for _ in $(seq 1 120); do
    if curl -fsS "${url}" >/dev/null 2>&1; then
      return 0
    fi
    if ! kill -0 "${pid}" >/dev/null 2>&1; then
      echo "${name} process exited before becoming ready." >&2
      if [[ -f "${logfile}" ]]; then
        echo "--- ${name} log ---" >&2
        tail -n 120 "${logfile}" >&2 || true
      fi
      return 1
    fi
    sleep 0.25
  done

  echo "${name} did not become ready at ${url}" >&2
  if [[ -f "${logfile}" ]]; then
    echo "--- ${name} log ---" >&2
    tail -n 120 "${logfile}" >&2 || true
  fi
  return 1
}

cleanup() {
  if [[ -n "${WEB_PID}" ]]; then
    kill "${WEB_PID}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${API_PID}" ]]; then
    kill "${API_PID}" >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT

if [[ ! -x "${ROOT}/.venv/bin/uvicorn" ]]; then
  echo "Missing .venv. Run: make setup" >&2
  exit 1
fi

if [[ -z "${API_PORT}" ]]; then
  API_PORT="$(pick_free_port "${API_HOST}" 8000)"
fi
if [[ -z "${WEB_PORT}" ]]; then
  # Use 5174 by default to avoid collisions with a manual Vite session on 5173.
  WEB_PORT="$(pick_free_port "${WEB_HOST}" 5174)"
fi
API_BASE_URL="http://${API_HOST}:${API_PORT}"
API_LOG="$(mktemp -t pharmassist-api-e2e.XXXXXX.log)"
WEB_LOG="$(mktemp -t pharmassist-web-e2e.XXXXXX.log)"

echo "Starting API on http://${API_HOST}:${API_PORT} ..."
"${ROOT}/.venv/bin/uvicorn" pharmassist_api.main:app --app-dir "${ROOT}/apps/api/src" \
  --host "${API_HOST}" --port "${API_PORT}" --log-level warning >"${API_LOG}" 2>&1 &
API_PID="$!"

echo "Starting web on http://${WEB_HOST}:${WEB_PORT} ..."
(cd "${ROOT}" && VITE_API_BASE_URL="${API_BASE_URL}" VITE_ADMIN_DB_PREVIEW_KEY="${WEB_ADMIN_KEY}" VITE_API_KEY="${WEB_API_KEY}" npm -w apps/web run dev -- --host "${WEB_HOST}" --port "${WEB_PORT}" --strictPort) \
  >"${WEB_LOG}" 2>&1 &
WEB_PID="$!"

echo "Waiting for API ..."
wait_http_ready "API" "http://${API_HOST}:${API_PORT}/healthz" "${API_PID}" "${API_LOG}"

echo "Waiting for web ..."
wait_http_ready "Web" "http://${WEB_HOST}:${WEB_PORT}/" "${WEB_PID}" "${WEB_LOG}"

echo "Ensuring Playwright Chromium is installed ..."
(cd "${ROOT}/apps/web" && npx -y playwright install chromium) >/dev/null 2>&1

echo "Running Playwright tests ..."
# Keep test output deterministic and avoid NO_COLOR/FORCE_COLOR warning spam.
(cd "${ROOT}" && env -u NO_COLOR -u FORCE_COLOR WEB_PORT="${WEB_PORT}" E2E_BASE_URL="http://${WEB_HOST}:${WEB_PORT}" npm -w apps/web run test:e2e)
