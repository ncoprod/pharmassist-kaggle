from __future__ import annotations

from typing import Any

from pharmassist_api.steps.question_bank import load_question_bank


def _normalize_yes_no(answer: str) -> str | None:
    t = answer.strip().lower()
    if t in {"yes", "y", "oui", "o", "true", "1"}:
        return "yes"
    if t in {"no", "n", "non", "false", "0"}:
        return "no"
    return None


def validate_and_canonicalize_follow_up_answers(
    follow_up_answers: list[dict[str, Any]],
) -> tuple[list[dict[str, str]] | None, list[dict[str, Any]]]:
    """Validate follow-up answers against the closed question bank.

    Returns:
    - (canonical_answers, []) on success
    - (None, issues) on validation errors
    """
    bank = load_question_bank()
    canonical: list[dict[str, str]] = []
    issues: list[dict[str, Any]] = []

    for idx, item in enumerate(follow_up_answers):
        if not isinstance(item, dict):
            issues.append(
                {
                    "code": "INVALID_ITEM",
                    "json_path": f"$.follow_up_answers[{idx}]",
                    "message": "Answer item must be an object.",
                }
            )
            continue

        qid = item.get("question_id")
        ans = item.get("answer")
        if not isinstance(qid, str) or not qid.strip():
            issues.append(
                {
                    "code": "MISSING_QUESTION_ID",
                    "json_path": f"$.follow_up_answers[{idx}].question_id",
                    "message": "question_id must be a non-empty string.",
                }
            )
            continue
        if not isinstance(ans, str) or not ans.strip():
            issues.append(
                {
                    "code": "MISSING_ANSWER",
                    "json_path": f"$.follow_up_answers[{idx}].answer",
                    "message": "answer must be a non-empty string.",
                }
            )
            continue

        qid = qid.strip()
        ans = ans.strip()

        q = bank.get(qid)
        if not isinstance(q, dict):
            issues.append(
                {
                    "code": "UNKNOWN_QUESTION_ID",
                    "json_path": f"$.follow_up_answers[{idx}].question_id",
                    "message": f"Unknown question_id: {qid}",
                }
            )
            continue

        ans_type = q.get("answer_type")
        if ans_type == "yes_no":
            normalized = _normalize_yes_no(ans)
            if normalized is None:
                issues.append(
                    {
                        "code": "INVALID_YES_NO",
                        "json_path": f"$.follow_up_answers[{idx}].answer",
                        "message": "Expected yes/no answer (e.g. yes/no, oui/non).",
                    }
                )
                continue
            canonical.append({"question_id": qid, "answer": normalized})
            continue

        if ans_type == "choice":
            choices = q.get("choices")
            if not isinstance(choices, list) or not all(isinstance(c, str) for c in choices):
                issues.append(
                    {
                        "code": "INVALID_QUESTION_CONFIG",
                        "json_path": f"$.follow_up_answers[{idx}].question_id",
                        "message": f"Question {qid} has invalid choices configuration.",
                    }
                )
                continue
            if ans not in set(choices):
                issues.append(
                    {
                        "code": "INVALID_CHOICE",
                        "json_path": f"$.follow_up_answers[{idx}].answer",
                        "message": f"Answer must be one of: {', '.join(choices)}",
                    }
                )
                continue
            canonical.append({"question_id": qid, "answer": ans})
            continue

        if ans_type == "number":
            t = ans.replace(",", ".")
            try:
                value = float(t)
            except ValueError:
                issues.append(
                    {
                        "code": "INVALID_NUMBER",
                        "json_path": f"$.follow_up_answers[{idx}].answer",
                        "message": "Expected a numeric answer.",
                    }
                )
                continue
            if qid == "q_temperature" and not (30.0 <= value <= 45.0):
                issues.append(
                    {
                        "code": "NUMBER_OUT_OF_RANGE",
                        "json_path": f"$.follow_up_answers[{idx}].answer",
                        "message": "Temperature must be between 30 and 45 °C.",
                    }
                )
                continue
            if qid == "q_duration" and not (0.0 <= value <= 3650.0):
                issues.append(
                    {
                        "code": "NUMBER_OUT_OF_RANGE",
                        "json_path": f"$.follow_up_answers[{idx}].answer",
                        "message": "Duration must be between 0 and 3650 days.",
                    }
                )
                continue
            canonical.append({"question_id": qid, "answer": ans})
            continue

        issues.append(
            {
                "code": "UNSUPPORTED_ANSWER_TYPE",
                "json_path": f"$.follow_up_answers[{idx}].question_id",
                "message": f"Unsupported answer_type for question {qid}: {ans_type}",
            }
        )

    if issues:
        return None, issues

    return canonical, []

