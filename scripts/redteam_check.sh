#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

API_PORT="${API_PORT:-8010}"
API_HOST="${API_HOST:-127.0.0.1}"
API_BASE="http://${API_HOST}:${API_PORT}"
ADMIN_KEY="${ADMIN_KEY:-redteam-secret}"
DB_PATH="${DB_PATH:-${ROOT}/.data/redteam.db}"
LOG_PATH="${LOG_PATH:-${ROOT}/.data/redteam_api.log}"

mkdir -p "${ROOT}/.data"
rm -f "${DB_PATH}"

echo "[redteam] starting API on ${API_BASE}"
PYTHONPATH=apps/api/src \
PHARMASSIST_DB_PATH="${DB_PATH}" \
PHARMASSIST_ADMIN_API_KEY="${ADMIN_KEY}" \
PHARMASSIST_ADMIN_RATE_LIMIT_MAX=3 \
PHARMASSIST_ADMIN_RATE_LIMIT_WINDOW_SEC=60 \
.venv/bin/uvicorn pharmassist_api.main:app --host "${API_HOST}" --port "${API_PORT}" >"${LOG_PATH}" 2>&1 &
API_PID=$!
cleanup() {
  kill "${API_PID}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

for _ in $(seq 1 60); do
  if curl -sf "${API_BASE}/healthz" >/dev/null; then
    break
  fi
  sleep 0.2
done

if ! curl -sf "${API_BASE}/healthz" >/dev/null; then
  echo "[redteam] API failed to start. Logs:"
  cat "${LOG_PATH}"
  exit 1
fi

assert_status() {
  local expected="$1"
  shift
  local got
  got="$(curl -s -o /dev/null -w "%{http_code}" "$@")"
  if [[ "${got}" != "${expected}" ]]; then
    echo "[redteam] expected HTTP ${expected}, got ${got}: $*"
    echo "[redteam] API logs:"
    tail -n 200 "${LOG_PATH}" || true
    exit 1
  fi
}

echo "[redteam] check unauthorized access -> 401"
assert_status 401 "${API_BASE}/admin/db-preview/tables"

echo "[redteam] check wrong admin key -> 401"
assert_status 401 -H "X-Admin-Key: wrong" "${API_BASE}/admin/db-preview/tables"

echo "[redteam] check valid key -> 200"
assert_status 200 -H "X-Admin-Key: ${ADMIN_KEY}" "${API_BASE}/admin/db-preview/tables"

echo "[redteam] check SQLi-like table input is rejected -> 400"
assert_status 400 -G -H "X-Admin-Key: ${ADMIN_KEY}" --data-urlencode "table=patients;drop table runs" "${API_BASE}/admin/db-preview"

echo "[redteam] check rate limit -> 429"
assert_status 429 -H "X-Admin-Key: ${ADMIN_KEY}" "${API_BASE}/admin/db-preview/tables"

echo "[redteam] all runtime checks passed"
