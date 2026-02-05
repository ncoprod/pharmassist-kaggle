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
from pharmassist_api.pharmacy import ensure_pharmacy_dataset_loaded
from pharmassist_api.privacy.phi_boundary import scan_text
from pharmassist_api.steps.question_bank import load_question_bank
from pharmassist_api.validators.phi_scanner import scan_for_phi


class FollowUpAnswer(BaseModel):
    question_id: str
    answer: str


class RunCreateRequest(BaseModel):
    case_ref: str = "case_000042"
    patient_ref: str | None = None
    visit_ref: str | None = None
    language: Literal["fr", "en"] = "fr"
    trigger: Literal["manual", "import", "ocr_upload", "scheduled_refresh"] = "manual"
    follow_up_answers: list[FollowUpAnswer] | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    # Idempotent: loads a tiny committed subset unless PHARMASSIST_PHARMACY_DATA_DIR is set.
    try:
        ensure_pharmacy_dataset_loaded()
    except Exception:
        # Keep the Kaggle demo resilient: case_ref-based runs still work even if dataset is missing.
        pass
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

def _normalize_yes_no(answer: str) -> str | None:
    t = answer.strip().lower()
    if t in {"yes", "y", "oui", "o", "true", "1"}:
        return "yes"
    if t in {"no", "n", "non", "false", "0"}:
        return "no"
    return None


def _validate_and_canonicalize_follow_up_answers(
    follow_up_answers: list[dict[str, Any]],
) -> tuple[list[dict[str, str]] | None, list[dict[str, Any]]]:
    bank = load_question_bank()
    canonical: list[dict[str, str]] = []
    issues: list[dict[str, Any]] = []

    for idx, item in enumerate(follow_up_answers):
        if not isinstance(item, dict):
            issues.append(
                {
                    "code": "INVALID_ITEM",
                    "json_path": f"$.follow_up_answers[{idx}]",
                    "message": "Answer item must be an object.",
                }
            )
            continue

        qid = item.get("question_id")
        ans = item.get("answer")
        if not isinstance(qid, str) or not qid.strip():
            issues.append(
                {
                    "code": "MISSING_QUESTION_ID",
                    "json_path": f"$.follow_up_answers[{idx}].question_id",
                    "message": "question_id must be a non-empty string.",
                }
            )
            continue
        if not isinstance(ans, str) or not ans.strip():
            issues.append(
                {
                    "code": "MISSING_ANSWER",
                    "json_path": f"$.follow_up_answers[{idx}].answer",
                    "message": "answer must be a non-empty string.",
                }
            )
            continue

        qid = qid.strip()
        ans = ans.strip()

        q = bank.get(qid)
        if not isinstance(q, dict):
            issues.append(
                {
                    "code": "UNKNOWN_QUESTION_ID",
                    "json_path": f"$.follow_up_answers[{idx}].question_id",
                    "message": f"Unknown question_id: {qid}",
                }
            )
            continue

        ans_type = q.get("answer_type")
        if ans_type == "yes_no":
            normalized = _normalize_yes_no(ans)
            if normalized is None:
                issues.append(
                    {
                        "code": "INVALID_YES_NO",
                        "json_path": f"$.follow_up_answers[{idx}].answer",
                        "message": "Expected yes/no answer (e.g. yes/no, oui/non).",
                    }
                )
                continue
            canonical.append({"question_id": qid, "answer": normalized})
            continue

        if ans_type == "choice":
            choices = q.get("choices")
            if not isinstance(choices, list) or not all(isinstance(c, str) for c in choices):
                issues.append(
                    {
                        "code": "INVALID_QUESTION_CONFIG",
                        "json_path": f"$.follow_up_answers[{idx}].question_id",
                        "message": f"Question {qid} has invalid choices configuration.",
                    }
                )
                continue
            if ans not in set(choices):
                issues.append(
                    {
                        "code": "INVALID_CHOICE",
                        "json_path": f"$.follow_up_answers[{idx}].answer",
                        "message": f"Answer must be one of: {', '.join(choices)}",
                    }
                )
                continue
            canonical.append({"question_id": qid, "answer": ans})
            continue

        if ans_type == "number":
            t = ans.replace(",", ".")
            try:
                value = float(t)
            except ValueError:
                issues.append(
                    {
                        "code": "INVALID_NUMBER",
                        "json_path": f"$.follow_up_answers[{idx}].answer",
                        "message": "Expected a numeric answer.",
                    }
                )
                continue
            if qid == "q_temperature" and not (30.0 <= value <= 45.0):
                issues.append(
                    {
                        "code": "NUMBER_OUT_OF_RANGE",
                        "json_path": f"$.follow_up_answers[{idx}].answer",
                        "message": "Temperature must be between 30 and 45 °C.",
                    }
                )
                continue
            if qid == "q_duration" and not (0.0 <= value <= 3650.0):
                issues.append(
                    {
                        "code": "NUMBER_OUT_OF_RANGE",
                        "json_path": f"$.follow_up_answers[{idx}].answer",
                        "message": "Duration must be between 0 and 3650 days.",
                    }
                )
                continue
            canonical.append({"question_id": qid, "answer": ans})
            continue

        issues.append(
            {
                "code": "UNSUPPORTED_ANSWER_TYPE",
                "json_path": f"$.follow_up_answers[{idx}].question_id",
                "message": f"Unsupported answer_type for question {qid}: {ans_type}",
            }
        )

    if issues:
        return None, issues

    return canonical, []


@app.post("/runs")
async def create_run(req: RunCreateRequest) -> dict[str, Any]:
    follow_up_answers = (
        [a.model_dump() for a in req.follow_up_answers] if req.follow_up_answers else None
    )
    if follow_up_answers:
        violations = scan_for_phi(follow_up_answers, path="$.follow_up_answers")
        # Defense in depth: treat "label-like" PHI (e.g. "Nom: ...") as BLOCKER too.
        for idx, item in enumerate(follow_up_answers):
            if not isinstance(item, dict):
                continue
            ans = item.get("answer")
            if isinstance(ans, str) and ans.strip():
                violations.extend(scan_text(ans, json_path=f"$.follow_up_answers[{idx}].answer"))
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

        canonical, issues = _validate_and_canonicalize_follow_up_answers(follow_up_answers)
        if issues:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "Invalid follow_up_answers",
                    "issues": issues,
                },
            )
        follow_up_answers = canonical
    case_ref = req.case_ref
    if isinstance(req.visit_ref, str) and req.visit_ref.strip():
        case_ref = f"visit:{req.visit_ref.strip()}"

    run = new_run_with_answers(
        case_ref=case_ref,
        patient_ref=req.patient_ref.strip() if isinstance(req.patient_ref, str) else None,
        visit_ref=req.visit_ref.strip() if isinstance(req.visit_ref, str) else None,
        language=req.language,
        trigger=req.trigger,
        follow_up_answers=follow_up_answers,
    )

    # Ensure our API output follows the canonical contract early.
    validate_instance(run, "run")

    # Kick off the background pipeline.
    asyncio.create_task(run_pipeline(run["run_id"]))

    return run


@app.get("/patients")
def search_patients(query: str = Query(default="", min_length=0, max_length=64)) -> dict[str, Any]:
    q = query.strip()
    if not q:
        return {"patients": []}
    return {"patients": db.search_patients(query_prefix=q, limit=20)}


@app.get("/patients/{patient_ref}")
def get_patient(patient_ref: str) -> dict[str, Any]:
    patient = db.get_patient(patient_ref)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient


@app.get("/patients/{patient_ref}/visits")
def get_patient_visits(patient_ref: str) -> dict[str, Any]:
    patient = db.get_patient(patient_ref)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return {
        "patient_ref": patient_ref,
        "visits": db.list_patient_visits(patient_ref=patient_ref, limit=50),
    }


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
