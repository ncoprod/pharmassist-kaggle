import asyncio
import json

from fastapi.testclient import TestClient


def test_db_preview_tables_and_schema(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))

    from pharmassist_api.contracts.validate_schema import validate_instance
    from pharmassist_api.main import app

    with TestClient(app) as client:
        tables_resp = client.get("/admin/db-preview/tables")
        assert tables_resp.status_code == 200
        tables = tables_resp.json().get("tables")
        assert isinstance(tables, list)
        assert "patients" in tables
        assert "run_events" in tables

        preview_resp = client.get(
            "/admin/db-preview",
            params={"table": "patients", "query": "pt_0000", "limit": 10},
        )
        assert preview_resp.status_code == 200
        payload = preview_resp.json()
        validate_instance(payload, "db_preview")
        assert payload["table"] == "patients"
        assert payload["redacted"] is True
        assert "llm_context_json" not in payload["columns"]


def test_db_preview_rejects_unknown_table(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))

    from pharmassist_api.main import app

    with TestClient(app) as client:
        resp = client.get("/admin/db-preview", params={"table": "drop_table"})
        assert resp.status_code == 400


def test_db_preview_run_events_never_expose_raw_ocr_text(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))

    from pharmassist_api import db
    from pharmassist_api.cases.load_case import load_case_bundle
    from pharmassist_api.main import app
    from pharmassist_api.orchestrator import new_run, run_pipeline

    db.init_db()

    bundle = load_case_bundle("case_000042")
    needle = "PATIENT NOTE"
    assert needle in bundle["intake_text_ocr"]["en"]

    run = new_run(case_ref="case_000042", language="en", trigger="manual")
    asyncio.run(run_pipeline(run["run_id"]))

    with TestClient(app) as client:
        resp = client.get(
            "/admin/db-preview",
            params={
                "table": "run_events",
                "query": run["run_id"][:8],
                "limit": 100,
            },
        )
        assert resp.status_code == 200
        payload = resp.json()
        blob = json.dumps(payload.get("rows", []), ensure_ascii=False)
        assert needle not in blob
