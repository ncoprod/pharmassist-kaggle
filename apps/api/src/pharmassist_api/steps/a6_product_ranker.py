from __future__ import annotations

import re
from typing import Any

from pharmassist_api.contracts.validate_schema import validate_or_return_errors
from pharmassist_api.steps.a3_triage import _is_yes  # reuse simple yes/no parsing

SCHEMA_VERSION = "0.0.0"


def rank_products(
    *,
    intake_extracted: dict[str, Any],
    llm_context: dict[str, Any],
    follow_up_answers: list[dict[str, Any]] | None,
    products: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Rank OTC/parapharmacy products deterministically (Day 6).

    Returns:
      - ranked_products[] (schema-valid fragments for recommendation.ranked_products)
      - safety_warnings[] (schema-valid fragments for recommendation.safety_warnings)
    """
    answers = _answers_to_map(follow_up_answers)
    pregnancy_status = _pregnancy_status(llm_context, answers)

    allergy_terms = _allergy_terms(llm_context)
    target_category = _infer_target_category(intake_extracted)

    ranked: list[tuple[int, dict[str, Any], str]] = []
    warnings: list[dict[str, Any]] = []

    for p in products:
        if not isinstance(p, dict):
            continue
        if not p.get("in_stock", False):
            continue

        sku = p.get("sku")
        if not isinstance(sku, str) or not sku:
            continue

        # Hard filter: allergy matches ingredient (defense in depth).
        if _matches_allergy(p, allergy_terms):
            warnings.append(
                {
                    "code": "ALLERGY_MATCH",
                    "severity": "BLOCKER",
                    "message": (
                        "Patient allergy may match a product ingredient; excluded from ranking."
                    ),
                    "related_product_sku": sku,
                }
            )
            continue

        score, why = _score_product(
            product=p,
            target_category=target_category,
            pregnancy_status=pregnancy_status,
        )
        ranked.append((score, p, why))

        # Pregnancy warnings are informative, not a hard exclude (unless contraindicated).
        tags = p.get("contraindication_tags") or []
        if pregnancy_status == "unknown" and "pregnancy_unknown" in tags:
            warnings.append(
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
            warnings.append(
                {
                    "code": "PREGNANCY_CONTRAINDICATED",
                    "severity": "BLOCKER",
                    "message": "Contraindicated in pregnancy; excluded from ranking.",
                    "related_product_sku": sku,
                }
            )
            continue

    ranked.sort(key=lambda t: t[0], reverse=True)
    top = ranked[:3]

    ranked_products: list[dict[str, Any]] = []
    for score, p, why in top:
        ranked_products.append(
            {
                "product_sku": p["sku"],
                "score_0_100": int(score),
                "why": why,
            }
        )

    # Ensure contract fragments are safe and valid.
    base_reco = {
        "schema_version": SCHEMA_VERSION,
        "ranked_products": [],
        "safety_warnings": [],
        "follow_up_questions": [],
        "confidence": 0.1,
    }

    safe_ranked: list[dict[str, Any]] = []
    for item in ranked_products:
        if not validate_or_return_errors(
            {**base_reco, "ranked_products": [item]}, "recommendation"
        ):
            safe_ranked.append(item)

    safe_warnings: list[dict[str, Any]] = []
    for w in warnings:
        if not validate_or_return_errors(
            {**base_reco, "safety_warnings": [w]}, "recommendation"
        ):
            safe_warnings.append(w)

    return safe_ranked, safe_warnings


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


def _pregnancy_status(llm_context: dict[str, Any], answers: dict[str, str]) -> str:
    # Start from llm_context if present (future), otherwise use follow-up answer.
    preg = llm_context.get("pregnancy_status") if isinstance(llm_context, dict) else None
    if isinstance(preg, str) and preg:
        return preg

    if _is_yes(answers.get("q_pregnancy")):
        return "pregnant"
    if answers.get("q_pregnancy") is not None:
        # Any explicit non-yes is treated as "not pregnant".
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

    haystack = " ".join(
        [
            str(product.get("name", "")),
            str(product.get("brand", "")),
            " ".join([str(x) for x in (product.get("ingredients") or [])]),
        ]
    ).lower()

    return any(t in haystack for t in allergy_terms)


def _infer_target_category(intake_extracted: dict[str, Any]) -> str:
    labels: list[str] = []
    for s in intake_extracted.get("symptoms") or []:
        if isinstance(s, dict) and isinstance(s.get("label"), str):
            labels.append(s["label"].lower())
    blob = " ".join(labels) + " " + str(intake_extracted.get("presenting_problem", "")).lower()

    if any(k in blob for k in ("sneez", "itchy eye", "allergic", "eternu")):
        return "allergy"
    if any(k in blob for k in ("bloat", "ballonn", "gas", "indigestion")):
        return "digestion"
    if any(k in blob for k in ("dry skin", "peau", "eczema", "itchy skin")):
        return "dermatology"
    return "general"


def _score_product(
    *,
    product: dict[str, Any],
    target_category: str,
    pregnancy_status: str,
) -> tuple[int, str]:
    category = str(product.get("category") or "").lower()
    ingredients = [str(x).lower() for x in (product.get("ingredients") or [])]
    tags = [str(x).lower() for x in (product.get("contraindication_tags") or [])]

    # 0–60: category match
    category_score = 0
    if target_category != "general":
        if category == target_category:
            category_score = 60
        elif target_category in category:
            category_score = 40
        else:
            category_score = 10
    else:
        category_score = 20

    # 0–30: ingredient match (very small starter lexicon)
    ingredient_score = 0
    kw = _ingredient_keywords(target_category)
    if any(k in " ".join(ingredients) for k in kw):
        ingredient_score = 30
    elif kw:
        ingredient_score = 10

    # 0–10: stock bonus (limited weight)
    stock_qty = product.get("stock_qty")
    stock_score = 0
    if isinstance(stock_qty, int) and stock_qty > 0:
        stock_score = min(10, int(round((min(stock_qty, 50) / 50) * 10)))

    score = max(0, min(100, category_score + ingredient_score + stock_score))

    why_parts = []
    why_parts.append(f"Category match: {target_category}")
    if ingredient_score >= 30:
        why_parts.append("Key ingredient match")
    if stock_score > 0:
        why_parts.append("In stock")

    if pregnancy_status == "unknown" and "pregnancy_unknown" in tags:
        why_parts.append("Confirm pregnancy status")
    if pregnancy_status == "pregnant" and "pregnancy_contraindicated" in tags:
        why_parts.append("Contraindicated in pregnancy")

    why = "; ".join(why_parts)[:2000]
    why = re.sub(r"\s+", " ", why).strip()

    return score, why


def _ingredient_keywords(target_category: str) -> list[str]:
    if target_category == "allergy":
        return ["cetirizine", "loratadine", "antihist"]
    if target_category == "digestion":
        return ["simethicone", "probiotic", "antacid"]
    if target_category == "dermatology":
        return ["glycerin", "urea", "emollient"]
    return []
