import asyncio


def test_orchestrator_fails_safe_if_a1_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))

    from pharmassist_api import db
    from pharmassist_api import orchestrator

    db.init_db()

    run = orchestrator.new_run(case_ref="case_000042", language="fr", trigger="manual")
    run_id = run["run_id"]

    def _boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(orchestrator, "extract_intake", _boom)

    asyncio.run(orchestrator.run_pipeline(run_id))

    stored = db.get_run(run_id)
    assert stored is not None
    assert stored["status"] == "failed_safe"

    events = db.list_events(run_id)
    assert any(e["data"].get("type") == "finalized" for e in events)

