from pharmassist_api.cases.load_case import load_case_bundle
from pharmassist_api.contracts.validate_schema import validate_instance
from pharmassist_api.steps.a3_triage import triage_and_followup
from pharmassist_api.steps.a4_evidence_retrieval import retrieve_evidence
from pharmassist_api.steps.a5_safety import compute_safety_warnings
from pharmassist_api.steps.a6_product_ranker import rank_products
from pharmassist_api.steps.a7_report_composer import compose_report_markdown
from pharmassist_api.steps.a8_handout import compose_handout_markdown
from pharmassist_api.validators.citations import lint_citations
from pharmassist_api.validators.rx_advice_lint import lint_rx_advice


def test_report_and_handout_are_policy_safe_and_citations_valid():
    bundle = load_case_bundle("case_000042")

    intake, reco, needs_more_info, _meta = triage_and_followup(
        intake_extracted=bundle["intake_extracted"],
        llm_context=bundle["llm_context"],
        follow_up_answers=[
            {"question_id": "q_fever", "answer": "no"},
            {"question_id": "q_breathing", "answer": "no"},
            {"question_id": "q_pregnancy", "answer": "no"},
        ],
        language="en",
    )
    assert needs_more_info is False
    validate_instance(intake, "intake_extracted")
    validate_instance(reco, "recommendation")

    ranked, ranker_warnings = rank_products(
        intake_extracted=intake,
        llm_context=bundle["llm_context"],
        follow_up_answers=[
            {"question_id": "q_fever", "answer": "no"},
            {"question_id": "q_breathing", "answer": "no"},
            {"question_id": "q_pregnancy", "answer": "no"},
        ],
        products=bundle["products"],
    )
    reco["ranked_products"] = ranked

    products_by_sku = {p["sku"]: p for p in bundle["products"]}
    safety = compute_safety_warnings(
        llm_context=bundle["llm_context"],
        follow_up_answers=[
            {"question_id": "q_fever", "answer": "no"},
            {"question_id": "q_breathing", "answer": "no"},
            {"question_id": "q_pregnancy", "answer": "no"},
        ],
        products_by_sku=products_by_sku,
        ranked_products=ranked,
        escalation=reco.get("escalation"),
    )
    reco["safety_warnings"] = list(reco.get("safety_warnings") or []) + list(ranker_warnings) + list(safety)

    evidence_items = retrieve_evidence(
        intake_extracted=intake,
        llm_context=bundle["llm_context"],
        k=5,
    )
    for ev in evidence_items:
        validate_instance(ev, "evidence_item")

    report = compose_report_markdown(
        intake_extracted=intake,
        recommendation=reco,
        evidence_items=evidence_items,
        language="en",
    )
    assert report.strip()
    assert "Do not change prescription treatment without medical advice." in report
    assert not lint_rx_advice(report, path="$.report_markdown")

    evidence_ids = {e["evidence_id"] for e in evidence_items}
    assert not lint_citations(report, evidence_ids=evidence_ids, path="$.report_markdown")

    handout = compose_handout_markdown(recommendation=reco, language="en")
    assert handout.strip()
    assert not lint_rx_advice(handout, path="$.handout_markdown")


def test_report_composer_skips_model_call_when_prompt_has_phi(monkeypatch):
    monkeypatch.setenv("PHARMASSIST_USE_MEDGEMMA_REPORT", "1")

    from pharmassist_api.steps import a7_report_composer as mod

    called = {"n": 0}

    def _fake_generate_text(*, user_content: str, system: str, max_new_tokens: int = 0):
        called["n"] += 1
        return "# should-not-be-called\n"

    monkeypatch.setattr(mod, "medgemma_generate_text", _fake_generate_text)

    intake_extracted = {
        "schema_version": "0.0.0",
        "presenting_problem": "Name: John Doe",
        "symptoms": [{"label": "sneezing", "severity": "unknown"}],
        "red_flags": [],
    }

    report = mod.compose_report_markdown(
        intake_extracted=intake_extracted,
        recommendation=None,
        evidence_items=None,
        language="en",
    )

    assert called["n"] == 0
    assert report.startswith("# Pharmacist report")
