import asyncio
import json

from fastapi.testclient import TestClient


def test_db_preview_tables_and_schema(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))

    from pharmassist_api.contracts.validate_schema import validate_instance
    from pharmassist_api.main import app, reset_admin_guard_state_for_tests

    reset_admin_guard_state_for_tests()

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

    from pharmassist_api.main import app, reset_admin_guard_state_for_tests

    reset_admin_guard_state_for_tests()

    with TestClient(app) as client:
        resp = client.get("/admin/db-preview", params={"table": "drop_table"})
        assert resp.status_code == 400


def test_db_preview_run_events_never_expose_raw_ocr_text(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))

    from pharmassist_api import db
    from pharmassist_api.cases.load_case import load_case_bundle
    from pharmassist_api.main import app, reset_admin_guard_state_for_tests
    from pharmassist_api.orchestrator import new_run, run_pipeline

    reset_admin_guard_state_for_tests()
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


def test_db_preview_requires_admin_key_when_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("PHARMASSIST_ADMIN_API_KEY", "topsecret")

    from pharmassist_api import db
    from pharmassist_api.main import app, reset_admin_guard_state_for_tests

    reset_admin_guard_state_for_tests()
    db.init_db()
    with TestClient(app) as client:
        denied = client.get("/admin/db-preview/tables")
        assert denied.status_code == 401

        denied_bad = client.get(
            "/admin/db-preview/tables",
            headers={"X-Admin-Key": "wrong"},
        )
        assert denied_bad.status_code == 401

        allowed = client.get(
            "/admin/db-preview/tables",
            headers={"X-Admin-Key": "topsecret"},
        )
        assert allowed.status_code == 200

    audits = db.list_admin_audit_events(limit=20)
    assert any(a["action"] == "deny" and a["reason"] == "invalid_admin_key" for a in audits)
    assert any(a["action"] == "allow" and a["reason"] == "admin_key" for a in audits)


def test_db_preview_admin_rate_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("PHARMASSIST_ADMIN_API_KEY", "topsecret")
    monkeypatch.setenv("PHARMASSIST_ADMIN_RATE_LIMIT_MAX", "1")
    monkeypatch.setenv("PHARMASSIST_ADMIN_RATE_LIMIT_WINDOW_SEC", "60")

    from pharmassist_api import db
    from pharmassist_api.main import app, reset_admin_guard_state_for_tests

    reset_admin_guard_state_for_tests()
    db.init_db()
    headers = {"X-Admin-Key": "topsecret"}
    with TestClient(app) as client:
        ok_resp = client.get("/admin/db-preview/tables", headers=headers)
        assert ok_resp.status_code == 200

        rate_limited = client.get("/admin/db-preview/tables", headers=headers)
        assert rate_limited.status_code == 429

    audits = db.list_admin_audit_events(limit=20)
    assert any(a["action"] == "rate_limited" and a["reason"] == "too_many_requests" for a in audits)


def test_db_preview_audit_meta_redacts_query(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("PHARMASSIST_ADMIN_API_KEY", "topsecret")

    from pharmassist_api import db
    from pharmassist_api.main import app, reset_admin_guard_state_for_tests

    reset_admin_guard_state_for_tests()
    db.init_db()
    headers = {"X-Admin-Key": "topsecret"}
    with TestClient(app) as client:
        resp = client.get(
            "/admin/db-preview",
            params={"table": "patients", "query": "pt_0000", "limit": 7},
            headers=headers,
        )
        assert resp.status_code == 200

    audits = db.list_admin_audit_events(limit=20)
    hit = next(
        (
            a
            for a in audits
            if a["endpoint"] == "/admin/db-preview"
            and a["action"] == "allow"
            and a["reason"] == "admin_key"
        ),
        None,
    )
    assert hit is not None
    meta = hit["meta"]
    assert meta.get("table") == "patients"
    assert meta.get("query_len") == 7
    assert isinstance(meta.get("query_sha256_12"), str)
    assert len(meta["query_sha256_12"]) == 12
    assert "query" not in meta


def test_db_preview_denies_non_loopback_without_admin_key(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))

    from pharmassist_api import db
    from pharmassist_api.main import app, reset_admin_guard_state_for_tests

    reset_admin_guard_state_for_tests()
    db.init_db()
    with TestClient(app, client=("8.8.8.8", 12345)) as client:
        resp = client.get("/admin/db-preview/tables")
        assert resp.status_code == 403

    audits = db.list_admin_audit_events(limit=20)
    assert any(
        a["action"] == "deny" and a["reason"] == "non_loopback_without_admin_key"
        for a in audits
    )


def test_db_preview_denies_forwarded_headers_without_admin_key(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))

    from pharmassist_api import db
    from pharmassist_api.main import app, reset_admin_guard_state_for_tests

    reset_admin_guard_state_for_tests()
    db.init_db()
    with TestClient(app) as client:
        resp = client.get(
            "/admin/db-preview/tables",
            headers={"X-Forwarded-For": "203.0.113.10"},
        )
        assert resp.status_code == 403

    audits = db.list_admin_audit_events(limit=20)
    assert any(
        a["action"] == "deny" and a["reason"] == "forwarded_headers_without_admin_key"
        for a in audits
    )
