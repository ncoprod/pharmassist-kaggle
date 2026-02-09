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


def test_orchestrator_final_policy_gate_fails_safe_on_handout(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))

    from pharmassist_api import db
    from pharmassist_api import orchestrator as orch

    db.init_db()
    run = orch.new_run(case_ref="case_000042", language="en", trigger="manual")

    # Simulate a policy-violating handout being produced late in the pipeline.
    monkeypatch.setattr(
        orch,
        "compose_handout_markdown",
        lambda **_kwargs: "Start your antibiotic prescription now.",
    )

    asyncio.run(orch.run_pipeline(run["run_id"]))

    stored = db.get_run(run["run_id"])
    assert stored is not None
    assert stored["status"] == "failed_safe"
    assert stored["policy_violations"]
    assert "handout_markdown" not in (stored.get("artifacts") or {})


def test_orchestrator_final_policy_gate_fails_safe_on_planner_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("PHARMASSIST_USE_AGENTIC_PLANNER", "1")

    from pharmassist_api import db
    from pharmassist_api import orchestrator as orch

    db.init_db()
    run = orch.new_run(case_ref="case_000042", language="en", trigger="manual")

    monkeypatch.setattr(orch, "planner_feature_enabled", lambda: True)
    monkeypatch.setattr(
        orch,
        "build_planner_plan",
        lambda **_kwargs: {
            "schema_version": "0.0.0",
            "planner_version": "feb14-v1",
            "generated_at": "2026-02-09T00:00:00Z",
            "mode": "agentic",
            "fallback_used": False,
            "trace_meta": {"plan_source": "llm_json"},
            "safety_checks": ["No PHI in plan artifact."],
            "steps": [
                {
                    "step_id": "agentic-1",
                    "kind": "otc_suggestion",
                    "title": "Unsafe Rx advice",
                    "detail": "Stop your prescription medication now.",
                    "evidence_refs": [],
                }
            ],
        },
    )

    asyncio.run(orch.run_pipeline(run["run_id"]))

    stored = db.get_run(run["run_id"])
    assert stored is not None
    assert stored["status"] == "failed_safe"
    assert stored["policy_violations"]
    assert "plan" not in (stored.get("artifacts") or {})
