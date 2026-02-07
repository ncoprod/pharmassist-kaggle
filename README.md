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
- **Scan ordonnance v1**: PDF text-layer upload -> deterministic PHI redaction -> PHI boundary -> A1 intake extraction (metadata-only persistence).

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
- `POST /documents/prescription` (multipart PDF + `patient_ref` + `language`)
- `GET /admin/db-preview/tables`
- `GET /admin/db-preview?table=patients&query=pt_0000&limit=50`

Run creation:
- `POST /runs` accepts `visit_ref` (+ optional `patient_ref`) and resolves a case bundle from the dataset
  **without any PHI** (no OCR/PDF text stored).
- PDF ingestion creates a synthetic visit linked to `patient_ref`, then you can launch a run from that visit.

DB viewer:
- Read-only and redacted by design.
- Returns compact table previews only (no raw OCR/PDF text, no free-form payload blobs).
- App/API hardening:
  - if `PHARMASSIST_API_KEY` is set, `runs` + `patients` endpoints require `X-Api-Key`.
  - SSE uses short-lived per-run tokens from `POST /runs/{run_id}/events-token` (no long-lived API key in URL).
  - if `PHARMASSIST_API_KEY` is not set, these endpoints are loopback-only and reject proxy-forward headers.
- Admin hardening:
  - if `PHARMASSIST_ADMIN_API_KEY` is set, requests must send header `X-Admin-Key`.
  - if `PHARMASSIST_ADMIN_API_KEY` is not set, admin endpoints are loopback-only and reject proxy-forward headers.
  - built-in rate limiting for admin endpoints (`PHARMASSIST_ADMIN_RATE_LIMIT_MAX`, `PHARMASSIST_ADMIN_RATE_LIMIT_WINDOW_SEC`).
  - every admin access is audit-logged in `admin_audit_events` (redacted metadata only).

To use a larger dataset, download it locally and point:
- `PHARMASSIST_PHARMACY_DATA_DIR=/path/to/dataset_dir` (must contain `patients.jsonl.gz`, `visits.jsonl.gz`, `events.jsonl.gz`, `inventory.jsonl.gz`)

For a full local demo (Paris15 synthetic year), the repo also provides convenience targets:
- `make api-dev-full`
- `make web-dev-full`

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
make redteam
make security-audit
```

Note: `make e2e` starts and stops the API/web servers automatically for Playwright.
It auto-selects free local ports when `8000`/`5174` are already busy, which avoids
false failures from stale dev servers.
To run hardened E2E with auth enabled:

```bash
PHARMASSIST_API_KEY='change-me' PHARMASSIST_ADMIN_API_KEY='change-me' make e2e
```

No-GPU evaluation and replay:

```bash
make eval
make demo-replay
```

For manual UI testing, run `make api-dev` and `make web-dev` in separate terminals.

### Full Demo (local, large dataset)

1) Prepare the full dataset (choose one option):

Option A — fetch the pinned Kaggle dataset:

```bash
cd /Users/nico/Documents/AI/pharmassist-kaggle
KAGGLE_USERNAME='YOUR_USERNAME' KAGGLE_KEY='YOUR_KEY' make dataset-fetch
```

Option B — generate it locally from `pharmassist-synthdata`:

```bash
cd /Users/nico/Documents/AI/pharmassist-synthdata
.venv/bin/pharmassist-synthdata sim-year --seed 2025 --pharmacy paris15 --year 2025 --out /Users/nico/Documents/AI/pharmassist-kaggle/.data/paris15_full
```

2) Run API + web with full-dataset defaults (in `pharmassist-kaggle`):

```bash
cd /Users/nico/Documents/AI/pharmassist-kaggle
DEMO_DATA_DIR='.data/paris15_full' make api-dev-full
```

```bash
cd /Users/nico/Documents/AI/pharmassist-kaggle
make web-dev-full
```

3) Verify load/counts with a single deterministic check:

```bash
cd /Users/nico/Documents/AI/pharmassist-kaggle
make dataset-load-check
```

The check auto-raises a low shell `nofile` limit when possible (macOS safety).

Expected order of magnitude with `seed=2025`:
- `patients ~ 10k`
- `visits ~ 66k`
- `events ~ 118k`

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
#    This is required: if the token cannot be fetched, fail fast (no SKIP mode).
import os
import time

HF_TOKEN_OK = False

def load_hf_token(max_attempts=8):
    if os.environ.get("HF_TOKEN"):
        return True
    try:
        from kaggle_secrets import UserSecretsClient
    except Exception as exc:
        raise RuntimeError(f"kaggle_secrets unavailable: {exc}") from exc
    for attempt in range(1, max_attempts + 1):
        try:
            user_secrets = UserSecretsClient()
            token = user_secrets.get_secret("HF_TOKEN")
            if token:
                os.environ["HF_TOKEN"] = token
                return True
        except Exception as exc:
            print(f"HF_TOKEN fetch attempt {attempt}/{max_attempts} failed: {exc}")
            time.sleep(min(20, attempt * 3))
    return False

HF_TOKEN_OK = load_hf_token()
print("HF_TOKEN loaded:", HF_TOKEN_OK)
if not HF_TOKEN_OK:
    raise RuntimeError(
        "HF_TOKEN is required for MedGemma GPU smoke. Configure Kaggle secret HF_TOKEN and rerun."
    )
```

```python
# 3) Verify token can access the gated MedGemma repo
from huggingface_hub import HfApi

api = HfApi(token=os.environ["HF_TOKEN"])
info = api.model_info("google/medgemma-4b-it")
print("HF_GATED_ACCESS_OK:", info.id)
```

```python
# 4) Cache models under /kaggle/working so subsequent runs reuse downloads
import os
os.environ["HF_HOME"] = "/kaggle/working/hf"
```

```python
# 5) (Optional) Install only missing deps (avoid editable installs in Kaggle)
import importlib, subprocess, sys

need = []
for pkg in ["torch", "transformers", "accelerate", "safetensors", "jsonschema", "huggingface_hub"]:
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
# 6) Run MedGemma extraction smoke test (validates JSON against schema)
#    This step is mandatory and must PASS.
import os
import subprocess
import sys

if not os.environ.get("HF_TOKEN"):
    raise RuntimeError("HF_TOKEN missing in environment")

cmd = [
    sys.executable,
    "-m",
    "pharmassist_api.scripts.haidef_smoke",
    "--model",
    "google/medgemma-4b-it",
    "--mode",
    "conditional",
    "--case-ref",
    "case_000042",
    "--language",
    "en",
    "--max-new-tokens",
    "256",
    "--debug",
]
env = dict(os.environ)
env["PYTHONPATH"] = "apps/api/src"
proc = subprocess.run(cmd, env=env, text=True, capture_output=True)
print(proc.stdout)
print("HAIDEF_SMOKE_STATUS:", "PASS" if proc.returncode == 0 else "FAIL")
if proc.returncode != 0:
    print(proc.stderr)
    raise RuntimeError("MedGemma smoke test failed")
```

```python
# 7) (Optional) Follow-up selector smoke (low-info case)
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
# 8) Run the full pipeline (A1 uses MedGemma; A7 report can also use MedGemma)
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
# 9) Privacy proof: ensure raw OCR snippet is NOT persisted in DB events
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
