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

Repo layout:
- `packages/contracts/`: canonical JSON Schemas + examples
- `apps/api/`: FastAPI orchestrator + pipeline steps
- `apps/web/`: Vite UI (start run + SSE progress)

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
for pkg in ["transformers", "accelerate", "safetensors", "jsonschema"]:
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
# 6) (Optional) Follow-up selector smoke (MedGemma selects allowlisted question_ids)
#
# This run is expected to stop in `needs_more_info` and show follow-up questions.
import os, sys
sys.path.insert(0, "apps/api/src")

os.environ["PHARMASSIST_DB_PATH"] = "/kaggle/working/pharmassist_demo.db"
os.environ["PHARMASSIST_USE_MEDGEMMA"] = "1"
os.environ["PHARMASSIST_MEDGEMMA_MODEL"] = "google/medgemma-4b-it"
os.environ["PHARMASSIST_USE_MEDGEMMA_FOLLOWUP"] = "1"

from pharmassist_api import db, orchestrator

db.init_db()
run = orchestrator.new_run(case_ref="case_000042", language="en", trigger="manual")
await orchestrator.run_pipeline(run["run_id"])
r = db.get_run(run["run_id"])

print("status:", r["status"])
print("follow_up_question_ids:", [q["question_id"] for q in r["artifacts"]["recommendation"]["follow_up_questions"]])
```

```python
# 7) Run the full pipeline (A1 uses MedGemma; A7 report can also use MedGemma)
#
# IMPORTANT: do NOT use asyncio.run(...) inside notebooks (an event loop is already running).
# Use top-level `await` instead.
import os, sys
sys.path.insert(0, "apps/api/src")

os.environ["PHARMASSIST_DB_PATH"] = "/kaggle/working/pharmassist_demo.db"
os.environ["PHARMASSIST_USE_MEDGEMMA"] = "1"
os.environ["PHARMASSIST_MEDGEMMA_MODEL"] = "google/medgemma-4b-it"
os.environ["PHARMASSIST_USE_MEDGEMMA_REPORT"] = "1"  # optional
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
