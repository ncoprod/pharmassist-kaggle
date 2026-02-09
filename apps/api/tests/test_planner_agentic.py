from __future__ import annotations

import asyncio
import json


def _answers() -> list[dict[str, str]]:
    return [
        {"question_id": "q_fever", "answer": "no"},
        {"question_id": "q_breathing", "answer": "no"},
        {"question_id": "q_pregnancy", "answer": "no"},
    ]


def test_agentic_planner_uses_valid_json_when_flag_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("PHARMASSIST_USE_AGENTIC_PLANNER", "1")
    monkeypatch.setenv(
        "PHARMASSIST_AGENTIC_PLANNER_RAW_JSON",
        json.dumps(
            {
                "safety_checks": ["No PHI."],
                "steps": [
                    {
                        "kind": "safety_check",
                        "title": "Check danger signs",
                        "detail": "Verify no breathing red flags.",
                        "evidence_refs": ["ev_0001"],
                    }
                ],
            }
        ),
    )

    from pharmassist_api import db
    from pharmassist_api.contracts.validate_schema import validate_instance
    from pharmassist_api.orchestrator import new_run_with_answers, run_pipeline

    db.init_db()

    run = new_run_with_answers(
        case_ref="case_000042",
        language="en",
        trigger="manual",
        follow_up_answers=_answers(),
    )
    asyncio.run(run_pipeline(run["run_id"]))

    stored = db.get_run(run["run_id"])
    assert stored is not None
    plan = (stored.get("artifacts") or {}).get("plan")
    assert isinstance(plan, dict)
    assert plan.get("mode") == "agentic"
    assert plan.get("fallback_used") is False
    validate_instance(plan, "planner_plan")


def test_agentic_planner_falls_back_on_invalid_json(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("PHARMASSIST_USE_AGENTIC_PLANNER", "1")
    monkeypatch.setenv("PHARMASSIST_AGENTIC_PLANNER_RAW_JSON", "{not-json")

    from pharmassist_api import db
    from pharmassist_api.contracts.validate_schema import validate_instance
    from pharmassist_api.orchestrator import new_run_with_answers, run_pipeline

    db.init_db()

    run = new_run_with_answers(
        case_ref="case_000042",
        language="en",
        trigger="manual",
        follow_up_answers=_answers(),
    )
    asyncio.run(run_pipeline(run["run_id"]))

    stored = db.get_run(run["run_id"])
    assert stored is not None
    plan = (stored.get("artifacts") or {}).get("plan")
    assert isinstance(plan, dict)
    assert plan.get("mode") == "fallback_deterministic"
    assert plan.get("fallback_used") is True
    validate_instance(plan, "planner_plan")


def test_agentic_planner_falls_back_on_disallowed_kind_or_extra_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("PHARMASSIST_USE_AGENTIC_PLANNER", "1")
    monkeypatch.setenv(
        "PHARMASSIST_AGENTIC_PLANNER_RAW_JSON",
        json.dumps(
            {
                "safety_checks": ["No PHI."],
                "steps": [
                    {
                        "kind": "unsafe_kind",
                        "title": "Do something unsafe",
                        "detail": "This must be rejected by allowlist.",
                        "evidence_refs": [],
                    }
                ],
                "unexpected_top_level": True,
            }
        ),
    )

    from pharmassist_api import db
    from pharmassist_api.contracts.validate_schema import validate_instance
    from pharmassist_api.orchestrator import new_run_with_answers, run_pipeline

    db.init_db()

    run = new_run_with_answers(
        case_ref="case_000042",
        language="en",
        trigger="manual",
        follow_up_answers=_answers(),
    )
    asyncio.run(run_pipeline(run["run_id"]))

    stored = db.get_run(run["run_id"])
    assert stored is not None
    plan = (stored.get("artifacts") or {}).get("plan")
    assert isinstance(plan, dict)
    assert plan.get("mode") == "fallback_deterministic"
    assert plan.get("fallback_used") is True
    validate_instance(plan, "planner_plan")


def test_planner_artifact_absent_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.delenv("PHARMASSIST_USE_AGENTIC_PLANNER", raising=False)
    monkeypatch.delenv("PHARMASSIST_AGENTIC_PLANNER_RAW_JSON", raising=False)

    from pharmassist_api import db
    from pharmassist_api.orchestrator import new_run_with_answers, run_pipeline

    db.init_db()

    run = new_run_with_answers(
        case_ref="case_000042",
        language="en",
        trigger="manual",
        follow_up_answers=_answers(),
    )
    asyncio.run(run_pipeline(run["run_id"]))

    stored = db.get_run(run["run_id"])
    assert stored is not None
    artifacts = stored.get("artifacts") or {}
    assert "plan" not in artifacts
