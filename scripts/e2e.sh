#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8000}"
WEB_HOST="${WEB_HOST:-127.0.0.1}"
# Use 5174 by default to ensure our API CORS config supports arbitrary Vite ports.
WEB_PORT="${WEB_PORT:-5174}"
API_BASE_URL="http://${API_HOST}:${API_PORT}"
WEB_ADMIN_KEY="${WEB_ADMIN_KEY:-${PHARMASSIST_ADMIN_API_KEY:-}}"
WEB_API_KEY="${WEB_API_KEY:-${PHARMASSIST_API_KEY:-}}"

API_PID=""
WEB_PID=""

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

echo "Starting API on http://${API_HOST}:${API_PORT} ..."
"${ROOT}/.venv/bin/uvicorn" pharmassist_api.main:app --app-dir "${ROOT}/apps/api/src" \
  --host "${API_HOST}" --port "${API_PORT}" --log-level warning >/dev/null 2>&1 &
API_PID="$!"

echo "Starting web on http://${WEB_HOST}:${WEB_PORT} ..."
(cd "${ROOT}" && VITE_API_BASE_URL="${API_BASE_URL}" VITE_ADMIN_DB_PREVIEW_KEY="${WEB_ADMIN_KEY}" VITE_API_KEY="${WEB_API_KEY}" npm -w apps/web run dev -- --host "${WEB_HOST}" --port "${WEB_PORT}" --strictPort) \
  >/dev/null 2>&1 &
WEB_PID="$!"

echo "Waiting for API ..."
for _ in $(seq 1 80); do
  if curl -fsS "http://${API_HOST}:${API_PORT}/healthz" >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done
curl -fsS "http://${API_HOST}:${API_PORT}/healthz" >/dev/null 2>&1 || {
  echo "API did not become ready" >&2
  exit 1
}

echo "Waiting for web ..."
for _ in $(seq 1 80); do
  if curl -fsS "http://${WEB_HOST}:${WEB_PORT}/" >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done
curl -fsS "http://${WEB_HOST}:${WEB_PORT}/" >/dev/null 2>&1 || {
  echo "Web did not become ready" >&2
  exit 1
}

echo "Ensuring Playwright Chromium is installed ..."
(cd "${ROOT}/apps/web" && npx -y playwright install chromium) >/dev/null 2>&1

echo "Running Playwright tests ..."
(cd "${ROOT}" && WEB_PORT="${WEB_PORT}" E2E_BASE_URL="http://${WEB_HOST}:${WEB_PORT}" npm -w apps/web run test:e2e)
