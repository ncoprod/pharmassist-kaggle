#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

API_PORT="${API_PORT:-8010}"
API_HOST="${API_HOST:-127.0.0.1}"
API_BASE="http://${API_HOST}:${API_PORT}"
ADMIN_KEY="${ADMIN_KEY:-redteam-secret}"
API_KEY="${API_KEY:-redteam-app-key}"
DB_PATH="${DB_PATH:-${ROOT}/.data/redteam.db}"
LOG_PATH="${LOG_PATH:-${ROOT}/.data/redteam_api.log}"

mkdir -p "${ROOT}/.data"
rm -f "${DB_PATH}"

# macOS shells can inherit a low soft nofile limit (e.g. 256) from launchctl.
# Uvicorn/FastAPI startup may then fail before health checks with Errno 24.
MIN_NOFILE="${MIN_NOFILE:-4096}"
CURRENT_NOFILE="$(ulimit -Sn)"
if [[ "${CURRENT_NOFILE}" -lt "${MIN_NOFILE}" ]]; then
  if ulimit -Sn "${MIN_NOFILE}" 2>/dev/null; then
    CURRENT_NOFILE="$(ulimit -Sn)"
    echo "[redteam] raised open files soft limit to ${CURRENT_NOFILE}"
  else
    echo "[redteam] soft open files limit too low (${CURRENT_NOFILE}), cannot raise to ${MIN_NOFILE}"
    echo "[redteam] run: ulimit -n ${MIN_NOFILE} (or higher) and retry"
    exit 1
  fi
fi

echo "[redteam] starting API on ${API_BASE}"
PYTHONPATH=apps/api/src \
PHARMASSIST_DB_PATH="${DB_PATH}" \
PHARMASSIST_ADMIN_API_KEY="${ADMIN_KEY}" \
PHARMASSIST_API_KEY="${API_KEY}" \
PHARMASSIST_MAX_PRESCRIPTION_UPLOAD_BYTES=512 \
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

mktemp_file() {
  mktemp "${ROOT}/.data/redteam.XXXXXX"
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

echo "[redteam] check data endpoint auth guard -> 401"
assert_status 401 "${API_BASE}/patients?query=pt_0000"
assert_status 200 -H "X-Api-Key: ${API_KEY}" "${API_BASE}/patients?query=pt_0000"
assert_status 401 "${API_BASE}/patients/inbox"
assert_status 200 -H "X-Api-Key: ${API_KEY}" "${API_BASE}/patients/inbox"
assert_status 401 "${API_BASE}/patients/pt_000000/analysis-status"
assert_status 200 -H "X-Api-Key: ${API_KEY}" "${API_BASE}/patients/pt_000000/analysis-status"
assert_status 401 -X POST -H "Content-Type: application/json" -d '{"reason":"redteam"}' "${API_BASE}/patients/pt_000000/refresh"
assert_status 200 -X POST -H "X-Api-Key: ${API_KEY}" -H "Content-Type: application/json" -d '{"reason":"redteam"}' "${API_BASE}/patients/pt_000000/refresh"

TXT_FILE="$(mktemp_file)"
PDF_SMALL="$(mktemp_file)"
PDF_BIG="$(mktemp_file)"
PDF_BAD_HEADER="$(mktemp_file)"
trap 'rm -f "${TXT_FILE}" "${PDF_SMALL}" "${PDF_BIG}" "${PDF_BAD_HEADER}"; cleanup' EXIT

echo "not a pdf" >"${TXT_FILE}"
printf '%%PDF-1.4\nfake\n' >"${PDF_SMALL}"
printf 'notpdf' >"${PDF_BAD_HEADER}"
python3 - <<PY
from pathlib import Path
p = Path("${PDF_BIG}")
p.write_bytes((b"%PDF-1.4\\n" + (b"A" * 120000)))
PY

echo "[redteam] check upload requires auth -> 401"
assert_status 401 -F "patient_ref=pt_000000" -F "language=en" -F "file=@${PDF_SMALL};type=application/pdf;filename=rx.pdf" "${API_BASE}/documents/prescription"

echo "[redteam] check non-pdf upload -> 415"
assert_status 415 -H "X-Api-Key: ${API_KEY}" -F "patient_ref=pt_000000" -F "language=en" -F "file=@${TXT_FILE};type=text/plain" "${API_BASE}/documents/prescription"

echo "[redteam] check oversize upload -> 413"
assert_status 413 -H "X-Api-Key: ${API_KEY}" -F "patient_ref=pt_000000" -F "language=en" -F "file=@${PDF_BIG};type=application/pdf;filename=rx_big.pdf" "${API_BASE}/documents/prescription"

echo "[redteam] check malformed pdf -> 400"
assert_status 400 -H "X-Api-Key: ${API_KEY}" -F "patient_ref=pt_000000" -F "language=en" -F "file=@${PDF_SMALL};type=application/pdf;filename=rx_small.pdf" "${API_BASE}/documents/prescription"

echo "[redteam] check invalid pdf header with pdf mime -> 400"
assert_status 400 -H "X-Api-Key: ${API_KEY}" -F "patient_ref=pt_000000" -F "language=en" -F "file=@${PDF_BAD_HEADER};type=application/pdf;filename=rx_bad.pdf" "${API_BASE}/documents/prescription"

echo "[redteam] all runtime checks passed"
