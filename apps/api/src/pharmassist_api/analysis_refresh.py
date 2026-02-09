from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime
from typing import Any, Literal

from pharmassist_api import db
from pharmassist_api.orchestrator import new_run_with_answers, run_pipeline

AnalysisStatus = Literal["up_to_date", "refresh_pending", "running", "failed"]

_LOCK = threading.Lock()
_PENDING_PATIENTS: set[str] = set()
_RUNNING_PATIENTS: set[str] = set()
_LAST_ERROR: dict[str, str] = {}
_LAST_REASON: dict[str, str] = {}
_WORKER_TASK: asyncio.Task[None] | None = None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _normalize_error(err: Exception) -> str:
    text = str(err).strip().replace("\n", " ").lower()
    if "timeout" in text:
        return "timeout"
    if "not found" in text:
        return "not_found"
    if isinstance(err, ValueError):
        return "invalid_input"
    return err.__class__.__name__[:80]


def reset_analysis_refresh_state_for_tests() -> None:
    global _WORKER_TASK
    with _LOCK:
        _PENDING_PATIENTS.clear()
        _RUNNING_PATIENTS.clear()
        _LAST_ERROR.clear()
        _LAST_REASON.clear()
        _WORKER_TASK = None


async def queue_patient_refresh(*, patient_ref: str, reason: str) -> dict[str, Any]:
    patient_ref_norm = patient_ref.strip()
    reason_norm = (reason or "manual").strip()[:80] or "manual"
    if not patient_ref_norm:
        raise ValueError("patient_ref is required")

    with _LOCK:
        already_tracked = (
            patient_ref_norm in _PENDING_PATIENTS or patient_ref_norm in _RUNNING_PATIENTS
        )
        _PENDING_PATIENTS.add(patient_ref_norm)
        _LAST_REASON[patient_ref_norm] = reason_norm
        _LAST_ERROR.pop(patient_ref_norm, None)

    db.set_patient_analysis_state(
        patient_ref=patient_ref_norm,
        status="refresh_pending",
        changed_since_last_analysis=True,
        refresh_reason=reason_norm,
    )

    _ensure_worker(asyncio.get_running_loop())
    return {
        "schema_version": "0.0.0",
        "patient_ref": patient_ref_norm,
        "queued": not already_tracked,
        "status": "refresh_pending",
        "refresh_reason": reason_norm,
    }


def _ensure_worker(loop: asyncio.AbstractEventLoop) -> None:
    global _WORKER_TASK
    task = _WORKER_TASK
    if task is not None and not task.done():
        return
    _WORKER_TASK = loop.create_task(_refresh_worker())


async def _refresh_worker() -> None:
    global _WORKER_TASK

    while True:
        patient_ref: str | None = None
        reason = "scheduled_refresh"

        with _LOCK:
            if _PENDING_PATIENTS:
                patient_ref = sorted(_PENDING_PATIENTS)[0]
                _PENDING_PATIENTS.discard(patient_ref)
                _RUNNING_PATIENTS.add(patient_ref)
                reason = _LAST_REASON.get(patient_ref, reason)

        if patient_ref is None:
            _WORKER_TASK = None
            return

        try:
            db.set_patient_analysis_state(
                patient_ref=patient_ref,
                status="running",
                changed_since_last_analysis=True,
                refresh_reason=reason,
            )
            run_id = await _run_refresh_for_patient(patient_ref=patient_ref)
            run = db.get_run(run_id)
            final_status = str((run or {}).get("status") or "")
            if final_status == "completed":
                db.set_patient_analysis_state(
                    patient_ref=patient_ref,
                    status="up_to_date",
                    last_run_id=run_id,
                    last_error="",
                    changed_since_last_analysis=False,
                    refresh_reason=reason,
                )
            else:
                db.set_patient_analysis_state(
                    patient_ref=patient_ref,
                    status="failed",
                    last_run_id=run_id,
                    last_error=f"pipeline_status={final_status or 'unknown'}",
                    changed_since_last_analysis=True,
                    refresh_reason=reason,
                )
                _LAST_ERROR[patient_ref] = f"pipeline_status={final_status or 'unknown'}"
        except Exception as e:  # noqa: BLE001 - keep refresh loop resilient
            msg = _normalize_error(e)
            _LAST_ERROR[patient_ref] = msg
            db.set_patient_analysis_state(
                patient_ref=patient_ref,
                status="failed",
                last_error=msg,
                changed_since_last_analysis=True,
                refresh_reason=reason,
            )
        finally:
            with _LOCK:
                _RUNNING_PATIENTS.discard(patient_ref)


async def _run_refresh_for_patient(*, patient_ref: str) -> str:
    patient = db.get_patient(patient_ref)
    if not patient:
        raise ValueError("Patient not found")

    latest_visit = db.get_latest_patient_visit(patient_ref=patient_ref)
    if not latest_visit:
        raise ValueError("No visit found for patient")

    latest_run = db.get_latest_run_for_patient(patient_ref=patient_ref) or {}
    language = latest_run.get("language") if isinstance(latest_run.get("language"), str) else "fr"
    if language not in {"fr", "en"}:
        language = "fr"

    run = new_run_with_answers(
        case_ref=f"visit:{latest_visit['visit_ref']}",
        patient_ref=patient_ref,
        visit_ref=str(latest_visit["visit_ref"]),
        language=language,
        trigger="scheduled_refresh",
        follow_up_answers=None,
    )
    await run_pipeline(run["run_id"])
    return str(run["run_id"])


def get_patient_analysis_status(*, patient_ref: str) -> dict[str, Any]:
    patient_ref_norm = patient_ref.strip()

    latest_visit = db.get_latest_patient_visit(patient_ref=patient_ref_norm)
    latest_refresh_run = db.get_latest_run_for_patient(
        patient_ref=patient_ref_norm,
        trigger="scheduled_refresh",
    )
    latest_completed_refresh_run = db.get_latest_run_for_patient(
        patient_ref=patient_ref_norm,
        trigger="scheduled_refresh",
        status="completed",
    )
    latest_run = latest_refresh_run or latest_completed_refresh_run
    state = db.get_patient_analysis_state(patient_ref_norm) or {}

    with _LOCK:
        is_pending = patient_ref_norm in _PENDING_PATIENTS
        is_running = patient_ref_norm in _RUNNING_PATIENTS
        runtime_error = _LAST_ERROR.get(patient_ref_norm)

    changed_since_last_analysis = False
    if latest_visit is not None:
        if latest_completed_refresh_run is None:
            changed_since_last_analysis = True
        else:
            run_created = _parse_iso(latest_completed_refresh_run.get("created_at"))
            visit_ts = _parse_iso(latest_visit.get("occurred_at"))
            if run_created is None or visit_ts is None:
                changed_since_last_analysis = False
            else:
                changed_since_last_analysis = visit_ts > run_created

    status: AnalysisStatus
    if is_running:
        status = "running"
    elif is_pending:
        status = "refresh_pending"
    elif (
        latest_refresh_run
        and str(latest_refresh_run.get("status") or "") in {"failed", "failed_safe"}
    ):
        status = "failed"
    elif runtime_error:
        status = "failed"
    elif changed_since_last_analysis:
        status = "refresh_pending"
    elif latest_completed_refresh_run:
        status = "up_to_date"
    elif state.get("status") == "failed":
        status = "failed"
    else:
        status = "up_to_date"

    message = {
        "up_to_date": "Analysis is up to date.",
        "refresh_pending": "New data detected; refresh is pending.",
        "running": "Refresh is running.",
        "failed": "Last refresh failed. Manual refresh recommended.",
    }[status]

    return {
        "schema_version": "0.0.0",
        "patient_ref": patient_ref_norm,
        "status": status,
        "changed_since_last_analysis": changed_since_last_analysis,
        "latest_visit_ref": (
            latest_visit.get("visit_ref") if isinstance(latest_visit, dict) else None
        ),
        "latest_visit_at": (
            latest_visit.get("occurred_at") if isinstance(latest_visit, dict) else None
        ),
        "latest_run_id": latest_run.get("run_id") if isinstance(latest_run, dict) else None,
        "latest_run_status": latest_run.get("status") if isinstance(latest_run, dict) else None,
        "latest_run_at": latest_run.get("created_at") if isinstance(latest_run, dict) else None,
        "last_error": runtime_error or state.get("last_error") or None,
        "message": message,
        "updated_at": (
            state.get("updated_at") if isinstance(state.get("updated_at"), str) else _now_iso()
        ),
    }


def get_patients_inbox(*, limit: int = 50) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for patient_ref in db.list_patient_refs_with_visits(limit=None):
        status = get_patient_analysis_status(patient_ref=patient_ref)
        if status["changed_since_last_analysis"] or status["status"] in {
            "refresh_pending",
            "running",
            "failed",
        }:
            items.append(status)

    items.sort(
        key=lambda x: (_parse_iso(x.get("latest_visit_at")) or datetime.min.replace(tzinfo=UTC)),
        reverse=True,
    )
    limited_items = items[: max(int(limit), 0)]

    return {
        "schema_version": "0.0.0",
        "generated_at": _now_iso(),
        "count": len(limited_items),
        "patients": limited_items,
    }
