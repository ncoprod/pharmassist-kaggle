from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient


def test_run_outputs_and_status_surfaces_do_not_leak_raw_ocr(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))

    from pharmassist_api import db
    from pharmassist_api.main import app
    from pharmassist_api.orchestrator import new_run_with_answers, run_pipeline

    db.init_db()

    run = new_run_with_answers(
        case_ref="case_000042",
        language="en",
        trigger="manual",
        follow_up_answers=[
            {"question_id": "q_fever", "answer": "no"},
            {"question_id": "q_breathing", "answer": "no"},
            {"question_id": "q_pregnancy", "answer": "no"},
        ],
    )
    asyncio.run(run_pipeline(run["run_id"]))

    with TestClient(app) as client:
        run_resp = client.get(f"/runs/{run['run_id']}")
        assert run_resp.status_code == 200
        run_payload = run_resp.json()

        status_resp = client.get("/patients/pt_000000/analysis-status")
        assert status_resp.status_code == 200

        inbox_resp = client.get("/patients/inbox")
        assert inbox_resp.status_code == 200

    blob = json.dumps(
        [run_payload, status_resp.json(), inbox_resp.json()],
        ensure_ascii=False,
    )
    # OCR-only strings must never leave the trusted boundary.
    assert "PPATIENT NOTE" not in blob
    assert "CCief complaint" not in blob
