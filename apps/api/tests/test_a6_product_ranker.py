from pharmassist_api.cases.load_case import load_case_bundle
from pharmassist_api.contracts.validate_schema import validate_instance
from pharmassist_api.steps.a6_product_ranker import rank_products


def test_ranker_returns_schema_valid_ranked_products_for_allergy_case():
    bundle = load_case_bundle("case_000042")

    intake_extracted = bundle["intake_extracted"]
    llm_context = bundle["llm_context"]
    products = bundle["products"]

    ranked, warnings = rank_products(
        intake_extracted=intake_extracted,
        llm_context=llm_context,
        follow_up_answers=[{"question_id": "q_pregnancy", "answer": "no"}],
        products=products,
    )

    # Minimal sanity.
    assert ranked and len(ranked) <= 3
    for item in ranked:
        assert isinstance(item.get("product_sku"), str) and item["product_sku"]
        assert isinstance(item.get("product_name"), str) and item["product_name"]
        assert isinstance(item.get("score_0_100"), int)
        assert isinstance(item.get("why"), str) and item["why"]

    # Ensure fragments would validate inside a full recommendation payload.
    reco = {
        "schema_version": "0.0.0",
        "ranked_products": ranked,
        "safety_warnings": warnings,
        "follow_up_questions": [],
        "confidence": 0.5,
    }
    validate_instance(reco, "recommendation")


def test_ranker_does_not_emit_pregnancy_warnings_for_male_patient():
    bundle = load_case_bundle("case_000044")

    intake_extracted = bundle["intake_extracted"]
    llm_context = bundle["llm_context"]
    products = bundle["products"]

    ranked, warnings = rank_products(
        intake_extracted=intake_extracted,
        llm_context=llm_context,
        follow_up_answers=None,
        products=products,
    )

    assert ranked and len(ranked) <= 3
    assert not any(w.get("code") == "PREGNANCY_STATUS_UNKNOWN" for w in warnings)


def test_ranker_uses_primary_domain_answer_for_low_info_routing():
    bundle = load_case_bundle("case_lowinfo_000102")

    intake_extracted = bundle["intake_extracted"]
    llm_context = bundle["llm_context"]
    products = bundle["products"]

    ranked, _warnings = rank_products(
        intake_extracted=intake_extracted,
        llm_context=llm_context,
        follow_up_answers=[
            {"question_id": "q_primary_domain", "answer": "digestive"},
            {"question_id": "q_overall_severity", "answer": "mild"},
            {"question_id": "q_fever", "answer": "no"},
            {"question_id": "q_breathing", "answer": "no"},
            {"question_id": "q_chest_pain", "answer": "no"},
        ],
        products=products,
    )

    assert ranked and len(ranked) <= 3
    sku = ranked[0]["product_sku"]
    p = next(p for p in products if p.get("sku") == sku)
    assert p.get("category") == "digestion"
