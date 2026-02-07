# PharmAssist Kaggle API (FastAPI)

Local dev:

```bash
make setup
make api-dev
```

Health check:

```bash
curl http://localhost:8000/healthz
```

Key endpoints:
- `POST /runs` start a synthetic run
- `GET /runs/{run_id}` fetch run state
- `GET /runs/{run_id}/events` stream SSE events
- `POST /documents/prescription` ingest PDF text-layer (metadata-only persistence)
- `GET /patients*` browse synthetic patients/visits

Contracts + policies:
- JSON Schemas + examples: `packages/contracts/`
- Contract validation: `make validate`
- Policy validators: `apps/api/src/pharmassist_api/validators/`

Optional MedGemma/HAI-DEF smoke test (GPU recommended):

```bash
PYTHONPATH=apps/api/src python -m pharmassist_api.scripts.haidef_smoke \
  --model google/medgemma-4b-it \
  --mode conditional \
  --case-ref case_000042 \
  --language en
```

No-GPU deterministic checks:

```bash
PYTHONPATH=apps/api/src python -m pharmassist_api.scripts.eval_suite --out .data/eval/latest
PYTHONPATH=apps/api/src python -m pharmassist_api.scripts.demo_replay --out .data/demo_replay/latest
```
