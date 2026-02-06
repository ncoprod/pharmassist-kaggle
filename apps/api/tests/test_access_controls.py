from fastapi.testclient import TestClient


def test_patients_endpoint_loopback_only_without_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))

    from pharmassist_api.main import app

    with TestClient(app, client=("8.8.8.8", 12000)) as client:
        resp = client.get("/patients", params={"query": "pt_0000"})
        assert resp.status_code == 403


def test_patients_endpoint_denies_forwarded_headers_without_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))

    from pharmassist_api.main import app

    with TestClient(app) as client:
        resp = client.get(
            "/patients",
            params={"query": "pt_0000"},
            headers={"X-Forwarded-For": "203.0.113.10"},
        )
        assert resp.status_code == 403


def test_endpoints_require_api_key_when_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("PHARMASSIST_API_KEY", "appsecret")

    from pharmassist_api.main import app

    with TestClient(app, client=("8.8.8.8", 12001)) as client:
        denied = client.get("/patients", params={"query": "pt_0000"})
        assert denied.status_code == 401

        allowed = client.get(
            "/patients",
            params={"query": "pt_0000"},
            headers={"X-Api-Key": "appsecret"},
        )
        assert allowed.status_code == 200

        create_denied = client.post(
            "/runs",
            json={"case_ref": "case_000042", "language": "en", "trigger": "manual"},
        )
        assert create_denied.status_code == 401

        create_ok = client.post(
            "/runs",
            json={"case_ref": "case_000042", "language": "en", "trigger": "manual"},
            headers={"X-Api-Key": "appsecret"},
        )
        assert create_ok.status_code == 200
        run_id = create_ok.json()["run_id"]

        get_denied = client.get(f"/runs/{run_id}")
        assert get_denied.status_code == 401

        get_ok = client.get(f"/runs/{run_id}", headers={"X-Api-Key": "appsecret"})
        assert get_ok.status_code == 200


def test_events_stream_token_flow_when_api_key_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("PHARMASSIST_API_KEY", "appsecret")

    from pharmassist_api.main import app

    with TestClient(app, client=("8.8.8.8", 12002)) as client:
        create_ok = client.post(
            "/runs",
            json={"case_ref": "case_000042", "language": "en", "trigger": "manual"},
            headers={"X-Api-Key": "appsecret"},
        )
        assert create_ok.status_code == 200
        run_id = create_ok.json()["run_id"]

        token_denied = client.post(f"/runs/{run_id}/events-token")
        assert token_denied.status_code == 401

        token_ok = client.post(
            f"/runs/{run_id}/events-token",
            headers={"X-Api-Key": "appsecret"},
        )
        assert token_ok.status_code == 200
        stream_token = token_ok.json()["stream_token"]
        assert isinstance(stream_token, str)
        assert len(stream_token) >= 20

        invalid_token = client.get(
            f"/runs/{run_id}/events",
            params={"stream_token": "badtoken", "after": 999999},
        )
        assert invalid_token.status_code == 401

        with client.stream(
            "GET",
            f"/runs/{run_id}/events",
            params={"stream_token": stream_token, "after": 999999},
        ) as stream_resp:
            assert stream_resp.status_code == 200
