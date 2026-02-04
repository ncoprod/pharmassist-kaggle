from __future__ import annotations

from typing import Any

from pharmassist_api.contracts.validate_schema import validate_or_return_errors

SCHEMA_VERSION = "0.0.0"


def compute_safety_warnings(
    *,
    llm_context: dict[str, Any],
    follow_up_answers: list[dict[str, Any]] | None,
    products_by_sku: dict[str, dict[str, Any]],
    ranked_products: list[dict[str, Any]],
    escalation: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Deterministic safety engine (Day 6).

    Produces recommendation.safety_warnings[] items (no Rx advice, no PHI).
    """
    answers = _answers_to_map(follow_up_answers)
    pregnancy_status = _pregnancy_status(llm_context, answers)
    allergy_terms = _allergy_terms(llm_context)

    out: list[dict[str, Any]] = []

    if isinstance(escalation, dict) and escalation.get("recommended") is True:
        out.append(
            {
                "code": "ESCALATION_RECOMMENDED",
                "severity": "WARN",
                "message": str(escalation.get("reason") or "Escalation recommended."),
            }
        )

    for item in ranked_products:
        sku = item.get("product_sku") if isinstance(item, dict) else None
        if not isinstance(sku, str) or not sku:
            continue
        p = products_by_sku.get(sku)
        if not isinstance(p, dict):
            continue

        tags = p.get("contraindication_tags") or []

        if _matches_allergy(p, allergy_terms):
            out.append(
                {
                    "code": "ALLERGY_MATCH",
                    "severity": "BLOCKER",
                    "message": "Patient allergy may match a product ingredient.",
                    "related_product_sku": sku,
                }
            )

        if pregnancy_status == "unknown" and "pregnancy_unknown" in tags:
            out.append(
                {
                    "code": "PREGNANCY_STATUS_UNKNOWN",
                    "severity": "WARN",
                    "message": (
                        "Pregnancy status is unknown. Confirm before recommending if relevant."
                    ),
                    "related_product_sku": sku,
                }
            )

        if pregnancy_status == "pregnant" and "pregnancy_contraindicated" in tags:
            out.append(
                {
                    "code": "PREGNANCY_CONTRAINDICATED",
                    "severity": "BLOCKER",
                    "message": "Contraindicated in pregnancy.",
                    "related_product_sku": sku,
                }
            )

    # Deduplicate while keeping order.
    seen: set[tuple[str, str | None]] = set()
    deduped: list[dict[str, Any]] = []
    for w in out:
        key = (str(w.get("code") or ""), w.get("related_product_sku"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(w)

    # Validate as recommendation fragments.
    safe: list[dict[str, Any]] = []
    for w in deduped:
        if not validate_or_return_errors(
            {
                "schema_version": SCHEMA_VERSION,
                "ranked_products": [],
                "safety_warnings": [w],
                "follow_up_questions": [],
                "confidence": 0.1,
            },
            "recommendation",
        ):
            safe.append(w)
    return safe


def _answers_to_map(follow_up_answers: list[dict[str, Any]] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in follow_up_answers or []:
        if not isinstance(item, dict):
            continue
        qid = item.get("question_id")
        ans = item.get("answer")
        if isinstance(qid, str) and qid and isinstance(ans, str):
            ans = ans.strip()
            if ans:
                out[qid] = ans
    return out


def _is_yes(text: str | None) -> bool:
    if not text:
        return False
    t = text.strip().lower()
    return t in {"yes", "y", "oui", "true", "1"}


def _pregnancy_status(llm_context: dict[str, Any], answers: dict[str, str]) -> str:
    demo = llm_context.get("demographics") if isinstance(llm_context, dict) else None
    sex = demo.get("sex") if isinstance(demo, dict) else None
    if isinstance(sex, str) and sex.strip().upper().startswith("M"):
        # Pregnancy is not applicable for male patients; avoid irrelevant warnings.
        return "not_applicable"

    preg = llm_context.get("pregnancy_status") if isinstance(llm_context, dict) else None
    if isinstance(preg, str) and preg:
        return preg

    if _is_yes(answers.get("q_pregnancy")):
        return "pregnant"
    if answers.get("q_pregnancy") is not None:
        return "not_pregnant"
    return "unknown"


def _allergy_terms(llm_context: dict[str, Any]) -> set[str]:
    terms: set[str] = set()
    for a in (llm_context.get("allergies") or []) if isinstance(llm_context, dict) else []:
        if isinstance(a, dict):
            sub = a.get("substance")
            if isinstance(sub, str) and sub:
                terms.add(sub.lower())
    return terms


def _matches_allergy(product: dict[str, Any], allergy_terms: set[str]) -> bool:
    if not allergy_terms:
        return False
    ingredients = " ".join([str(x) for x in (product.get("ingredients") or [])]).lower()
    name = str(product.get("name") or "").lower()
    return any(t in ingredients or t in name for t in allergy_terms)
