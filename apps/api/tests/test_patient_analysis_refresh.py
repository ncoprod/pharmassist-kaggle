from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient


def _wait_for_status(client: TestClient, patient_ref: str, wanted: str, timeout_sec: float = 20.0) -> dict:
    deadline = time.time() + timeout_sec
    last: dict = {}
    while time.time() < deadline:
        resp = client.get(f"/patients/{patient_ref}/analysis-status")
        assert resp.status_code == 200
        last = resp.json()
        if last.get("status") == wanted:
            return last
        time.sleep(0.2)
    return last


def test_patient_analysis_status_refresh_and_inbox(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))

    from pharmassist_api.contracts.validate_schema import validate_instance
    from pharmassist_api.main import app, reset_admin_guard_state_for_tests

    reset_admin_guard_state_for_tests()

    with TestClient(app) as client:
        patient_ref = "pt_000000"

        status_resp = client.get(f"/patients/{patient_ref}/analysis-status")
        assert status_resp.status_code == 200
        status_payload = status_resp.json()
        validate_instance(status_payload, "patient_analysis_status")
        assert status_payload["patient_ref"] == patient_ref

        inbox_resp = client.get("/patients/inbox")
        assert inbox_resp.status_code == 200
        inbox_payload = inbox_resp.json()
        validate_instance(inbox_payload, "patient_inbox")
        assert isinstance(inbox_payload.get("patients"), list)

        refresh_resp = client.post(
            f"/patients/{patient_ref}/refresh",
            json={"reason": "test_manual_refresh"},
        )
        assert refresh_resp.status_code == 200
        body = refresh_resp.json()
        assert body.get("accepted") is True
        assert body.get("patient_ref") == patient_ref

        up_to_date = _wait_for_status(client, patient_ref, "up_to_date")
        assert up_to_date.get("status") == "up_to_date"
        assert up_to_date.get("changed_since_last_analysis") is False


def test_refresh_endpoint_coalesces_when_already_running(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))

    from pharmassist_api import analysis_refresh
    from pharmassist_api.main import app, reset_admin_guard_state_for_tests

    reset_admin_guard_state_for_tests()

    orig = analysis_refresh._run_refresh_for_patient

    async def _slow_run_refresh_for_patient(*, patient_ref: str) -> str:
        await asyncio.sleep(0.4)
        return await orig(patient_ref=patient_ref)

    monkeypatch.setattr(analysis_refresh, "_run_refresh_for_patient", _slow_run_refresh_for_patient)

    with TestClient(app) as client:
        patient_ref = "pt_000000"
        first = client.post(f"/patients/{patient_ref}/refresh", json={"reason": "burst_1"})
        assert first.status_code == 200
        second = client.post(f"/patients/{patient_ref}/refresh", json={"reason": "burst_2"})
        assert second.status_code == 200

        first_queued = bool(first.json().get("queued"))
        second_queued = bool(second.json().get("queued"))
        assert first_queued is True
        assert second_queued is False


def test_status_ignores_failed_manual_run_when_refresh_is_up_to_date(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))

    from pharmassist_api import db
    from pharmassist_api.main import app, reset_admin_guard_state_for_tests
    from pharmassist_api.orchestrator import new_run_with_answers

    reset_admin_guard_state_for_tests()

    with TestClient(app) as client:
        patient_ref = "pt_000000"
        refresh_resp = client.post(
            f"/patients/{patient_ref}/refresh",
            json={"reason": "seed_refresh"},
        )
        assert refresh_resp.status_code == 200
        up_to_date = _wait_for_status(client, patient_ref, "up_to_date")
        assert up_to_date.get("status") == "up_to_date"
        visit_ref = str(up_to_date.get("latest_visit_ref") or "")
        assert visit_ref

        manual = new_run_with_answers(
            case_ref=f"visit:{visit_ref}",
            patient_ref=patient_ref,
            visit_ref=visit_ref,
            language="en",
            trigger="manual",
            follow_up_answers=None,
        )
        db.update_run(manual["run_id"], status="failed_safe")

        after_manual_failed = client.get(f"/patients/{patient_ref}/analysis-status")
        assert after_manual_failed.status_code == 200
        payload = after_manual_failed.json()
        assert payload["status"] == "up_to_date"
        assert payload["latest_run_status"] == "completed"


def test_inbox_limit_is_applied_after_actionable_filter(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))

    from pharmassist_api import db
    from pharmassist_api.main import app, reset_admin_guard_state_for_tests
    from pharmassist_api.orchestrator import new_run_with_answers

    reset_admin_guard_state_for_tests()
    db.init_db()

    base = datetime.now(UTC)
    patient_new = "pt_000111"
    patient_old = "pt_000112"
    visit_new = "visit_000901"
    visit_old = "visit_000902"

    db.upsert_patient(patient_ref=patient_new, llm_context={"demographics": {"age_years": 34, "sex": "F"}})
    db.upsert_patient(patient_ref=patient_old, llm_context={"demographics": {"age_years": 68, "sex": "M"}})

    db.upsert_visit(
        visit_ref=visit_new,
        patient_ref=patient_new,
        occurred_at=(base - timedelta(minutes=5)).isoformat(),
        primary_domain="respiratory",
        intents=[],
        intake_extracted={"schema_version": "0.0.0", "presenting_problem": "cough", "symptoms": []},
    )
    db.upsert_visit(
        visit_ref=visit_old,
        patient_ref=patient_old,
        occurred_at=(base - timedelta(minutes=10)).isoformat(),
        primary_domain="respiratory",
        intents=[],
        intake_extracted={"schema_version": "0.0.0", "presenting_problem": "cough", "symptoms": []},
    )

    refresh_run = new_run_with_answers(
        case_ref=f"visit:{visit_new}",
        patient_ref=patient_new,
        visit_ref=visit_new,
        language="en",
        trigger="scheduled_refresh",
        follow_up_answers=None,
    )
    db.update_run(refresh_run["run_id"], status="completed")

    with TestClient(app) as client:
        inbox_resp = client.get("/patients/inbox?limit=1")
        assert inbox_resp.status_code == 200
        payload = inbox_resp.json()
        assert payload["count"] == 1
        assert len(payload["patients"]) == 1
        assert payload["patients"][0]["patient_ref"] == patient_old


def test_analysis_status_last_error_is_sanitized(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))

    from pharmassist_api import analysis_refresh
    from pharmassist_api.main import app, reset_admin_guard_state_for_tests

    reset_admin_guard_state_for_tests()

    async def _boom(*, patient_ref: str) -> str:  # noqa: ARG001
        raise ValueError("Patient John Doe 0601020304 not found in system")

    monkeypatch.setattr(analysis_refresh, "_run_refresh_for_patient", _boom)

    with TestClient(app) as client:
        patient_ref = "pt_000000"
        resp = client.post(f"/patients/{patient_ref}/refresh", json={"reason": "force_error"})
        assert resp.status_code == 200
        failed = _wait_for_status(client, patient_ref, "failed")
        assert failed["status"] == "failed"
        assert failed.get("last_error") == "not_found"
