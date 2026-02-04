from pharmassist_api.contracts.validate_schema import validate_instance
from pharmassist_api.steps.a3_triage import triage_and_followup


def test_triage_generates_follow_up_questions_for_allergy_case():
    intake = {
        "schema_version": "0.0.0",
        "presenting_problem": "Sneezing and itchy eyes for one week",
        "symptoms": [
            {"label": "sneezing", "severity": "moderate", "duration_days": 7},
            {"label": "itchy eyes", "severity": "mild", "duration_days": 7},
        ],
        "red_flags": [],
    }
    llm_context = {"demographics": {"age_years": 21, "sex": "F"}, "schema_version": "0.0.0"}

    updated, reco, needs_more_info = triage_and_followup(
        intake_extracted=intake,
        llm_context=llm_context,
        follow_up_answers=None,
        language="en",
    )

    validate_instance(updated, "intake_extracted")
    validate_instance(reco, "recommendation")
    assert needs_more_info is True
    qids = {q["question_id"] for q in reco["follow_up_questions"]}
    assert {"q_fever", "q_breathing", "q_pregnancy"} <= qids


def test_triage_escalates_on_red_flags():
    intake = {
        "schema_version": "0.0.0",
        "presenting_problem": "Difficulty breathing and chest pain",
        "symptoms": [{"label": "shortness of breath", "severity": "severe"}],
        "red_flags": [],
    }
    llm_context = {"demographics": {"age_years": 55, "sex": "M"}, "schema_version": "0.0.0"}

    updated, reco, needs_more_info = triage_and_followup(
        intake_extracted=intake,
        llm_context=llm_context,
        follow_up_answers=None,
        language="en",
    )

    validate_instance(updated, "intake_extracted")
    validate_instance(reco, "recommendation")
    assert needs_more_info is False
    assert "escalation" in reco
    assert reco["escalation"]["recommended"] is True
    assert "RF_BREATHING_DIFFICULTY" in updated["red_flags"]


def test_triage_no_follow_up_when_answers_present():
    intake = {
        "schema_version": "0.0.0",
        "presenting_problem": "Sneezing and itchy eyes for one week",
        "symptoms": [
            {"label": "sneezing", "severity": "moderate", "duration_days": 7},
            {"label": "itchy eyes", "severity": "mild", "duration_days": 7},
        ],
        "red_flags": [],
    }
    llm_context = {"demographics": {"age_years": 21, "sex": "F"}, "schema_version": "0.0.0"}

    answers = [
        {"question_id": "q_fever", "answer": "no"},
        {"question_id": "q_breathing", "answer": "no"},
        {"question_id": "q_pregnancy", "answer": "no"},
    ]

    updated, reco, needs_more_info = triage_and_followup(
        intake_extracted=intake,
        llm_context=llm_context,
        follow_up_answers=answers,
        language="en",
    )

    validate_instance(updated, "intake_extracted")
    validate_instance(reco, "recommendation")
    assert needs_more_info is False
    assert reco["follow_up_questions"] == []


def test_high_fever_temperature_triggers_red_flag():
    intake = {
        "schema_version": "0.0.0",
        "presenting_problem": "Fever and fatigue",
        "symptoms": [{"label": "fever", "severity": "unknown"}],
        "red_flags": [],
    }
    llm_context = {"demographics": {"age_years": 30, "sex": "M"}, "schema_version": "0.0.0"}

    answers = [
        {"question_id": "q_fever", "answer": "yes"},
        {"question_id": "q_temperature", "answer": "39.5"},
    ]

    updated, reco, needs_more_info = triage_and_followup(
        intake_extracted=intake,
        llm_context=llm_context,
        follow_up_answers=answers,
        language="en",
    )

    validate_instance(updated, "intake_extracted")
    validate_instance(reco, "recommendation")
    assert needs_more_info is False
    assert "RF_HIGH_FEVER" in updated["red_flags"]
    assert reco.get("escalation", {}).get("recommended") is True
