import os

from fastapi.testclient import TestClient


def test_create_run_returns_schema_compliant_run(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))

    # Import after env var is set so startup uses the temp DB.
    from pharmassist_api.contracts.validate_schema import validate_instance
    from pharmassist_api.main import app

    with TestClient(app) as client:
        resp = client.post(
            "/runs",
            json={
                "case_ref": "case_000042",
                "language": "fr",
                "trigger": "manual",
                "follow_up_answers": [{"question_id": "q_fever", "answer": "no"}],
            },
        )
        assert resp.status_code == 200
        payload = resp.json()
        validate_instance(payload, "run")

        # Ensure we can fetch it back.
        run_id = payload["run_id"]
        resp2 = client.get(f"/runs/{run_id}")
        assert resp2.status_code == 200
        validate_instance(resp2.json(), "run")

    # Clean up (defense-in-depth; tmp_path should be cleaned by pytest anyway).
    os.environ.pop("PHARMASSIST_DB_PATH", None)
