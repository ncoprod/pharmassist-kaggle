import asyncio
import gzip
import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient


def _write_jsonl_gz(path: Path, rows: list[dict]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def test_pharmacy_dataset_loader_loads_mini_subset(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))

    from pharmassist_api import db
    from pharmassist_api.pharmacy import ensure_pharmacy_dataset_loaded

    db.init_db()
    res = ensure_pharmacy_dataset_loaded()

    assert db.count_patients() == 20
    assert db.count_visits() == 60
    assert db.count_inventory() > 0
    assert res.get("patients") == 20
    assert res.get("visits") == 60


def test_pharmacy_dataset_loader_sanitizes_event_payloads(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(db_path))

    from pharmassist_api import db
    from pharmassist_api.pharmacy.load_dataset import ensure_pharmacy_dataset_loaded

    db.init_db()

    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()

    llm_context = {
        "schema_version": "0.0.0",
        "demographics": {"age_years": 34, "sex": "F"},
        "allergies": [],
        "conditions": [],
        "current_medications": [],
    }
    intake_extracted = {
        "schema_version": "0.0.0",
        "presenting_problem": "Sneezing",
        "symptoms": [{"label": "sneezing", "severity": "moderate", "duration_days": 3}],
        "red_flags": [],
    }
    product = {
        "schema_version": "0.0.0",
        "sku": "SKU-0001",
        "name": "Example product",
        "brand": "ExampleBrand",
        "category": "allergy",
        "ingredients": ["cetirizine"],
        "contraindication_tags": [],
        "price_eur": 4.99,
        "in_stock": True,
        "stock_qty": 12,
    }

    _write_jsonl_gz(
        dataset_dir / "patients.jsonl.gz",
        [{"patient_ref": "pt_000001", "llm_context": llm_context}],
    )
    _write_jsonl_gz(
        dataset_dir / "visits.jsonl.gz",
        [
            {
                "visit_ref": "visit_000001",
                "patient_ref": "pt_000001",
                "occurred_at": "2025-01-01",
                "primary_domain": "respiratory",
                "intents": ["symptom_advice"],
                "intake_extracted": intake_extracted,
            }
        ],
    )

    # 1) Known event type with extra keys -> must be sanitized/whitelisted.
    # 2) Unknown event type -> must be dropped entirely (safer than storing unknown payload).
    _write_jsonl_gz(
        dataset_dir / "events.jsonl.gz",
        [
            {
                "event_ref": "ev_000001",
                "visit_ref": "visit_000001",
                "patient_ref": "pt_000001",
                "occurred_at": "2025-01-01",
                "event_type": "otc_purchase",
                "payload": {
                    "items": [{"sku": "SKU-0001", "qty": 1, "note": "DROP_ME"}],
                    "ocr_text": "DROP_ME_TOO",
                },
            },
            {
                "event_ref": "ev_000002",
                "visit_ref": "visit_000001",
                "patient_ref": "pt_000001",
                "occurred_at": "2025-01-01",
                "event_type": "document_uploaded",
                "payload": {"ocr_text": "very long raw text that should never be persisted"},
            },
        ],
    )
    _write_jsonl_gz(dataset_dir / "inventory.jsonl.gz", [product])

    res = ensure_pharmacy_dataset_loaded(dataset_dir=dataset_dir)
    assert res.get("patients_loaded") == 1
    assert res.get("visits_loaded") == 1
    assert res.get("events_loaded") == 1
    assert res.get("inventory_loaded") == 1

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT event_type, payload_json FROM events ORDER BY event_ref ASC LIMIT 1"
        ).fetchone()
        assert row is not None
        assert row[0] == "otc_purchase"
        payload = json.loads(row[1])
        assert payload == {"items": [{"sku": "SKU-0001", "qty": 1}]}


def test_patients_endpoints_and_run_from_visit(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))

    from pharmassist_api import db
    from pharmassist_api.contracts.validate_schema import validate_instance
    from pharmassist_api.main import app
    from pharmassist_api.orchestrator import run_pipeline

    with TestClient(app) as client:
        # Search.
        resp = client.get("/patients", params={"query": "pt_0000"})
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data.get("patients"), list)
        assert data["patients"]
        patient_ref = next(
            (p.get("patient_ref") for p in data["patients"] if p.get("patient_ref") == "pt_000000"),
            data["patients"][0]["patient_ref"],
        )

        # Detail.
        resp = client.get(f"/patients/{patient_ref}")
        assert resp.status_code == 200
        patient = resp.json()
        assert patient["patient_ref"] == patient_ref
        assert isinstance(patient.get("llm_context"), dict)
        validate_instance(patient["llm_context"], "llm_context")

        # Visits.
        resp = client.get(f"/patients/{patient_ref}/visits")
        assert resp.status_code == 200
        payload = resp.json()
        visits = payload.get("visits") or []
        assert isinstance(visits, list) and visits
        visit_ref = visits[0]["visit_ref"]

        # Create run from visit.
        resp = client.post(
            "/runs",
            json={
                "patient_ref": patient_ref,
                "visit_ref": visit_ref,
                "language": "fr",
                "trigger": "manual",
            },
        )
        assert resp.status_code == 200
        run = resp.json()
        validate_instance(run, "run")

        # Execute pipeline synchronously (TestClient background tasks are best-effort).
        asyncio.run(run_pipeline(run["run_id"]))

        stored = db.get_run(run["run_id"])
        assert stored is not None
        assert stored["status"] == "completed"
