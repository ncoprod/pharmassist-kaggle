#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

DB_PATH="${DB_PATH:-${ROOT}/.data/pharmassist_full.db}"
DATA_DIR="${DATA_DIR:-${ROOT}/.data/paris15_full}"
PYTHON_BIN="${PYTHON_BIN:-${ROOT}/.venv/bin/python}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "[dataset-check] python not found at ${PYTHON_BIN}"
  exit 1
fi

required=(patients.jsonl.gz visits.jsonl.gz events.jsonl.gz inventory.jsonl.gz)
for f in "${required[@]}"; do
  if [[ ! -f "${DATA_DIR}/${f}" ]]; then
    echo "[dataset-check] missing ${DATA_DIR}/${f}"
    exit 1
  fi
done

# Some macOS shells inherit a low soft nofile limit (e.g. 256) that can
# intermittently break large validation passes with Errno 24.
MIN_NOFILE="${MIN_NOFILE:-4096}"
CURRENT_NOFILE="$(ulimit -Sn)"
if [[ "${CURRENT_NOFILE}" -lt "${MIN_NOFILE}" ]]; then
  if ulimit -Sn "${MIN_NOFILE}" 2>/dev/null; then
    CURRENT_NOFILE="$(ulimit -Sn)"
    echo "[dataset-check] raised open files soft limit to ${CURRENT_NOFILE}"
  else
    echo "[dataset-check] soft open files limit too low (${CURRENT_NOFILE}), cannot raise to ${MIN_NOFILE}"
    echo "[dataset-check] run: ulimit -n ${MIN_NOFILE} (or higher) and retry"
    exit 1
  fi
fi

mkdir -p "$(dirname "${DB_PATH}")"
if [[ -d "${DB_PATH}" ]]; then
  echo "[dataset-check] DB_PATH points to a directory: ${DB_PATH}"
  exit 1
fi
rm -f "${DB_PATH}"

echo "[dataset-check] db=${DB_PATH}"
echo "[dataset-check] data=${DATA_DIR}"

PHARMASSIST_DB_PATH="${DB_PATH}" \
PHARMASSIST_PHARMACY_DATA_DIR="${DATA_DIR}" \
PYTHONPATH="apps/api/src" \
"${PYTHON_BIN}" - <<'PY'
from pharmassist_api import db
from pharmassist_api.pharmacy.load_dataset import ensure_pharmacy_dataset_loaded

db.init_db()
res = ensure_pharmacy_dataset_loaded()
patients = db.count_patients()
visits = db.count_visits()
inventory = db.count_inventory()

print(res)
print("patients", patients)
print("visits", visits)
print("inventory", inventory)

assert patients > 0, "patients must be > 0"
assert visits > 0, "visits must be > 0"
assert inventory > 0, "inventory must be > 0"
PY

echo "[dataset-check] PASS"
