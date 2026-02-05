import asyncio


def test_orchestrator_final_policy_gate_fails_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))

    from pharmassist_api import db
    from pharmassist_api import orchestrator as orch

    db.init_db()
    run = orch.new_run(case_ref="case_000042", language="en", trigger="manual")

    # Simulate a policy-violating artifact being produced late in the pipeline.
    monkeypatch.setattr(
        orch,
        "compose_report_markdown",
        lambda **_kwargs: "Stop taking your prescription medication.",
    )

    asyncio.run(orch.run_pipeline(run["run_id"]))

    stored = db.get_run(run["run_id"])
    assert stored is not None
    assert stored["status"] == "failed_safe"
    assert stored["policy_violations"]
    assert "report_markdown" not in (stored.get("artifacts") or {})
