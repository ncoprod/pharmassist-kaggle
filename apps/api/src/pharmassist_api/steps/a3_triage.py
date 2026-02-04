from __future__ import annotations

import re
import unicodedata
from typing import Any, Literal

from pharmassist_api.contracts.validate_schema import validate_or_return_errors

SCHEMA_VERSION = "0.0.0"

Language = Literal["fr", "en"]


# Stable follow-up questions (used to associate answers across reruns).
_QUESTION_BANK: dict[str, dict[str, Any]] = {
    "q_duration": {
        "priority": 1,
        "answer_type": "number",
        "question": {"fr": "Depuis combien de jours ?", "en": "How many days?"},
        "reason": {
            "fr": "La duree aide a differencier une situation benigne d'un probleme a evaluer.",
            "en": "Duration helps distinguish self-limited issues from those needing evaluation.",
        },
    },
    "q_fever": {
        "priority": 2,
        "answer_type": "yes_no",
        "question": {"fr": "Avez-vous de la fievre ?", "en": "Do you have fever?"},
        "reason": {
            "fr": "La fievre peut orienter vers une infection ou une evaluation medicale.",
            "en": "Fever may indicate infection or the need for medical evaluation.",
        },
    },
    "q_temperature": {
        "priority": 2,
        "answer_type": "number",
        "question": {
            "fr": "Temperature maximale (°C) ?",
            "en": "Max temperature (°C)?",
        },
        "reason": {
            "fr": "Une temperature elevee (>= 39°C) est un signe d'alerte.",
            "en": "High temperature (>= 39°C) is a red flag.",
        },
    },
    "q_breathing": {
        "priority": 1,
        "answer_type": "yes_no",
        "question": {
            "fr": "Avez-vous une gene respiratoire ?",
            "en": "Any breathing difficulty?",
        },
        "reason": {
            "fr": "Une gene respiratoire est un signe d'alerte.",
            "en": "Breathing difficulty is a red flag.",
        },
    },
    "q_chest_pain": {
        "priority": 1,
        "answer_type": "yes_no",
        "question": {"fr": "Douleur thoracique ?", "en": "Chest pain?"},
        "reason": {
            "fr": "La douleur thoracique est un signe d'alerte.",
            "en": "Chest pain is a red flag.",
        },
    },
    "q_pregnancy": {
        "priority": 3,
        "answer_type": "yes_no",
        "question": {"fr": "Etes-vous enceinte ?", "en": "Are you pregnant?"},
        "reason": {
            "fr": "Certains produits necessitent des precautions en cas de grossesse.",
            "en": "Some products require caution in pregnancy.",
        },
    },
}


def triage_and_followup(
    *,
    intake_extracted: dict[str, Any],
    llm_context: dict[str, Any],
    follow_up_answers: list[dict[str, Any]] | None,
    language: Language,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Rules-first triage and follow-up funnel (Day 5).

    Returns:
      - updated intake_extracted (red_flags filled)
      - a schema-valid recommendation (ranked_products empty for Day 5)
      - needs_more_info flag (when follow-up questions are required but unanswered)
    """
    answers = _answers_to_map(follow_up_answers)

    text_blob = _norm(intake_extracted.get("presenting_problem", ""))
    for s in intake_extracted.get("symptoms") or []:
        if isinstance(s, dict):
            text_blob += " " + _norm(s.get("label", ""))

    # Red flags can come from extracted text OR explicit follow-up answers.
    red_flags = _detect_red_flags(text_blob, answers)
    intake_extracted = dict(intake_extracted)
    intake_extracted["red_flags"] = sorted(red_flags)

    escalation = _escalation_for(red_flags, language)
    follow_up_questions: list[dict[str, Any]] = []

    # If there is any red flag, we escalate immediately and avoid asking more questions.
    if not red_flags:
        follow_up_questions = _generate_follow_up_questions(
            intake_extracted=intake_extracted,
            llm_context=llm_context,
            answers=answers,
            language=language,
        )

    needs_more_info = bool(follow_up_questions)

    confidence = 0.2
    if _is_low_info(intake_extracted):
        confidence = 0.1
    elif not red_flags and not follow_up_questions:
        confidence = 0.5

    recommendation: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "ranked_products": [],  # Day 5: ranking not implemented yet.
        "safety_warnings": [],
        "follow_up_questions": follow_up_questions,
        "confidence": confidence,
    }
    if escalation is not None:
        recommendation["escalation"] = escalation
        recommendation["safety_warnings"].append(
            {
                "code": "ESCALATION_RECOMMENDED",
                "message": escalation["reason"],
                "severity": "WARN",
            }
        )

    # Safety net: never return invalid objects.
    if validate_or_return_errors(intake_extracted, "intake_extracted"):
        intake_extracted = {
            "schema_version": SCHEMA_VERSION,
            "presenting_problem": "unspecified",
            "symptoms": [{"label": "unspecified symptom", "severity": "unknown"}],
            "red_flags": [],
        }
    if validate_or_return_errors(recommendation, "recommendation"):
        recommendation = {
            "schema_version": SCHEMA_VERSION,
            "ranked_products": [],
            "safety_warnings": [],
            "follow_up_questions": [],
            "confidence": 0.1,
        }
        needs_more_info = False

    return intake_extracted, recommendation, needs_more_info


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


def _is_low_info(intake_extracted: dict[str, Any]) -> bool:
    syms = intake_extracted.get("symptoms") or []
    if not isinstance(syms, list) or not syms:
        return True
    for s in syms:
        if isinstance(s, dict) and isinstance(s.get("label"), str):
            if "unspecified" in s["label"].lower():
                return True
    return False


def _generate_follow_up_questions(
    *,
    intake_extracted: dict[str, Any],
    llm_context: dict[str, Any],
    answers: dict[str, str],
    language: Language,
) -> list[dict[str, Any]]:
    needed_ids: set[str] = set()

    # Low-info cases need a minimal funnel.
    if _is_low_info(intake_extracted):
        needed_ids |= {"q_duration", "q_fever", "q_breathing"}
    else:
        # Allergy-like symptoms: rule out fever/breathing issues.
        labels = [
            _norm((s or {}).get("label", ""))
            for s in (intake_extracted.get("symptoms") or [])
            if isinstance(s, dict)
        ]
        compact = " ".join(labels)
        if (
            ("sneez" in compact)
            or ("itchy" in compact and "eye" in compact)
            or ("eternu" in compact)
        ):
            needed_ids |= {"q_fever", "q_breathing"}

    # Ask duration if we don't have duration_days anywhere.
    has_duration = any(
        isinstance(s, dict) and isinstance(s.get("duration_days"), int)
        for s in (intake_extracted.get("symptoms") or [])
    )
    if not has_duration:
        needed_ids.add("q_duration")

    # Pregnancy question if unknown and sex is F.
    demo = llm_context.get("demographics") if isinstance(llm_context, dict) else None
    sex = demo.get("sex") if isinstance(demo, dict) else None
    preg = llm_context.get("pregnancy_status") if isinstance(llm_context, dict) else None
    if sex == "F" and preg is None:
        needed_ids.add("q_pregnancy")

    # If fever is present, we need the max temperature to assess high fever (>= 39C).
    if _is_yes(answers.get("q_fever")) and "q_temperature" not in answers:
        needed_ids.add("q_temperature")

    # Remove answered questions.
    needed_ids = {qid for qid in needed_ids if qid not in answers}

    # Materialize in stable priority order.
    items: list[dict[str, Any]] = []
    for qid in sorted(
        needed_ids,
        key=lambda k: int(_QUESTION_BANK.get(k, {}).get("priority", 9)),
    ):
        base = _QUESTION_BANK.get(qid)
        if not base:
            continue
        items.append(
            {
                "question_id": qid,
                "question": base["question"][language],
                "answer_type": base["answer_type"],
                "reason": base["reason"][language],
                "priority": base["priority"],
            }
        )
    return items


def _detect_red_flags(text_blob_norm: str, answers: dict[str, str]) -> set[str]:
    rf: set[str] = set()

    def has_any(*subs: str) -> bool:
        return any(s in text_blob_norm for s in subs)

    # From text (best-effort, since OCR may be noisy).
    if has_any(
        "shortness of breath",
        "difficulty breathing",
        "breathing difficulty",
        "dyspnee",
        "dyspne",
        "gene respiratoire",
        "essouff",
    ):
        rf.add("RF_BREATHING_DIFFICULTY")
    if has_any("chest pain", "douleur thorac", "oppression thorac"):
        rf.add("RF_CHEST_PAIN")
    if has_any(
        "angioedema",
        "angioedeme",
        "oedeme de quincke",
        "swelling lips",
        "swelling face",
        "lip swelling",
        "face swelling",
        "tongue swelling",
    ):
        rf.add("RF_ANAPHYLAXIS")
    if has_any("confusion", "seizure", "convulsion", "weakness one side", "paralys", "faiblesse"):
        rf.add("RF_NEURO")
    if has_any(
        "blood in stool",
        "blood in vomit",
        "vomiting blood",
        "sang dans les selles",
        "sang dans les vomis",
        "sang dans vom",
        "hematemesis",
        "melena",
    ):
        rf.add("RF_BLOOD")

    # From answers (more reliable).
    if _is_yes(answers.get("q_breathing")):
        rf.add("RF_BREATHING_DIFFICULTY")
    if _is_yes(answers.get("q_chest_pain")):
        rf.add("RF_CHEST_PAIN")

    # Fever >= 39C is a red flag if a temperature was provided.
    temp_c = _parse_temperature_c(answers.get("q_temperature"))
    if temp_c is not None and temp_c >= 39.0:
        rf.add("RF_HIGH_FEVER")

    return rf


def _parse_temperature_c(text: str | None) -> float | None:
    if not text:
        return None
    t = text.strip().replace(",", ".")
    try:
        value = float(t)
    except ValueError:
        return None
    # Sanity bounds for human body temperature.
    if 30.0 <= value <= 45.0:
        return value
    return None


def _is_yes(text: str | None) -> bool:
    if not text:
        return False
    t = _norm(text)
    return t in {"yes", "y", "oui", "true", "1"} or t.startswith("yes ") or t.startswith("oui ")


def _escalation_for(red_flags: set[str], language: Language) -> dict[str, Any] | None:
    if not red_flags:
        return None

    # Highest urgency: anaphylaxis OR breathing+dchest pain.
    urgent = ("RF_ANAPHYLAXIS" in red_flags) or (
        "RF_BREATHING_DIFFICULTY" in red_flags and "RF_CHEST_PAIN" in red_flags
    )
    if urgent:
        return {
            "recommended": True,
            "reason": (
                "Signes d'alerte: appelez le SAMU / Urgences (15/112)."
                if language == "fr"
                else "Red flags detected: call emergency services (112/911 equivalent)."
            ),
            "suggested_service": (
                "SAMU / Urgences (15/112)" if language == "fr" else "Emergency services"
            ),
        }

    return {
        "recommended": True,
        "reason": (
            "Signes d'alerte: consultez un medecin sous 24h (ou urgences si aggravation)."
            if language == "fr"
            else "Red flags detected: see a doctor within 24h (ER if worsening)."
        ),
        "suggested_service": "Medecin (24h) / Urgences si aggravation"
        if language == "fr"
        else "Doctor (24h) / ER if worsening",
    }


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[^a-z0-9\\s:/().,-]+", " ", text)
    text = re.sub(r"\\s+", " ", text).strip()
    return text
