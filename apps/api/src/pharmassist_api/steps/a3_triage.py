from __future__ import annotations

import re
import unicodedata
from typing import Any, Literal

from pharmassist_api.contracts.validate_schema import validate_or_return_errors
from pharmassist_api.steps.a3_followup_selector import maybe_select_followup_question_ids
from pharmassist_api.steps.question_bank import load_question_bank

SCHEMA_VERSION = "0.0.0"

Language = Literal["fr", "en"]

TriageMeta = dict[str, Any]


def triage_and_followup(
    *,
    intake_extracted: dict[str, Any],
    llm_context: dict[str, Any],
    follow_up_answers: list[dict[str, Any]] | None,
    language: Language,
) -> tuple[dict[str, Any], dict[str, Any], bool, TriageMeta]:
    """Rules-first triage and follow-up funnel (Day 5).

    Returns:
      - updated intake_extracted (red_flags filled)
      - a schema-valid recommendation (ranked_products empty for Day 5)
      - needs_more_info flag (when follow-up questions are required but unanswered)
      - meta (safe trace metadata, e.g. selector mode + ids; no OCR text)
    """
    answers = _answers_to_map(follow_up_answers)

    text_blob = _norm(intake_extracted.get("presenting_problem", ""))
    for s in intake_extracted.get("symptoms") or []:
        if isinstance(s, dict):
            text_blob += " " + _norm(s.get("label", ""))

    # Red flags can come from extracted text OR explicit follow-up answers.
    red_flags = _detect_red_flags(text_blob, answers)
    if _is_low_info(intake_extracted):
        overall_sev = _norm(answers.get("q_overall_severity") or "")
        if overall_sev == "severe":
            red_flags.add("RF_SEVERE_SYMPTOMS")
    intake_extracted = dict(intake_extracted)
    intake_extracted["red_flags"] = sorted(red_flags)

    escalation = _escalation_for(red_flags, language)
    follow_up_questions: list[dict[str, Any]] = []
    selector_meta: dict[str, Any] = {
        "attempted": False,
        "mode": "rules",
        "max_k": 5,
        "candidate_ids": [],
        "selected_ids": [],
    }

    # If there is any red flag, we escalate immediately and avoid asking more questions.
    if not red_flags:
        follow_up_questions, needs_more_info, selector_meta = _generate_follow_up_questions(
            intake_extracted=intake_extracted,
            llm_context=llm_context,
            answers=answers,
            language=language,
        )
    else:
        needs_more_info = False

    confidence = 0.2
    if _is_low_info(intake_extracted):
        confidence = 0.1
    elif not red_flags and not needs_more_info:
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

    return intake_extracted, recommendation, needs_more_info, {"followup_selector": selector_meta}


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
            label_norm = _norm(s["label"])
            # OCR can introduce spacing / leetspeak noise (e.g. "unspec  ified").
            label_compact = label_norm.replace(" ", "")
            label_deleet_compact = _deleet(label_norm).replace(" ", "")
            if (
                "unspecified" in label_compact
                or "unspecified" in label_deleet_compact
                or "nonspecifie" in label_compact
                or "nonspecifie" in label_deleet_compact
            ):
                return True
    return False


def _generate_follow_up_questions(
    *,
    intake_extracted: dict[str, Any],
    llm_context: dict[str, Any],
    answers: dict[str, str],
    language: Language,
) -> tuple[list[dict[str, Any]], bool, dict[str, Any]]:
    required_ids: set[str] = set()
    optional_ids: set[str] = set()
    bank = load_question_bank()

    # Low-info cases need a minimal funnel.
    if _is_low_info(intake_extracted):
        # Required safety + routing questions (no free text).
        required_ids |= {
            "q_primary_domain",
            "q_breathing",
            "q_chest_pain",
            "q_fever",
            "q_overall_severity",
        }

        # If the domain is known (from follow-up answers), we can surface
        # additional domain-specific questions as optional candidates.
        primary_domain = answers.get("q_primary_domain")
        if isinstance(primary_domain, str) and primary_domain:
            domain_optional: dict[str, set[str]] = {
                "allergy_ent": {
                    "q_allergy_severity",
                    "q_allergy_known_trigger",
                    "q_allergy_eye_symptoms",
                    "q_allergy_nasal_congestion",
                    "q_allergy_wheezing_or_asthma",
                },
                "digestive": {
                    "q_gi_main_symptom",
                    "q_gi_vomiting",
                    "q_gi_abdominal_pain_severe",
                    "q_gi_blood_in_stool",
                    "q_gi_dehydration_signs",
                },
                "skin": {
                    "q_skin_main_problem",
                    "q_skin_spreading_fast",
                    "q_skin_mucosa_blisters",
                    "q_skin_pus_or_warmth",
                    "q_skin_new_drug_recent",
                },
                "pain": {
                    "q_pain_location",
                    "q_pain_after_trauma",
                    "q_headache_sudden_worst",
                    "q_headache_neuro_symptoms",
                },
                "eye": {
                    "q_eye_main_problem",
                    "q_eye_vision_change",
                    "q_eye_severe_pain",
                    "q_eye_contact_lenses",
                    "q_eye_trauma",
                },
                "urology": {
                    "q_uro_pain_urination",
                    "q_uro_frequency_urgency",
                    "q_uro_flank_pain",
                    "q_uro_blood_in_urine",
                },
                "respiratory": {
                    "q_resp_cough",
                    "q_resp_wheezing",
                    "q_resp_cyanosis",
                    "q_resp_sore_throat",
                    "q_resp_sputum_color",
                    "q_resp_asthma_copd",
                },
                "other": set(),
            }
            optional_ids |= domain_optional.get(primary_domain, set())
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
            # Follow-up is optional here: the default path should proceed.
            optional_ids |= {"q_fever", "q_breathing", "q_allergy_severity"}

    # Ask duration if we don't have duration_days anywhere (except for low-info
    # funnels where routing + safety screens matter more than timeline).
    if not _is_low_info(intake_extracted):
        has_duration = any(
            isinstance(s, dict) and isinstance(s.get("duration_days"), int)
            for s in (intake_extracted.get("symptoms") or [])
        )
        if not has_duration:
            required_ids.add("q_duration")

    # If fever is present, we need the max temperature to assess high fever (>= 39C).
    if _is_yes(answers.get("q_fever")) and "q_temperature" not in answers:
        required_ids.add("q_temperature")

    # Remove answered questions.
    required_ids = {qid for qid in required_ids if qid not in answers}
    optional_ids = {qid for qid in optional_ids if qid not in answers}

    max_k = 5
    needs_more_info = bool(required_ids)

    # Candidate ids for the selector (closed allowlist). This may include optional candidates.
    candidate_ids = sorted(
        (required_ids | optional_ids),
        key=lambda k: int((bank.get(k) or {}).get("priority", 9)),
    )

    # Optional MedGemma selector: choose a subset (closed allowlist).
    selected_ids: list[str] | None = None
    selector_meta: dict[str, Any] = {"attempted": False, "mode": "rules", "max_k": max_k}
    if optional_ids and len(required_ids) < max_k:
        selected_ids, selector_meta = maybe_select_followup_question_ids(
            intake_extracted=intake_extracted,
            llm_context=llm_context,
            candidate_ids=candidate_ids,
            question_bank=bank,
            language=language,
            max_k=max_k,
        )

    # Safety property: required questions are never dropped by the model.
    required_sorted = sorted(
        required_ids,
        key=lambda k: int((bank.get(k) or {}).get("priority", 9)),
    )
    selected_sorted = sorted(
        [qid for qid in (selected_ids or []) if qid not in required_ids],
        key=lambda k: int((bank.get(k) or {}).get("priority", 9)),
    )
    ids_to_render: list[str] = []
    for qid in required_sorted:
        if len(ids_to_render) >= max_k:
            break
        ids_to_render.append(qid)
    for qid in selected_sorted:
        if len(ids_to_render) >= max_k:
            break
        ids_to_render.append(qid)

    items: list[dict[str, Any]] = []
    for qid in ids_to_render:
        base = bank.get(qid)
        if not base:
            continue
        q_map = base.get("question") if isinstance(base.get("question"), dict) else {}
        r_map = base.get("reason") if isinstance(base.get("reason"), dict) else {}
        question = q_map.get(language)
        reason = r_map.get(language)
        if not isinstance(question, str):
            continue

        payload: dict[str, Any] = {
            "question_id": qid,
            "question": question,
            "answer_type": base.get("answer_type"),
            "priority": base.get("priority"),
        }
        if isinstance(reason, str) and reason:
            payload["reason"] = reason
        if base.get("answer_type") == "choice" and isinstance(base.get("choices"), list):
            payload["choices"] = base.get("choices")
        items.append(
            payload
        )

    selector_meta = {
        **(selector_meta or {}),
        "candidate_ids": candidate_ids,
        "selected_ids": list(selected_ids or []),
    }
    return items, needs_more_info, selector_meta


def _detect_red_flags(text_blob_norm: str, answers: dict[str, str]) -> set[str]:
    rf: set[str] = set()

    def has_any(*subs: str) -> bool:
        # OCR can introduce leetspeak digits (e.g. dy5pnea). We scan both the
        # normalized blob and a "de-leeted" variant, with/without spaces.
        blob = text_blob_norm
        blob_deleet = _deleet(text_blob_norm)
        blob_compact = blob.replace(" ", "")
        blob_deleet_compact = blob_deleet.replace(" ", "")
        for s in subs:
            if (
                s in blob
                or s in blob_deleet
                or s in blob_compact
                or s in blob_deleet_compact
            ):
                return True
        return False

    # From text (best-effort, since OCR may be noisy).
    if has_any(
        "shortness of breath",
        "difficulty breathing",
        "breathing difficulty",
        "dyspnee",
        "dyspnea",
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
    if _is_yes(answers.get("q_severe_allergic_reaction")):
        rf.add("RF_ANAPHYLAXIS")
    if _is_yes(answers.get("q_confusion_or_fainting")):
        rf.add("RF_NEURO")
    if _is_yes(answers.get("q_resp_cyanosis")):
        rf.add("RF_BREATHING_DIFFICULTY")
    if _is_yes(answers.get("q_gi_blood_in_stool")) or _is_yes(answers.get("q_gi_black_stool")):
        rf.add("RF_BLOOD")
    if _is_yes(answers.get("q_gi_vomiting_blood")):
        rf.add("RF_BLOOD")
    if _is_yes(answers.get("q_headache_sudden_worst")) or _is_yes(
        answers.get("q_headache_neuro_symptoms")
    ):
        rf.add("RF_NEURO")
    if _is_yes(answers.get("q_eye_vision_change")):
        rf.add("RF_EYE_VISION_CHANGE")
    if _is_yes(answers.get("q_uro_blood_in_urine")):
        rf.add("RF_BLOOD")

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


def _deleet(text: str) -> str:
    # Common OCR/leetspeak substitutions (helps match substrings like "dy5pnea" -> "dyspnea").
    return text.translate(
        str.maketrans(
            {
                "0": "o",
                "1": "i",
                "2": "z",
                "3": "e",
                "4": "a",
                "5": "s",
                "6": "g",
                "7": "t",
                "8": "b",
                "9": "g",
            }
        )
    )
