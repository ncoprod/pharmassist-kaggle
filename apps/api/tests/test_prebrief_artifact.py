from __future__ import annotations

import asyncio


def test_completed_run_includes_prebrief_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))

    from pharmassist_api import db
    from pharmassist_api.contracts.validate_schema import validate_instance
    from pharmassist_api.orchestrator import new_run_with_answers, run_pipeline

    db.init_db()

    run = new_run_with_answers(
        case_ref="case_000042",
        language="en",
        trigger="manual",
        follow_up_answers=[
            {"question_id": "q_fever", "answer": "no"},
            {"question_id": "q_breathing", "answer": "no"},
            {"question_id": "q_pregnancy", "answer": "no"},
        ],
    )
    asyncio.run(run_pipeline(run["run_id"]))

    stored = db.get_run(run["run_id"])
    assert stored is not None
    prebrief = (stored.get("artifacts") or {}).get("prebrief")
    assert isinstance(prebrief, dict)
    validate_instance(prebrief, "prebrief")
    assert isinstance(prebrief.get("new_rx_delta"), list)
    what_changed = prebrief.get("what_changed")
    assert isinstance(what_changed, list)
    assert any("=" in item for item in what_changed)
