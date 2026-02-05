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


def test_followup_selector_is_not_attempted_when_no_followup_required(monkeypatch):
    # Even if the MedGemma follow-up selector is enabled, we must not attempt
    # to select questions when the rules-first triage does not require follow-up.
    monkeypatch.setenv("PHARMASSIST_USE_MEDGEMMA_FOLLOWUP", "1")

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

    _updated, reco, needs_more_info, meta = triage_and_followup(
        intake_extracted=intake,
        llm_context=llm_context,
        follow_up_answers=None,
        language="en",
    )

    assert needs_more_info is False
    assert reco["follow_up_questions"] == []

    sel = meta.get("followup_selector") if isinstance(meta, dict) else None
    assert isinstance(sel, dict)
    assert sel.get("attempted") is False


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


def test_triage_escalates_on_noisy_dyspnea_label():
    # OCR noise can introduce leetspeak digits (e.g. dy5pnea). We should still
    # detect breathing difficulty and escalate.
    intake = {
        "schema_version": "0.0.0",
        "presenting_problem": "Symptomes non specifie(s)",
        "symptoms": [{"label": "dy5pnea", "severity": "severe"}],
        "red_flags": [],
    }
    llm_context = {"demographics": {"age_years": 62, "sex": "M"}, "schema_version": "0.0.0"}

    updated, reco, needs_more_info, _meta = triage_and_followup(
        intake_extracted=intake,
        llm_context=llm_context,
        follow_up_answers=None,
        language="fr",
    )

    validate_instance(updated, "intake_extracted")
    validate_instance(reco, "recommendation")
    assert needs_more_info is False
    assert reco.get("escalation", {}).get("recommended") is True
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
    assert {
        "q_primary_domain",
        "q_overall_severity",
        "q_fever",
        "q_breathing",
        "q_chest_pain",
    } <= qids

    # Once answered, the run should proceed.
    answers = [
        {"question_id": "q_primary_domain", "answer": "digestive"},
        {"question_id": "q_overall_severity", "answer": "mild"},
        {"question_id": "q_fever", "answer": "no"},
        {"question_id": "q_breathing", "answer": "no"},
        {"question_id": "q_chest_pain", "answer": "no"},
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


def test_triage_low_info_detects_ocr_spaced_unspecified_label():
    # Some OCR paths introduce spacing inside tokens (e.g. "unspec  ified").
    intake = {
        "schema_version": "0.0.0",
        "presenting_problem": "unspecified",
        "symptoms": [{"label": "unspec  ified symptom", "severity": "unknown"}],
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
    assert {
        "q_primary_domain",
        "q_overall_severity",
        "q_fever",
        "q_breathing",
        "q_chest_pain",
    } <= qids


def test_triage_low_info_questions_render_in_fr_and_en():
    intake = {
        "schema_version": "0.0.0",
        "presenting_problem": "unspecified",
        "symptoms": [{"label": "unspecified symptom", "severity": "unknown"}],
        "red_flags": [],
    }
    llm_context = {"demographics": {"age_years": 34, "sex": "F"}, "schema_version": "0.0.0"}

    for language in ("fr", "en"):
        _updated, reco, needs_more_info, _meta = triage_and_followup(
            intake_extracted=intake,
            llm_context=llm_context,
            follow_up_answers=None,
            language=language,
        )
        assert needs_more_info is True
        qs = {q["question_id"]: q for q in reco["follow_up_questions"]}
        assert "q_primary_domain" in qs
        assert qs["q_primary_domain"].get("answer_type") == "choice"
        assert isinstance(qs["q_primary_domain"].get("choices"), list)
        assert "q_breathing" in qs
        assert "q_fever" in qs


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
