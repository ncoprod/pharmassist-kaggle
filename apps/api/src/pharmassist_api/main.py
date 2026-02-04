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
from pharmassist_api.orchestrator import dumps_sse, get_queue, new_run_with_answers, run_pipeline
from pharmassist_api.validators.phi_scanner import scan_for_phi


class FollowUpAnswer(BaseModel):
    question_id: str
    answer: str


class RunCreateRequest(BaseModel):
    case_ref: str = "case_000042"
    language: Literal["fr", "en"] = "fr"
    trigger: Literal["manual", "import", "ocr_upload", "scheduled_refresh"] = "manual"
    follow_up_answers: list[FollowUpAnswer] | None = None


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
    # Vite can bump the port (5173 -> 5174, etc.) if already in use.
    # Accept any local dev origin while keeping the allowlist strict (no LAN / public origins).
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
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
    follow_up_answers = (
        [a.model_dump() for a in req.follow_up_answers] if req.follow_up_answers else None
    )
    if follow_up_answers:
        violations = scan_for_phi(follow_up_answers, path="$.follow_up_answers")
        blockers = [v for v in violations if v.severity == "BLOCKER"]
        if blockers:
            # Do not persist identifier-like content from untrusted UI input.
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "PHI detected in follow_up_answers",
                    "violations": [
                        {
                            "code": v.code,
                            "severity": v.severity,
                            "json_path": v.json_path,
                            "message": v.message,
                        }
                        for v in blockers
                    ],
                },
            )
    run = new_run_with_answers(
        case_ref=req.case_ref,
        language=req.language,
        trigger=req.trigger,
        follow_up_answers=follow_up_answers,
    )

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
