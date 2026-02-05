from __future__ import annotations

import json
import os
from typing import Any, Literal

from pharmassist_api.models.medgemma_client import medgemma_generate_text
from pharmassist_api.privacy.phi_boundary import scan_text

SCHEMA_VERSION = "0.0.0"

Language = Literal["fr", "en"]


def _enabled() -> bool:
    # Separate flag from A1/A7 so local dev can stay fallback-only by default.
    return os.getenv("PHARMASSIST_USE_MEDGEMMA_FOLLOWUP", "").strip() == "1"


def _parse_first_json_object(text: str) -> Any:
    """Best-effort JSON object extraction from a model output.

    We intentionally keep this small and dependency-free.
    """
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    end = None
    for i, ch in enumerate(text[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    if end is None:
        return None

    try:
        return json.loads(text[start:end])
    except Exception:
        return None


def maybe_select_followup_question_ids(
    *,
    intake_extracted: dict[str, Any],
    llm_context: dict[str, Any],
    candidate_ids: list[str],
    question_bank: dict[str, dict[str, Any]],
    language: Language,
    max_k: int = 5,
) -> tuple[list[str] | None, dict[str, Any]]:
    """Optional MedGemma-backed selection of follow-up question ids.

    Safety properties:
    - Closed allowlist: the model may only return ids from `candidate_ids`
    - No free-form question generation: we only select ids; rendering stays deterministic
    - Best-effort: failure falls back to rules
    """
    audit: dict[str, Any] = {
        "attempted": False,
        "mode": "rules",
        "max_k": max_k,
        "candidate_ids": list(candidate_ids),
        "selected_ids": [],
    }

    if not _enabled():
        return None, audit

    if not candidate_ids:
        return None, audit

    audit["attempted"] = True
    audit["mode"] = "medgemma"

    # Closed allowlist context.
    glossary_lines: list[str] = []
    for qid in candidate_ids:
        base = question_bank.get(qid) or {}
        ans_type = str(base.get("answer_type") or "")
        q_map = base.get("question") if isinstance(base.get("question"), dict) else {}
        q_fr = q_map.get("fr")
        q_en = q_map.get("en")
        if isinstance(q_fr, str) and isinstance(q_en, str):
            glossary_lines.append(f"- {qid} ({ans_type}): EN={q_en} | FR={q_fr}")
        elif isinstance(q_en, str):
            glossary_lines.append(f"- {qid} ({ans_type}): {q_en}")
        else:
            glossary_lines.append(f"- {qid} ({ans_type})")

    demo = llm_context.get("demographics") if isinstance(llm_context, dict) else None
    age = demo.get("age_years") if isinstance(demo, dict) else None
    sex = demo.get("sex") if isinstance(demo, dict) else None

    allergies = []
    for a in llm_context.get("allergies") or []:
        if isinstance(a, dict) and isinstance(a.get("substance"), str):
            allergies.append(a["substance"])

    conditions = []
    for c in llm_context.get("conditions") or []:
        if isinstance(c, dict) and isinstance(c.get("label"), str):
            conditions.append(c["label"])

    symptoms = []
    for s in intake_extracted.get("symptoms") or []:
        if isinstance(s, dict) and isinstance(s.get("label"), str):
            symptoms.append(s["label"])

    # IMPORTANT: keep prompts free of braces to avoid JSON-echo parsing ambiguity.
    user_content = (
        "Select the most relevant follow-up question ids from the allowlist below.\n"
        "Return ONLY a JSON object (no prose, no markdown) with keys:\n"
        "- schema_version: string (use \"0.0.0\")\n"
        "- question_ids: array of strings (ids from the allowlist)\n"
        f"Language: {language}\n"
        f"Max ids: {max_k}\n"
        "Do NOT invent new ids.\n"
        "\n"
        f"Symptoms: {', '.join(symptoms)}\n"
        f"Demographics: age_years={age} sex={sex}\n"
        f"Allergies: {', '.join(allergies)}\n"
        f"Conditions: {', '.join(conditions)}\n"
        "\n"
        "ALLOWLIST:\n"
        + "\n".join(glossary_lines)
        + "\n"
    )

    # Defense in depth: never send identifier-like content to any model.
    violations = scan_text(user_content, json_path="$.a3_followup_selector.user_content")
    blockers = [v for v in violations if v.severity == "BLOCKER"]
    if blockers:
        audit["mode"] = "fallback"
        return None, audit

    out = medgemma_generate_text(
        user_content=user_content,
        system=(
            "You are a clinical follow-up question selector.\n"
            "Output MUST be a single JSON object and nothing else."
        ),
        max_new_tokens=160,
    )
    if not isinstance(out, str) or not out.strip():
        audit["mode"] = "fallback"
        return None, audit

    parsed = _parse_first_json_object(out)
    if not isinstance(parsed, dict):
        audit["mode"] = "fallback"
        return None, audit

    ids = parsed.get("question_ids")
    if not isinstance(ids, list):
        audit["mode"] = "fallback"
        return None, audit

    allow = set(candidate_ids)
    selected: list[str] = []
    for item in ids:
        if not isinstance(item, str):
            continue
        qid = item.strip()
        if qid in allow and qid not in selected:
            selected.append(qid)
        if len(selected) >= max_k:
            break

    if not selected:
        audit["mode"] = "fallback"
        return None, audit

    audit["selected_ids"] = list(selected)
    return selected, audit
