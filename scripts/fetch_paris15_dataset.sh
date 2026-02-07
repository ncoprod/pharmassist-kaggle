#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

DATASET_REF="${KAGGLE_DATASET_REF:-nicolascoquelet/pharmassist-pharmacy-year-paris15-2025-v1}"
OUT_DIR="${1:-${OUT_DIR:-.data/paris15_full}}"
TMP_DIR="$(mktemp -d "${ROOT}/.data/kaggle_ds.XXXXXX")"
trap 'rm -rf "${TMP_DIR}"' EXIT

KAGGLE_BIN="${KAGGLE_BIN:-}"
if [[ -z "${KAGGLE_BIN}" ]]; then
  if command -v kaggle >/dev/null 2>&1; then
    KAGGLE_BIN="$(command -v kaggle)"
  elif [[ -x "${ROOT}/.venv/bin/kaggle" ]]; then
    KAGGLE_BIN="${ROOT}/.venv/bin/kaggle"
  else
    echo "[dataset] kaggle CLI not found."
    echo "[dataset] install with: .venv/bin/pip install kaggle"
    exit 1
  fi
fi

mkdir -p "${ROOT}/.data"
echo "[dataset] downloading ${DATASET_REF} ..."
"${KAGGLE_BIN}" datasets download -d "${DATASET_REF}" -p "${TMP_DIR}" --force

ZIP_PATH="$(ls "${TMP_DIR}"/*.zip 2>/dev/null | head -n 1 || true)"
if [[ -z "${ZIP_PATH}" ]]; then
  echo "[dataset] no zip file produced by kaggle CLI."
  exit 1
fi

rm -rf "${OUT_DIR}"
mkdir -p "${OUT_DIR}"
unzip -q "${ZIP_PATH}" -d "${OUT_DIR}"

required_base=(patients visits events inventory)
for base in "${required_base[@]}"; do
  gz="${OUT_DIR}/${base}.jsonl.gz"
  raw="${OUT_DIR}/${base}.jsonl"
  if [[ -f "${gz}" ]]; then
    continue
  fi
  if [[ -f "${raw}" ]]; then
    echo "[dataset] compressing ${base}.jsonl -> ${base}.jsonl.gz"
    gzip -c "${raw}" > "${gz}"
    continue
  fi
  echo "[dataset] missing required file: ${gz} (or ${raw})"
  exit 1
done

echo "[dataset] ok: ${OUT_DIR}"
ls -lh "${OUT_DIR}"
