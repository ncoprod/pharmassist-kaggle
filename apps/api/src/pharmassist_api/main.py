from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


class RunCreateRequest(BaseModel):
    # Day 1 stub: Day 3+ will introduce canonical schemas + full pipeline.
    intake_text: str | None = None


class RunCreateResponse(BaseModel):
    run_id: str
    status: str
    created_at: str


app = FastAPI(title="PharmAssist Kaggle Demo API", version="0.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory placeholder; Day 3 will add persistence (SQLite) + SSE.
_RUNS: dict[str, dict[str, Any]] = {}


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/runs", response_model=RunCreateResponse)
def create_run(_req: RunCreateRequest) -> RunCreateResponse:
    run_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    _RUNS[run_id] = {"run_id": run_id, "status": "created", "created_at": created_at}
    return RunCreateResponse(run_id=run_id, status="created", created_at=created_at)


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    return _RUNS.get(run_id) or {"run_id": run_id, "status": "not_found"}

