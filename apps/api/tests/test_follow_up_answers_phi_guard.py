import os

from fastapi.testclient import TestClient


def test_create_run_rejects_phi_in_follow_up_answers(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))

    from pharmassist_api.main import app

    with TestClient(app) as client:
        resp = client.post(
            "/runs",
            json={
                "case_ref": "case_000042",
                "language": "fr",
                "trigger": "manual",
                "follow_up_answers": [
                    {"question_id": "q_duration", "answer": "email test@example.com"}
                ],
            },
        )
        assert resp.status_code == 400
        payload = resp.json()
        assert "detail" in payload
        assert payload["detail"]["error"] == "PHI detected in follow_up_answers"

    os.environ.pop("PHARMASSIST_DB_PATH", None)

