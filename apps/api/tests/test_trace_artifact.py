import asyncio


def test_completed_run_includes_trace_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))

    from pharmassist_api import db
    from pharmassist_api.contracts.validate_schema import validate_instance
    from pharmassist_api.orchestrator import new_run_with_answers, run_pipeline

    db.init_db()

    # Provide enough follow-up answers so the pipeline completes in one run.
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
    assert stored["status"] == "completed"
    assert "trace" in (stored.get("artifacts") or {})

    # Contract gate: the full run object must validate (incl. trace).
    validate_instance(stored, "run")

