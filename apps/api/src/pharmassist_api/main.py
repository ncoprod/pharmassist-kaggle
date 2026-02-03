from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from pharmassist_api import db
from pharmassist_api.contracts.validate_schema import validate_instance
from pharmassist_api.orchestrator import dumps_sse, get_queue, new_run, run_pipeline


class RunCreateRequest(BaseModel):
    case_ref: str = "case_000042"
    language: Literal["fr", "en"] = "fr"
    trigger: Literal["manual", "import", "ocr_upload", "scheduled_refresh"] = "manual"

@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="PharmAssist Kaggle Demo API", version="0.0.0", lifespan=lifespan)

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

@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}

@app.get("/")
def root() -> dict[str, str]:
    # Avoid confusion during local dev (people will hit `/` first).
    return {"status": "ok", "healthz": "/healthz", "docs": "/docs"}


@app.post("/runs")
async def create_run(req: RunCreateRequest) -> dict[str, Any]:
    run = new_run(case_ref=req.case_ref, language=req.language, trigger=req.trigger)

    # Ensure our API output follows the canonical contract early.
    validate_instance(run, "run")

    # Kick off the background pipeline.
    asyncio.create_task(run_pipeline(run["run_id"]))

    return run


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@app.get("/runs/{run_id}/events")
async def run_events(
    run_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
) -> StreamingResponse:
    """Server-Sent Events stream for run progress.

    Day 3: in-process SSE; assumes a single server process (OK for Kaggle demo).
    """

    async def event_iter() -> Any:
        # Use Last-Event-ID for seamless browser reconnects.
        after_id = after
        if after_id == 0:
            last = request.headers.get("last-event-id")
            if last and last.isdigit():
                after_id = int(last)

        # 1) Replay history from DB (useful on refresh/reconnect).
        for item in db.list_events(run_id, after_id=after_id):
            eid = int(item["id"])
            data = dict(item["data"])
            yield dumps_sse(data, event_id=eid, event=str(data.get("type") or "message"))

        # 2) Subscribe to live events.
        q = get_queue(run_id)
        while True:
            if await request.is_disconnected():
                break

            try:
                msg = await asyncio.wait_for(q.get(), timeout=15)
            except TimeoutError:
                # Keep-alive comment.
                yield ": keep-alive\n\n"
                continue

            eid = int(msg["id"])
            data = dict(msg["data"])
            yield dumps_sse(data, event_id=eid, event=str(data.get("type") or "message"))

            if data.get("type") == "finalized":
                break

    return StreamingResponse(
        event_iter(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
