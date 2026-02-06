# PharmAssist AI — Kaggle MedGemma Impact Challenge

Synthetic-only, privacy-first demo application for the **Kaggle MedGemma Impact Challenge (2026)**.

What this repo demonstrates:
- **Strict contracts**: JSON Schemas + golden examples.
- **Policy validators**: PHI boundary (hard-stop) + Rx advice linting.
- **Agentic workflow**: a step-by-step orchestrator with an audit trace + SSE progress in the UI.
- **HAI-DEF usage**: optional model-backed intake extraction with **MedGemma**, with a deterministic fallback
  so CI stays fast and reproducible.
- **Safe LLM routing**: optional MedGemma follow-up selection that can only choose from a closed
  `question_id` allowlist (no free-form question generation).
- **No real patient data**: synthetic cases only.
- **Near‑MVP realism**: synthetic **patients + visits** and a “Start run from visit” flow.

Repo layout:
- `packages/contracts/`: canonical JSON Schemas + examples
- `apps/api/`: FastAPI orchestrator + pipeline steps
- `apps/api/src/pharmassist_api/pharmacy/`: synthetic pharmacy dataset loader + `/patients` endpoints
- `apps/web/`: Vite UI (start run + SSE progress)

## Patients + Visits (synthetic pharmacy-year)

On startup, the API seeds SQLite with a **tiny committed subset**:
- `apps/api/src/pharmassist_api/pharmacy/fixtures/paris15_mini/*.jsonl.gz`

Endpoints:
- `GET /patients?query=pt_0000`
- `GET /patients/{patient_ref}`
- `GET /patients/{patient_ref}/visits`
- `GET /admin/db-preview/tables`
- `GET /admin/db-preview?table=patients&query=pt_0000&limit=50`

Run creation:
- `POST /runs` accepts `visit_ref` (+ optional `patient_ref`) and resolves a case bundle from the dataset
  **without any PHI** (no OCR/PDF text stored).

DB viewer:
- Read-only and redacted by design.
- Returns compact table previews only (no raw OCR/PDF text, no free-form payload blobs).
- App/API hardening:
  - if `PHARMASSIST_API_KEY` is set, `runs` + `patients` endpoints require `X-Api-Key` (SSE accepts `?api_key=`).
  - if `PHARMASSIST_API_KEY` is not set, these endpoints are loopback-only and reject proxy-forward headers.
- Admin hardening:
  - if `PHARMASSIST_ADMIN_API_KEY` is set, requests must send header `X-Admin-Key`.
  - if `PHARMASSIST_ADMIN_API_KEY` is not set, admin endpoints are loopback-only and reject proxy-forward headers.
  - built-in rate limiting for admin endpoints (`PHARMASSIST_ADMIN_RATE_LIMIT_MAX`, `PHARMASSIST_ADMIN_RATE_LIMIT_WINDOW_SEC`).
  - every admin access is audit-logged in `admin_audit_events` (redacted metadata only).

To use a larger dataset, download it locally and point:
- `PHARMASSIST_PHARMACY_DATA_DIR=/path/to/dataset_dir` (must contain `patients.jsonl.gz`, `visits.jsonl.gz`, `events.jsonl.gz`, `inventory.jsonl.gz`)

## Local Dev (no model download)

Prereqs:
- Python 3.10+
- Node 20+ (any recent LTS is fine)

Local dev:

```bash
make setup

# terminal 1
make api-dev

# terminal 2
make web-dev
```

API health check:

```bash
curl http://localhost:8000/healthz
```

Enable authenticated DB preview in local dev (recommended):

```bash
export PHARMASSIST_API_KEY='change-me'
export PHARMASSIST_ADMIN_API_KEY='change-me'
export PHARMASSIST_ADMIN_RATE_LIMIT_MAX=30
export PHARMASSIST_ADMIN_RATE_LIMIT_WINDOW_SEC=60
make api-dev
```

In the web terminal, pass matching keys:

```bash
export VITE_API_KEY='change-me'
export VITE_ADMIN_DB_PREVIEW_KEY='change-me'
make web-dev
```

## Tests (must stay green)

```bash
make lint
npm -w apps/web run build
make validate
.venv/bin/pytest -q
make e2e
```

Note: `make e2e` starts and stops the API/web servers automatically for Playwright.
For manual UI testing, run `make api-dev` and `make web-dev` in separate terminals.

## Security / Red-Team checks

Runtime abuse checks (auth, allowlist, rate limit):

```bash
./scripts/redteam_check.sh
```

Static and dependency checks:

```bash
.venv/bin/python -m pip install -q pip-audit bandit
.venv/bin/pip-audit
.venv/bin/bandit -q -r apps/api/src -lll
npm audit --omit=dev --audit-level=high
```

## MedGemma / HAI-DEF smoke test (GPU recommended)

The Kaggle challenge requires using at least one HAI-DEF model. For this demo we
use **MedGemma** for `OCR/noisy text -> strict JSON extraction` behind a flag and
provide a deterministic fallback for CI.

This smoke test runs directly on a synthetic OCR fixture and validates the JSON
against the `intake_extracted` schema.

Notes:
- `google/medgemma-4b-it` is an **image-text-to-text** model. The script runs it
  in *text-only* mode using the official `AutoProcessor` +
  `AutoModelForImageTextToText` API.
- It downloads multiple GB of weights. Prefer running it on **Kaggle GPU**
  (Kaggle GPU hours are limited; budget accordingly).
- You must set `HF_TOKEN` (the model is gated on Hugging Face).

Run:

```bash
# Requires GPU + HF token in env:
# export HF_TOKEN=...

PYTHONPATH=apps/api/src python -m pharmassist_api.scripts.haidef_smoke \
  --model google/medgemma-4b-it \
  --mode conditional \
  --case-ref case_000042 \
  --language en \
  --max-new-tokens 256
```

### Kaggle CLI sanity checks (no GPU burn)

```bash
cd /Users/nico/Documents/AI/pharmassist-kaggle
.venv/bin/pip install -q kaggle

# Use your own Kaggle creds (or ~/.kaggle/kaggle.json)
KAGGLE_USERNAME='YOUR_USERNAME' KAGGLE_KEY='YOUR_KEY' \
.venv/bin/kaggle datasets status nicolascoquelet/pharmassist-pharmacy-year-paris15-2025-v1

KAGGLE_USERNAME='YOUR_USERNAME' KAGGLE_KEY='YOUR_KEY' \
.venv/bin/kaggle kernels list --user nicolascoquelet --sort-by dateCreated --page-size 20 -v

KAGGLE_USERNAME='YOUR_USERNAME' KAGGLE_KEY='YOUR_KEY' \
.venv/bin/kaggle kernels status nicolascoquelet/medgemma-nb
```

### Kaggle Notebook (recommended, no venv)

Kaggle images often ship with `torch`/`transformers` preinstalled and may not
support `python -m venv` (`ensurepip` can fail). The safest path is to **avoid
venv** and run from source via `PYTHONPATH`.

Suggested notebook cells (in order):

```python
# 1) Clone (or refresh)
#
# If you rerun the notebook, you can either delete and re-clone:
!rm -rf pharmassist-kaggle
!git clone https://github.com/ncoprod/pharmassist-kaggle.git
%cd /kaggle/working/pharmassist-kaggle
!git rev-parse --short HEAD
```

```python
# 2) Load HF token from Kaggle Secrets (create a secret named "HF_TOKEN")
from kaggle_secrets import UserSecretsClient
import os

os.environ["HF_TOKEN"] = UserSecretsClient().get_secret("HF_TOKEN")
print("HF_TOKEN loaded:", bool(os.environ.get("HF_TOKEN")))
```

```python
# 3) Cache models under /kaggle/working so subsequent runs reuse downloads
import os
os.environ["HF_HOME"] = "/kaggle/working/hf"
```

```python
# 4) (Optional) Install only missing deps (avoid editable installs in Kaggle)
import importlib, subprocess, sys

need = []
for pkg in ["torch", "transformers", "accelerate", "safetensors", "jsonschema"]:
    try:
        importlib.import_module(pkg)
    except Exception:
        need.append(pkg)

if need:
    print("Installing:", need)
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *need])
else:
    print("All deps already present")
```

```python
# 5) Run MedGemma extraction smoke test (validates JSON against schema)
!PYTHONPATH=apps/api/src python -m pharmassist_api.scripts.haidef_smoke \
  --model google/medgemma-4b-it \
  --mode conditional \
  --case-ref case_000042 \
  --language en \
  --max-new-tokens 256
```

```python
# 6) (Optional) Follow-up selector smoke (low-info case)
#
# This run is expected to stop in `needs_more_info` and show follow-up questions.
# Note: if required low-info questions already fill the max budget, triage keeps
# a rules-only set and selector attempt stays false by design.
import os, sys
from pathlib import Path

REPO = "/kaggle/working/pharmassist-kaggle"
API_SRC = f"{REPO}/apps/api/src"
assert Path(API_SRC).exists(), API_SRC
sys.path.insert(0, API_SRC)

os.environ["PHARMASSIST_DB_PATH"] = "/kaggle/working/pharmassist_demo.db"
os.environ["PHARMASSIST_USE_MEDGEMMA"] = "1"
os.environ["PHARMASSIST_MEDGEMMA_MODEL"] = "google/medgemma-4b-it"
os.environ["PHARMASSIST_USE_MEDGEMMA_FOLLOWUP"] = "1"

from pharmassist_api import db, orchestrator

db.init_db()
run = orchestrator.new_run(case_ref="case_lowinfo_000102", language="en", trigger="manual")
await orchestrator.run_pipeline(run["run_id"])
r = db.get_run(run["run_id"])

print("status:", r["status"])
question_ids = [q["question_id"] for q in r["artifacts"]["recommendation"]["follow_up_questions"]]
print("follow_up_question_ids:", question_ids)
assert question_ids, "expected at least one follow-up question for low-info case"
assert "q_primary_domain" in question_ids
```

```python
# 7) Run the full pipeline (A1 uses MedGemma; A7 report can also use MedGemma)
#
# IMPORTANT: do NOT use asyncio.run(...) inside notebooks (an event loop is already running).
# Use top-level `await` instead.
import os, sys
from pathlib import Path

REPO = "/kaggle/working/pharmassist-kaggle"
API_SRC = f"{REPO}/apps/api/src"
assert Path(API_SRC).exists(), API_SRC
sys.path.insert(0, API_SRC)

os.environ["PHARMASSIST_DB_PATH"] = "/kaggle/working/pharmassist_demo.db"
os.environ["PHARMASSIST_USE_MEDGEMMA"] = "1"
os.environ["PHARMASSIST_MEDGEMMA_MODEL"] = "google/medgemma-4b-it"
os.environ["PHARMASSIST_USE_MEDGEMMA_REPORT"] = "0"  # set to "1" if you want A7 model-backed too
os.environ["PHARMASSIST_USE_MEDGEMMA_FOLLOWUP"] = "0"  # keep off for this fully-automated demo

from pharmassist_api import db, orchestrator

db.init_db()
run = orchestrator.new_run_with_answers(
    case_ref="case_000042",
    language="en",
    trigger="manual",
    follow_up_answers=[
        {"question_id": "q_fever", "answer": "no"},
        {"question_id": "q_breathing", "answer": "no"},
        {"question_id": "q_pregnancy", "answer": "no"},
    ],
)

await orchestrator.run_pipeline(run["run_id"])
r = db.get_run(run["run_id"])

print("status:", r["status"])
print("symptoms:", [s["label"] for s in r["artifacts"]["intake_extracted"]["symptoms"]])
print("ranked_products:", len(r["artifacts"]["recommendation"].get("ranked_products", [])))
print("evidence_items:", len(r["artifacts"].get("evidence_items", [])))
print("\\n--- report head ---\\n", r["artifacts"].get("report_markdown", "")[:600])
```

```python
# 8) Privacy proof: ensure raw OCR snippet is NOT persisted in DB events
import json
from pharmassist_api.cases.load_case import load_case_bundle

bundle = load_case_bundle("case_000042")
needle = "PATIENT NOTE"
assert needle in bundle["intake_text_ocr"]["en"]

events = db.list_events(run["run_id"])
blob = json.dumps([e["data"] for e in events], ensure_ascii=False)
print("events:", len(events))
print("needle_in_events:", needle in blob)  # must be False
```

## Running the API with MedGemma enabled (optional)

By default, the API uses the deterministic fallback extractor.

To enable model-backed extraction:

```bash
# install optional deps (not installed in CI)
.venv/bin/pip install -e "apps/api[ml]"

export PHARMASSIST_USE_MEDGEMMA=1
export PHARMASSIST_MEDGEMMA_MODEL=google/medgemma-4b-it
make api-dev
```

To also enable **model-backed report composition** (Day 8), add:

```bash
export PHARMASSIST_USE_MEDGEMMA_REPORT=1
```

## License

This repository is open source (see `LICENSE`).
