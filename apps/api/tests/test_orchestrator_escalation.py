import asyncio


def test_orchestrator_redflag_escalates_and_stops_early(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))

    from pharmassist_api import db
    from pharmassist_api import orchestrator

    db.init_db()

    run = orchestrator.new_run(case_ref="case_redflag_000101", language="fr", trigger="manual")
    asyncio.run(orchestrator.run_pipeline(run["run_id"]))

    stored = db.get_run(run["run_id"])
    assert stored is not None
    assert stored["status"] == "completed"

    rec = stored["artifacts"]["recommendation"]
    assert rec.get("escalation", {}).get("recommended") is True

    # Safety: do not recommend products when escalation is recommended.
    assert rec.get("ranked_products") == []

    # Evidence retrieval should not run on early-stop escalation path.
    assert stored["artifacts"].get("evidence_items") in (None, [])

