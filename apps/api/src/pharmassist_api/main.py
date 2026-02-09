from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from pharmassist_api import db
from pharmassist_api.analysis_refresh import (
    get_patient_analysis_status,
    get_patients_inbox,
    queue_patient_refresh,
    reset_analysis_refresh_state_for_tests,
)
from pharmassist_api.contracts.validate_schema import validate_instance
from pharmassist_api.follow_up_answers import validate_and_canonicalize_follow_up_answers
from pharmassist_api.orchestrator import dumps_sse, get_queue, new_run_with_answers, run_pipeline
from pharmassist_api.pharmacy import ensure_pharmacy_dataset_loaded
from pharmassist_api.pharmacy.prescription_upload import ingest_prescription_pdf, max_upload_bytes
from pharmassist_api.privacy.phi_boundary import scan_text
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


class PatientRefreshRequest(BaseModel):
    reason: str = "manual_refresh"


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


_ADMIN_RATE_LOCK = threading.Lock()
_ADMIN_RATE_BUCKETS: dict[str, deque[float]] = {}
_STREAM_TOKEN_LOCK = threading.Lock()
_STREAM_TOKENS: dict[str, tuple[str, float]] = {}


def _env_int(name: str, default: int, *, low: int, high: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(low, min(high, value))


def _admin_rate_window_sec() -> int:
    return _env_int("PHARMASSIST_ADMIN_RATE_LIMIT_WINDOW_SEC", 60, low=1, high=3600)


def _admin_rate_max() -> int:
    return _env_int("PHARMASSIST_ADMIN_RATE_LIMIT_MAX", 30, low=1, high=1000)


def _admin_api_key() -> str:
    return (os.getenv("PHARMASSIST_ADMIN_API_KEY") or "").strip()


def _api_key() -> str:
    return (os.getenv("PHARMASSIST_API_KEY") or "").strip()


def _stream_token_ttl_sec() -> int:
    return _env_int("PHARMASSIST_EVENT_STREAM_TOKEN_TTL_SEC", 600, low=30, high=3600)


def _client_ip(request: Request) -> str:
    if request.client and isinstance(request.client.host, str) and request.client.host.strip():
        return request.client.host.strip()
    return "unknown"


def _is_loopback_ip(client_ip: str) -> bool:
    ip = client_ip.strip().lower()
    return ip in {"127.0.0.1", "::1", "localhost", "testclient"} or ip.startswith(
        "::ffff:127.0.0.1"
    )


def _has_forward_headers(request: Request) -> bool:
    return any(
        request.headers.get(name)
        for name in ("forwarded", "x-forwarded-for", "x-forwarded-proto", "x-forwarded-host")
    )


def _sha256_12(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _consume_admin_rate_limit(endpoint: str, client_ip: str) -> bool:
    now = time.monotonic()
    window = float(_admin_rate_window_sec())
    limit = int(_admin_rate_max())
    bucket_key = f"{endpoint}|{client_ip}"
    cutoff = now - window
    with _ADMIN_RATE_LOCK:
        bucket = _ADMIN_RATE_BUCKETS.setdefault(bucket_key, deque())
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
    return True


def _audit_admin_access(
    *,
    request: Request,
    endpoint: str,
    action: str,
    reason: str,
    meta: dict[str, Any],
) -> None:
    try:
        db.insert_admin_audit_event(
            endpoint=endpoint,
            method=request.method,
            client_ip=_client_ip(request),
            action=action,
            reason=reason,
            meta=meta,
        )
    except Exception:
        # Never block the API on audit-log write failures.
        pass


def _enforce_admin_controls(request: Request, *, endpoint: str, meta: dict[str, Any]) -> None:
    client_ip = _client_ip(request)
    if not _consume_admin_rate_limit(endpoint, client_ip):
        _audit_admin_access(
            request=request,
            endpoint=endpoint,
            action="rate_limited",
            reason="too_many_requests",
            meta=meta,
        )
        raise HTTPException(status_code=429, detail="Rate limit exceeded for admin endpoint")

    expected_key = _admin_api_key()
    if expected_key:
        provided_key = request.headers.get("x-admin-key") or ""
        if not secrets.compare_digest(provided_key, expected_key):
            _audit_admin_access(
                request=request,
                endpoint=endpoint,
                action="deny",
                reason="invalid_admin_key",
                meta=meta,
            )
            raise HTTPException(status_code=401, detail="Admin authentication required")
        _audit_admin_access(
            request=request,
            endpoint=endpoint,
            action="allow",
            reason="admin_key",
            meta=meta,
        )
        return

    if not _is_loopback_ip(client_ip):
        _audit_admin_access(
            request=request,
            endpoint=endpoint,
            action="deny",
            reason="non_loopback_without_admin_key",
            meta=meta,
        )
        raise HTTPException(
            status_code=403,
            detail="Admin endpoints are loopback-only unless PHARMASSIST_ADMIN_API_KEY is set",
        )

    if _has_forward_headers(request):
        _audit_admin_access(
            request=request,
            endpoint=endpoint,
            action="deny",
            reason="forwarded_headers_without_admin_key",
            meta=meta,
        )
        raise HTTPException(
            status_code=403,
            detail="Admin endpoints require PHARMASSIST_ADMIN_API_KEY behind proxy headers",
        )

    _audit_admin_access(
        request=request,
        endpoint=endpoint,
        action="allow",
        reason="loopback_dev_mode",
        meta=meta,
    )


def reset_admin_guard_state_for_tests() -> None:
    with _ADMIN_RATE_LOCK:
        _ADMIN_RATE_BUCKETS.clear()
    with _STREAM_TOKEN_LOCK:
        _STREAM_TOKENS.clear()
    reset_analysis_refresh_state_for_tests()


def _provided_api_key(request: Request) -> str:
    return (request.headers.get("x-api-key") or "").strip()


def _prune_expired_stream_tokens(*, now: float) -> None:
    expired = [token for token, (_run_id, exp) in _STREAM_TOKENS.items() if exp <= now]
    for token in expired:
        _STREAM_TOKENS.pop(token, None)


def _issue_stream_token(*, run_id: str) -> tuple[str, int]:
    ttl = _stream_token_ttl_sec()
    token = secrets.token_urlsafe(32)
    expires_at = time.monotonic() + float(ttl)
    with _STREAM_TOKEN_LOCK:
        _prune_expired_stream_tokens(now=time.monotonic())
        _STREAM_TOKENS[token] = (run_id, expires_at)
    return token, ttl


def _is_valid_stream_token(*, run_id: str, token: str) -> bool:
    token_norm = token.strip()
    if not token_norm:
        return False
    with _STREAM_TOKEN_LOCK:
        now = time.monotonic()
        _prune_expired_stream_tokens(now=now)
        row = _STREAM_TOKENS.get(token_norm)
        if not row:
            return False
        token_run_id, expires_at = row
        if expires_at <= now:
            _STREAM_TOKENS.pop(token_norm, None)
            return False
        return token_run_id == run_id


def _enforce_data_controls(request: Request, *, endpoint: str) -> None:
    expected = _api_key()
    if expected:
        provided = _provided_api_key(request)
        if not secrets.compare_digest(provided, expected):
            raise HTTPException(status_code=401, detail=f"{endpoint}: API authentication required")
        return

    if _has_forward_headers(request):
        detail = (
            f"{endpoint}: loopback-only without PHARMASSIST_API_KEY "
            "when proxy headers are present"
        )
        raise HTTPException(
            status_code=403,
            detail=detail,
        )
    if not _is_loopback_ip(_client_ip(request)):
        detail = f"{endpoint}: loopback-only without PHARMASSIST_API_KEY"
        raise HTTPException(status_code=403, detail=detail)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}

@app.get("/")
def root() -> dict[str, str]:
    # Avoid confusion during local dev (people will hit `/` first).
    return {"status": "ok", "healthz": "/healthz", "docs": "/docs"}


@app.post("/runs")
async def create_run(request: Request, req: RunCreateRequest) -> dict[str, Any]:
    _enforce_data_controls(request, endpoint="/runs")
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

        canonical, issues = validate_and_canonicalize_follow_up_answers(follow_up_answers)
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
def search_patients(
    request: Request, query: str = Query(default="", min_length=0, max_length=64)
) -> dict[str, Any]:
    _enforce_data_controls(request, endpoint="/patients")
    q = query.strip()
    if not q:
        return {"patients": []}
    return {"patients": db.search_patients(query_prefix=q, limit=20)}


@app.get("/patients/inbox")
def patients_inbox(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    _enforce_data_controls(request, endpoint="/patients/inbox")
    payload = get_patients_inbox(limit=limit)
    validate_instance(payload, "patient_inbox")
    return payload


@app.get("/patients/{patient_ref}")
def get_patient(request: Request, patient_ref: str) -> dict[str, Any]:
    _enforce_data_controls(request, endpoint="/patients/{patient_ref}")
    patient = db.get_patient(patient_ref)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient


@app.get("/patients/{patient_ref}/visits")
def get_patient_visits(request: Request, patient_ref: str) -> dict[str, Any]:
    _enforce_data_controls(request, endpoint="/patients/{patient_ref}/visits")
    patient = db.get_patient(patient_ref)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return {
        "patient_ref": patient_ref,
        "visits": db.list_patient_visits(patient_ref=patient_ref, limit=50),
    }


@app.get("/patients/{patient_ref}/analysis-status")
def patient_analysis_status(request: Request, patient_ref: str) -> dict[str, Any]:
    _enforce_data_controls(request, endpoint="/patients/{patient_ref}/analysis-status")
    patient = db.get_patient(patient_ref)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    payload = get_patient_analysis_status(patient_ref=patient_ref)
    validate_instance(payload, "patient_analysis_status")
    return payload


@app.post("/patients/{patient_ref}/refresh")
async def refresh_patient_analysis(
    request: Request,
    patient_ref: str,
    req: PatientRefreshRequest | None = None,
) -> dict[str, Any]:
    _enforce_data_controls(request, endpoint="/patients/{patient_ref}/refresh")
    patient = db.get_patient(patient_ref)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    reason = req.reason if req else "manual_refresh"
    queued = await queue_patient_refresh(patient_ref=patient_ref, reason=reason)
    payload = get_patient_analysis_status(patient_ref=patient_ref)
    validate_instance(payload, "patient_analysis_status")
    return {
        "schema_version": "0.0.0",
        "patient_ref": patient_ref,
        "accepted": True,
        "queued": bool(queued.get("queued")),
        "analysis_status": payload,
    }


@app.post("/documents/prescription")
async def upload_prescription_pdf(
    request: Request,
    patient_ref: Annotated[str, Form(min_length=1, max_length=64)],
    file: Annotated[UploadFile, File()],
    language: Annotated[Literal["fr", "en"], Form()] = "fr",
) -> dict[str, Any]:
    _enforce_data_controls(request, endpoint="/documents/prescription")
    patient_ref_norm = patient_ref.strip()
    if not patient_ref_norm:
        raise HTTPException(status_code=400, detail="patient_ref is required")
    if not db.get_patient(patient_ref_norm):
        raise HTTPException(status_code=404, detail="Patient not found")

    filename = (file.filename or "").strip()
    content_type = (file.content_type or "").strip().lower()
    if not filename.lower().endswith(".pdf") or content_type not in {
        "application/pdf",
        "application/x-pdf",
        "application/octet-stream",
    }:
        raise HTTPException(status_code=415, detail="Only PDF uploads are supported")

    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    size_limit = max_upload_bytes()
    if len(payload) > size_limit:
        raise HTTPException(status_code=413, detail=f"Uploaded file exceeds {size_limit} bytes")

    try:
        result = ingest_prescription_pdf(
            patient_ref=patient_ref_norm,
            language=language,
            pdf_bytes=payload,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid or unreadable PDF payload") from None

    common_doc_meta = {
        "status": result["status"],
        "source": "prescription_pdf_text_layer",
        "filename": filename,
        "content_type": content_type,
        "patient_ref": patient_ref_norm,
        "visit_ref": result["visit_ref"],
        "event_ref": result["event_ref"],
        "sha256_12": result["sha256_12"],
        "byte_size": len(payload),
        "page_count": result["page_count"],
        "text_length": result["text_length"],
        "redacted_text_length": result["redacted_text_length"],
        "redaction_replacements": result["redaction_replacements"],
        "language": language,
        "occurred_at": result["occurred_at"],
    }

    if result["status"] == "failed_phi_boundary":
        db.upsert_document(
            doc_ref=result["doc_ref"],
            metadata={
                **common_doc_meta,
                "violations": result["violations"],
            },
        )
        raise HTTPException(
            status_code=400,
            detail={
                "error": "PHI detected after redaction",
                "doc_ref": result["doc_ref"],
                "violations": result["violations"],
            },
        )

    intake_extracted = result["intake_extracted"]
    if not isinstance(intake_extracted, dict):
        raise HTTPException(status_code=500, detail="Internal extraction error")

    db.upsert_visit(
        visit_ref=result["visit_ref"],
        patient_ref=patient_ref_norm,
        occurred_at=result["occurred_at"],
        primary_domain=str(result["primary_domain"]),
        intents=["document_uploaded", "symptom_intake"],
        intake_extracted=intake_extracted,
    )
    event_payload = result["event_payload"]
    if not isinstance(event_payload, dict):
        raise HTTPException(status_code=500, detail="Internal event payload error")
    db.upsert_pharmacy_event(
        event_ref=result["event_ref"],
        visit_ref=result["visit_ref"],
        patient_ref=patient_ref_norm,
        occurred_at=result["occurred_at"],
        event_type="document_uploaded",
        payload=event_payload,
    )
    db.upsert_document(
        doc_ref=result["doc_ref"],
        metadata={
            **common_doc_meta,
            "event_payload_keys": sorted([k for k in event_payload.keys() if isinstance(k, str)]),
        },
    )
    try:
        await queue_patient_refresh(patient_ref=patient_ref_norm, reason="document_uploaded")
    except Exception:
        # Keep upload resilient even if refresh scheduling fails.
        pass

    receipt = {
        "schema_version": "0.0.0",
        "status": "ingested",
        "doc_ref": result["doc_ref"],
        "visit_ref": result["visit_ref"],
        "patient_ref": patient_ref_norm,
        "event_ref": result["event_ref"],
        "trigger": "ocr_upload",
        "language": language,
        "sha256_12": result["sha256_12"],
        "page_count": result["page_count"],
        "text_length": result["text_length"],
        "redaction_replacements": result["redaction_replacements"],
    }
    validate_instance(receipt, "document_upload_receipt")
    return receipt


@app.get("/admin/db-preview/tables")
def get_db_preview_tables(request: Request) -> dict[str, Any]:
    _enforce_admin_controls(
        request,
        endpoint="/admin/db-preview/tables",
        meta={},
    )
    return {"tables": db.list_db_preview_tables()}


@app.get("/admin/db-preview")
def get_db_preview(
    request: Request,
    table: str = Query(min_length=1, max_length=32),
    query: str = Query(default="", min_length=0, max_length=64),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    query_norm = (query or "").strip()
    table_norm = (table or "").strip().lower()
    _enforce_admin_controls(
        request,
        endpoint="/admin/db-preview",
        meta={
            "table": table_norm,
            "query_len": len(query_norm),
            "query_sha256_12": _sha256_12(query_norm),
            "limit": int(limit),
        },
    )
    try:
        payload = db.preview_db_table(table=table_norm, query=query_norm, limit=limit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    validate_instance(payload, "db_preview")
    return payload


@app.get("/runs/{run_id}")
def get_run(request: Request, run_id: str) -> dict[str, Any]:
    _enforce_data_controls(request, endpoint="/runs/{run_id}")
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@app.post("/runs/{run_id}/events-token")
def create_run_events_token(request: Request, run_id: str) -> dict[str, Any]:
    _enforce_data_controls(request, endpoint="/runs/{run_id}/events-token")
    if not db.get_run(run_id):
        raise HTTPException(status_code=404, detail="Run not found")
    token, ttl = _issue_stream_token(run_id=run_id)
    return {"run_id": run_id, "stream_token": token, "expires_in_sec": ttl}


@app.get("/runs/{run_id}/events")
async def run_events(
    run_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    stream_token: str = Query(default="", min_length=0, max_length=128),
) -> StreamingResponse:
    """Server-Sent Events stream for run progress.

    Day 3: in-process SSE; assumes a single server process (OK for Kaggle demo).
    """

    if not _is_valid_stream_token(run_id=run_id, token=stream_token):
        _enforce_data_controls(request, endpoint="/runs/{run_id}/events")

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
