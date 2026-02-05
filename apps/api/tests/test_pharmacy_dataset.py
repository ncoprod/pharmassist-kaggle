import asyncio

from fastapi.testclient import TestClient


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
