import pytest


def test_new_run_with_answers_rejects_phi_in_follow_up_answers(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))

    from pharmassist_api import db
    from pharmassist_api import orchestrator as orch

    db.init_db()

    with pytest.raises(ValueError):
        orch.new_run_with_answers(
            case_ref="case_000042",
            language="en",
            trigger="manual",
            follow_up_answers=[{"question_id": "q_fever", "answer": "Telephone: 0612345678"}],
        )

