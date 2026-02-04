import asyncio


def test_orchestrator_sets_needs_more_info_when_follow_up_required(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))

    from pharmassist_api import db
    from pharmassist_api import orchestrator

    db.init_db()

    run = orchestrator.new_run(case_ref="case_lowinfo_000102", language="en", trigger="manual")
    asyncio.run(orchestrator.run_pipeline(run["run_id"]))

    stored = db.get_run(run["run_id"])
    assert stored is not None
    assert stored["status"] == "needs_more_info"
    assert "recommendation" in stored["artifacts"]
    assert "intake_extracted" in stored["artifacts"]
    qids = {
        q.get("question_id")
        for q in (stored["artifacts"]["recommendation"].get("follow_up_questions") or [])
        if isinstance(q, dict)
    }
    assert {"q_duration", "q_fever", "q_breathing"} <= qids
