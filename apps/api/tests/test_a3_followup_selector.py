from pharmassist_api.steps.a3_followup_selector import maybe_select_followup_question_ids
from pharmassist_api.steps.question_bank import load_question_bank


def test_followup_selector_filters_unknown_ids_and_dedupes(monkeypatch):
    monkeypatch.setenv("PHARMASSIST_USE_MEDGEMMA_FOLLOWUP", "1")

    # Patch the model call so tests remain CI-safe (no model downloads).
    def fake_gen(*, user_content: str, system: str, max_new_tokens: int = 0):  # noqa: ARG001
        return '{"schema_version":"0.0.0","question_ids":["q_fever","q_unknown","q_breathing","q_fever"]}'

    monkeypatch.setattr(
        "pharmassist_api.steps.a3_followup_selector.medgemma_generate_text",
        fake_gen,
    )

    bank = load_question_bank()
    intake = {
        "schema_version": "0.0.0",
        "presenting_problem": "Sneezing",
        "symptoms": [{"label": "sneezing", "severity": "mild"}],
        "red_flags": [],
    }
    llm_context = {"schema_version": "0.0.0", "demographics": {"age_years": 21, "sex": "F"}}

    selected, meta = maybe_select_followup_question_ids(
        intake_extracted=intake,
        llm_context=llm_context,
        candidate_ids=["q_fever", "q_breathing"],
        question_bank=bank,
        language="en",
        max_k=5,
    )
    assert selected == ["q_fever", "q_breathing"]
    assert meta["attempted"] is True
    assert meta["mode"] == "medgemma"


def test_followup_selector_falls_back_on_invalid_output(monkeypatch):
    monkeypatch.setenv("PHARMASSIST_USE_MEDGEMMA_FOLLOWUP", "1")

    def fake_gen(*, user_content: str, system: str, max_new_tokens: int = 0):  # noqa: ARG001
        return "not json"

    monkeypatch.setattr(
        "pharmassist_api.steps.a3_followup_selector.medgemma_generate_text",
        fake_gen,
    )

    bank = load_question_bank()
    intake = {
        "schema_version": "0.0.0",
        "presenting_problem": "Sneezing",
        "symptoms": [{"label": "sneezing", "severity": "mild"}],
        "red_flags": [],
    }
    llm_context = {"schema_version": "0.0.0", "demographics": {"age_years": 21, "sex": "F"}}

    selected, meta = maybe_select_followup_question_ids(
        intake_extracted=intake,
        llm_context=llm_context,
        candidate_ids=["q_fever"],
        question_bank=bank,
        language="en",
        max_k=5,
    )
    assert selected is None
    assert meta["attempted"] is True
    assert meta["mode"] == "fallback"

