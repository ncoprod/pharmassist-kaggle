from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any, Literal

from pharmassist_api.contracts.validate_schema import validate_or_return_errors
from pharmassist_api.models.medgemma_client import medgemma_generate_text

Language = Literal["fr", "en"]

_ALLOWED_KINDS = {
    "counseling_question",
    "safety_check",
    "otc_suggestion",
    "escalation",
    "evidence_review",
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _product_label(item: dict[str, Any]) -> str:
    sku = str(item.get("product_sku") or "").strip()
    name = str(item.get("product_name") or "").strip()
    if name and sku:
        return f"{name} ({sku})"
    return name or sku


def planner_feature_enabled() -> bool:
    return (os.getenv("PHARMASSIST_USE_AGENTIC_PLANNER") or "").strip() == "1"


def _fallback_plan(
    *,
    recommendation: dict[str, Any] | None,
    language: Language,
    fallback_reason: str,
) -> dict[str, Any]:
    recommendation = recommendation or {}
    steps: list[dict[str, Any]] = []

    for warning in recommendation.get("safety_warnings") or []:
        if not isinstance(warning, dict):
            continue
        message = str(warning.get("message") or "").strip()
        if not message:
            continue
        steps.append(
            {
                "step_id": f"safety-{len(steps) + 1}",
                "kind": "safety_check",
                "title": "Safety check" if language == "en" else "Verification securite",
                "detail": message,
                "evidence_refs": [],
            }
        )

    escalation = recommendation.get("escalation") if isinstance(recommendation, dict) else None
    if isinstance(escalation, dict) and escalation.get("recommended") is True:
        steps.append(
            {
                "step_id": f"escalation-{len(steps) + 1}",
                "kind": "escalation",
                "title": "Escalate" if language == "en" else "Escalade",
                "detail": str(escalation.get("reason") or "Escalation recommended"),
                "evidence_refs": [],
            }
        )

    for ranked in recommendation.get("ranked_products") or []:
        if not isinstance(ranked, dict):
            continue
        sku = str(ranked.get("product_sku") or "").strip()
        label = _product_label(ranked)
        why = str(ranked.get("why") or "").strip()
        if not sku:
            continue
        steps.append(
            {
                "step_id": f"otc-{len(steps) + 1}",
                "kind": "otc_suggestion",
                "title": f"OTC {label}",
                "detail": (
                    why
                    or (
                        "Check suitability before dispensing"
                        if language == "en"
                        else "Verifier avant delivrance"
                    )
                ),
                "evidence_refs": [
                    r
                    for r in (ranked.get("evidence_refs") or [])
                    if isinstance(r, str)
                ][:2],
            }
        )
        if len(steps) >= 5:
            break

    for q in recommendation.get("follow_up_questions") or []:
        if not isinstance(q, dict):
            continue
        text = str(q.get("question") or "").strip()
        if not text:
            continue
        steps.append(
            {
                "step_id": f"question-{len(steps) + 1}",
                "kind": "counseling_question",
                "title": "Ask patient" if language == "en" else "Question patient",
                "detail": text,
                "evidence_refs": [],
            }
        )
        if len(steps) >= 6:
            break

    if not steps:
        steps = [
            {
                "step_id": "fallback-1",
                "kind": "counseling_question",
                "title": "Clarify symptoms" if language == "en" else "Clarifier les symptomes",
                "detail": "Collect symptom chronology and blockers before OTC advice."
                if language == "en"
                else "Recueillir la chronologie des symptomes avant conseil OTC.",
                "evidence_refs": [],
            }
        ]

    return {
        "schema_version": "0.0.0",
        "planner_version": "feb14-v1",
        "generated_at": _now_iso(),
        "mode": "fallback_deterministic",
        "fallback_used": True,
        "trace_meta": {
            "plan_source": "fallback",
            "invalid_json_reason": fallback_reason[:180],
        },
        "safety_checks": [
            "No PHI in plan artifact.",
            "Closed allowlist for step kinds.",
            "No prescription-medication advice.",
        ],
        "steps": steps[:8],
    }


def _normalize_step(item: dict[str, Any], idx: int) -> dict[str, Any] | None:
    kind = str(item.get("kind") or "").strip()
    if kind not in _ALLOWED_KINDS:
        return None

    detail = str(item.get("detail") or "").strip()
    if not detail:
        return None

    title = str(item.get("title") or kind.replace("_", " ").title()).strip()
    refs = [r for r in (item.get("evidence_refs") or []) if isinstance(r, str)]

    return {
        "step_id": f"agentic-{idx + 1}",
        "kind": kind,
        "title": title[:120],
        "detail": detail[:600],
        "evidence_refs": refs[:4],
    }


def _coerce_candidate(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None

    allowed_top_level = {"safety_checks", "steps"}
    unknown_top_level = {k for k in payload if k not in allowed_top_level}
    if unknown_top_level:
        return None

    steps_raw = payload.get("steps")
    if not isinstance(steps_raw, list):
        return None

    steps: list[dict[str, Any]] = []
    for idx, item in enumerate(steps_raw):
        if not isinstance(item, dict):
            return None
        normalized = _normalize_step(item, idx)
        if normalized is None:
            return None
        steps.append(normalized)
        if len(steps) >= 8:
            break

    if not steps:
        return None

    checks = [c for c in (payload.get("safety_checks") or []) if isinstance(c, str)]

    candidate = {
        "schema_version": "0.0.0",
        "planner_version": "feb14-v1",
        "generated_at": _now_iso(),
        "mode": "agentic",
        "fallback_used": False,
        "trace_meta": {"plan_source": "llm_json"},
        "safety_checks": checks[:6]
        or [
            "No PHI in plan artifact.",
            "Closed allowlist for step kinds.",
        ],
        "steps": steps,
    }

    if validate_or_return_errors(candidate, "planner_plan"):
        return None
    return candidate


def _try_parse_json(raw: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return _coerce_candidate(payload)


def _build_prompt(*, recommendation: dict[str, Any] | None, language: Language) -> str:
    recommendation = recommendation or {}
    lines: list[str] = [
        "Return STRICT JSON only.",
        (
            "Allowed step kinds: counseling_question, safety_check, "
            "otc_suggestion, escalation, evidence_review."
        ),
        "Output object with keys: safety_checks (array[string]), steps (array[object]).",
        f"Language: {language}",
        "Context summary:",
    ]

    esc = recommendation.get("escalation") if isinstance(recommendation, dict) else None
    if isinstance(esc, dict) and esc.get("recommended") is True:
        lines.append(f"- escalation: {esc.get('reason')}")

    for w in recommendation.get("safety_warnings") or []:
        if isinstance(w, dict):
            lines.append(f"- warning: {w.get('severity')} {w.get('message')}")

    for p in recommendation.get("ranked_products") or []:
        if isinstance(p, dict):
            lines.append(
                f"- product: {_product_label(p)} "
                f"why={p.get('why')} refs={p.get('evidence_refs')}"
            )

    return "\n".join(lines)[:5000]


def build_planner_plan(
    *,
    recommendation: dict[str, Any] | None,
    language: Language,
) -> dict[str, Any]:
    if not planner_feature_enabled():
        raise RuntimeError("Agentic planner is disabled")

    raw_from_env = (os.getenv("PHARMASSIST_AGENTIC_PLANNER_RAW_JSON") or "").strip()
    if raw_from_env:
        parsed = _try_parse_json(raw_from_env)
        if parsed is not None:
            return parsed
        return _fallback_plan(
            recommendation=recommendation,
            language=language,
            fallback_reason="invalid_env_json",
        )

    prompt = _build_prompt(recommendation=recommendation, language=language)
    out = medgemma_generate_text(
        user_content=prompt,
        system=(
            "You are a strict pharmacy planner. "
            "Return JSON only, with no markdown fences or prose."
        ),
        max_new_tokens=700,
    )
    if isinstance(out, str) and out.strip():
        parsed = _try_parse_json(out.strip())
        if parsed is not None:
            return parsed
        return _fallback_plan(
            recommendation=recommendation,
            language=language,
            fallback_reason="invalid_llm_json",
        )

    return _fallback_plan(
        recommendation=recommendation,
        language=language,
        fallback_reason="empty_llm_output",
    )
