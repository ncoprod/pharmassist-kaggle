from pharmassist_api.contracts.validate_schema import validate_instance
from pharmassist_api.steps.a5_safety import compute_safety_warnings


def test_safety_engine_warns_when_pregnancy_unknown_for_tagged_product():
    llm_context = {"schema_version": "0.0.0", "demographics": {"age_years": 30, "sex": "F"}}
    products_by_sku = {
        "SKU-0001": {
            "schema_version": "0.0.0",
            "sku": "SKU-0001",
            "name": "Example",
            "category": "allergy",
            "ingredients": ["cetirizine"],
            "contraindication_tags": ["pregnancy_unknown"],
            "in_stock": True,
        }
    }
    ranked_products = [{"product_sku": "SKU-0001", "score_0_100": 90, "why": "test"}]

    warnings = compute_safety_warnings(
        llm_context=llm_context,
        follow_up_answers=None,
        products_by_sku=products_by_sku,
        ranked_products=ranked_products,
        escalation=None,
    )

    assert any(w.get("code") == "PREGNANCY_STATUS_UNKNOWN" for w in warnings)
    reco = {
        "schema_version": "0.0.0",
        "ranked_products": ranked_products,
        "safety_warnings": warnings,
        "follow_up_questions": [],
        "confidence": 0.5,
    }
    validate_instance(reco, "recommendation")


def test_safety_engine_includes_escalation_warning():
    llm_context = {"schema_version": "0.0.0", "demographics": {"age_years": 55, "sex": "M"}}
    warnings = compute_safety_warnings(
        llm_context=llm_context,
        follow_up_answers=None,
        products_by_sku={},
        ranked_products=[],
        escalation={"recommended": True, "reason": "Red flags detected", "suggested_service": "ER"},
    )

    assert any(w.get("code") == "ESCALATION_RECOMMENDED" for w in warnings)

