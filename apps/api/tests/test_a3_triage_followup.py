from pharmassist_api.contracts.validate_schema import validate_instance
from pharmassist_api.steps.a3_triage import triage_and_followup


def test_triage_allergy_case_is_non_blocking_by_default():
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

    updated, reco, needs_more_info, _meta = triage_and_followup(
        intake_extracted=intake,
        llm_context=llm_context,
        follow_up_answers=None,
        language="en",
    )

    validate_instance(updated, "intake_extracted")
    validate_instance(reco, "recommendation")
    # Most runs should complete without blocking on follow-up questions.
    assert needs_more_info is False
    assert reco["follow_up_questions"] == []


def test_triage_escalates_on_red_flags():
    intake = {
        "schema_version": "0.0.0",
        "presenting_problem": "Difficulty breathing and chest pain",
        "symptoms": [{"label": "shortness of breath", "severity": "severe"}],
        "red_flags": [],
    }
    llm_context = {"demographics": {"age_years": 55, "sex": "M"}, "schema_version": "0.0.0"}

    updated, reco, needs_more_info, _meta = triage_and_followup(
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


def test_triage_low_info_blocks_until_min_questions_answered():
    intake = {
        "schema_version": "0.0.0",
        "presenting_problem": "unspecified",
        "symptoms": [{"label": "unspecified symptom", "severity": "unknown"}],
        "red_flags": [],
    }
    llm_context = {"demographics": {"age_years": 21, "sex": "F"}, "schema_version": "0.0.0"}

    updated, reco, needs_more_info, _meta = triage_and_followup(
        intake_extracted=intake,
        llm_context=llm_context,
        follow_up_answers=None,
        language="en",
    )

    validate_instance(updated, "intake_extracted")
    validate_instance(reco, "recommendation")
    assert needs_more_info is True
    qids = {q["question_id"] for q in reco["follow_up_questions"]}
    assert {"q_duration", "q_fever", "q_breathing"} <= qids

    # Once answered, the run should proceed.
    answers = [
        {"question_id": "q_duration", "answer": "7"},
        {"question_id": "q_fever", "answer": "no"},
        {"question_id": "q_breathing", "answer": "no"},
    ]
    updated2, reco2, needs_more_info2, _meta2 = triage_and_followup(
        intake_extracted=intake,
        llm_context=llm_context,
        follow_up_answers=answers,
        language="en",
    )
    validate_instance(updated2, "intake_extracted")
    validate_instance(reco2, "recommendation")
    assert needs_more_info2 is False


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

    updated, reco, needs_more_info, _meta = triage_and_followup(
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
