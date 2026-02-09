from __future__ import annotations

from typing import Any, Literal

from pharmassist_api.contracts.validate_schema import validate_or_return_errors

Language = Literal["fr", "en"]


def compose_prebrief(
    *,
    recommendation: dict[str, Any] | None,
    trace_events: list[dict[str, Any]] | None,
    language: Language,
    visit_ref: str | None = None,
) -> dict[str, Any]:
    recommendation = recommendation or {}
    trace_events = trace_events or []

    top_actions: list[str] = []
    top_risks: list[str] = []
    top_questions: list[str] = []
    what_changed: list[str] = []
    new_rx_delta: list[str] = []

    escalation = recommendation.get("escalation") if isinstance(recommendation, dict) else None
    if isinstance(escalation, dict) and escalation.get("recommended") is True:
        svc = str(escalation.get("suggested_service") or "Medical review")
        reason = str(escalation.get("reason") or "")
        if language == "fr":
            top_actions.append(f"Escalade recommandee: {svc}")
        else:
            top_actions.append(f"Escalation recommended: {svc}")
        if reason:
            top_risks.append(reason)

    for ranked in recommendation.get("ranked_products") or []:
        if not isinstance(ranked, dict):
            continue
        sku = str(ranked.get("product_sku") or "").strip()
        why = str(ranked.get("why") or "").strip()
        refs = [r for r in (ranked.get("evidence_refs") or []) if isinstance(r, str)]
        if sku:
            label = f"{sku}: {why}".strip()
            top_actions.append(label)
            if refs:
                new_rx_delta.append(f"{sku} evidence: {', '.join(refs[:2])}")

    for warning in recommendation.get("safety_warnings") or []:
        if not isinstance(warning, dict):
            continue
        msg = str(warning.get("message") or "").strip()
        sev = str(warning.get("severity") or "WARN").strip() or "WARN"
        if msg:
            top_risks.append(f"{sev}: {msg}")
            if sev == "BLOCKER":
                new_rx_delta.append(f"Blocker: {msg}")

    for q in recommendation.get("follow_up_questions") or []:
        if not isinstance(q, dict):
            continue
        text = str(q.get("question") or "").strip()
        if text:
            top_questions.append(text)

    if visit_ref:
        if language == "fr":
            what_changed.append(f"Nouvelle visite analysee: {visit_ref}")
        else:
            what_changed.append(f"New visit analyzed: {visit_ref}")

    for ev in trace_events:
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "tool_result" and isinstance(ev.get("result_summary"), str):
            what_changed.append(str(ev["result_summary"]))
        elif ev.get("type") == "policy_violation" and isinstance(ev.get("message"), str):
            top_risks.append(str(ev["message"]))

    def _top_unique(items: list[str], *, max_items: int = 3) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for item in items:
            text = item.strip()
            if not text:
                continue
            if text in seen:
                continue
            seen.add(text)
            out.append(text)
            if len(out) >= max_items:
                break
        return out

    payload = {
        "schema_version": "0.0.0",
        "top_actions": _top_unique(top_actions),
        "top_risks": _top_unique(top_risks),
        "top_questions": _top_unique(top_questions),
        "what_changed": _top_unique(what_changed),
        "new_rx_delta": _top_unique(new_rx_delta),
    }

    if not payload["top_actions"]:
        payload["top_actions"] = [
            "Confirmer l'evolution des symptomes avec le patient."
            if language == "fr"
            else "Confirm symptom evolution with the patient."
        ]
    if not payload["top_risks"]:
        payload["top_risks"] = [
            "Aucun risque majeur detecte."
            if language == "fr"
            else "No major risk detected."
        ]
    if not payload["top_questions"]:
        payload["top_questions"] = [
            "Depuis quand les symptomes ont-ils commence?"
            if language == "fr"
            else "When did the symptoms start?"
        ]
    if not payload["what_changed"]:
        payload["what_changed"] = [
            "Aucun changement notable depuis la derniere analyse."
            if language == "fr"
            else "No notable change since last analysis."
        ]
    if not payload["new_rx_delta"]:
        payload["new_rx_delta"] = [
            "Aucun delta Rx critique."
            if language == "fr"
            else "No critical Rx delta."
        ]

    if validate_or_return_errors(payload, "prebrief"):
        # Hard fallback to schema-safe deterministic payload.
        return {
            "schema_version": "0.0.0",
            "top_actions": payload["top_actions"][:1],
            "top_risks": payload["top_risks"][:1],
            "top_questions": payload["top_questions"][:1],
            "what_changed": payload["what_changed"][:1],
            "new_rx_delta": payload["new_rx_delta"][:1],
        }

    return payload
