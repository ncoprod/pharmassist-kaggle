from __future__ import annotations

import os
from typing import Any, Literal

from pharmassist_api.models.medgemma_client import medgemma_generate_text
from pharmassist_api.privacy.phi_boundary import scan_text
from pharmassist_api.validators.citations import lint_citations
from pharmassist_api.validators.rx_advice_lint import lint_rx_advice

SCHEMA_VERSION = "0.0.0"

Language = Literal["fr", "en"]


def _safe_text(value: Any) -> str:
    text = str(value or "")
    # Keep markdown renderers safe by neutralizing raw HTML/script tags.
    return text.replace("<", "‹").replace(">", "›").strip()


def _product_label(item: dict[str, Any]) -> str:
    sku = _safe_text(item.get("product_sku"))
    name = _safe_text(item.get("product_name"))
    if name and sku:
        return f"{name} ({sku})"
    return name or sku


def compose_report_markdown(
    *,
    intake_extracted: dict[str, Any],
    recommendation: dict[str, Any] | None,
    evidence_items: list[dict[str, Any]] | None,
    language: Language,
) -> str:
    """Compose pharmacist-facing report markdown (Day 8).

    Uses a deterministic template by default. Optional MedGemma path is gated
    behind `PHARMASSIST_USE_MEDGEMMA_REPORT=1` and always validated with:
      - PHI scan (hard boundary)
      - Rx advice lint (hard boundary)
      - Citation lint (hard boundary)
    """
    evidence_items = evidence_items or []
    evidence_ids = {
        str(e.get("evidence_id"))
        for e in evidence_items
        if isinstance(e, dict) and isinstance(e.get("evidence_id"), str)
    }

    # Optional MedGemma path (best-effort). Never required for CI.
    if os.getenv("PHARMASSIST_USE_MEDGEMMA_REPORT", "").strip() == "1":
        prompt = _build_report_prompt(
            intake_extracted=intake_extracted,
            recommendation=recommendation or {},
            evidence_items=evidence_items,
            language=language,
        )
        # Hard PHI boundary: never send identifier-like content to any model.
        if any(v.severity == "BLOCKER" for v in scan_text(prompt, "$.a7_report_composer.prompt")):
            prompt = ""

        if prompt:
            out = medgemma_generate_text(
                user_content=prompt,
                system=(
                    "You are a pharmacist-facing report writer.\n"
                    "Return markdown only.\n"
                    "OTC/parapharmacy decision support only.\n"
                    "Do NOT provide prescription-medication advice.\n"
                    "Citations MUST use the provided evidence ids in brackets, "
                    "e.g. [ev_allergy_001]."
                ),
                max_new_tokens=700,
            )
            if isinstance(out, str) and out.strip():
                md = out.strip()
                if _is_safe_markdown(md, evidence_ids=evidence_ids, path="$.report_markdown"):
                    return md

    # Deterministic fallback template.
    return _render_report_template(
        intake_extracted=intake_extracted,
        recommendation=recommendation or {},
        evidence_items=evidence_items,
        language=language,
    )


def _is_safe_markdown(markdown: str, *, evidence_ids: set[str], path: str) -> bool:
    # 1) PHI scan (BLOCKER only).
    blockers = [v for v in scan_text(markdown, path) if v.severity == "BLOCKER"]
    if blockers:
        return False

    # 2) Rx advice lint.
    if any(v.severity == "BLOCKER" for v in lint_rx_advice(markdown, path=path)):
        return False

    # 3) Citation lint.
    if any(
        v.severity == "BLOCKER"
        for v in lint_citations(markdown, evidence_ids=evidence_ids, path=path)
    ):
        return False

    return True


def _build_report_prompt(
    *,
    intake_extracted: dict[str, Any],
    recommendation: dict[str, Any],
    evidence_items: list[dict[str, Any]],
    language: Language,
) -> str:
    # Keep the prompt compact to reduce cost/latency; never include PHI.
    lines: list[str] = []
    lines.append(f"Language: {language}")
    lines.append("")
    lines.append("Intake (structured):")
    lines.append(f"- presenting_problem: {_safe_text(intake_extracted.get('presenting_problem'))}")
    lines.append("- symptoms:")
    for s in intake_extracted.get("symptoms") or []:
        if isinstance(s, dict):
            lines.append(
                f"  - {_safe_text(s.get('label'))} "
                f"(severity={_safe_text(s.get('severity'))}, "
                f"duration_days={_safe_text(s.get('duration_days'))})"
            )
    lines.append("")
    lines.append("Recommendation (structured):")
    for p in recommendation.get("ranked_products") or []:
        if isinstance(p, dict):
            lines.append(
                f"- product {_product_label(p)}: "
                f"score={_safe_text(p.get('score_0_100'))} "
                f"why={_safe_text(p.get('why'))}"
            )
            refs = p.get("evidence_refs") or []
            if refs:
                lines.append(f"  evidence_refs: {', '.join([str(r) for r in refs])}")
    esc = recommendation.get("escalation")
    if isinstance(esc, dict) and esc.get("recommended") is True:
        lines.append(
            f"- escalation: {_safe_text(esc.get('suggested_service'))} "
            f"reason={_safe_text(esc.get('reason'))}"
        )
    lines.append("")
    lines.append("Evidence (allowed citations):")
    for ev in evidence_items[:10]:
        if isinstance(ev, dict):
            lines.append(
                f"- [{_safe_text(ev.get('evidence_id'))}] {_safe_text(ev.get('title'))} "
                f"({_safe_text(ev.get('publisher'))})"
            )
            lines.append(f"  summary: {_safe_text(ev.get('summary'))}")
    lines.append("")
    lines.append(
        "Write a concise pharmacist report in markdown with sections: "
        "Summary, Recommendations, Safety, Evidence."
    )
    return "\n".join(lines)[:7000]


def _render_report_template(
    *,
    intake_extracted: dict[str, Any],
    recommendation: dict[str, Any],
    evidence_items: list[dict[str, Any]],
    language: Language,
) -> str:
    title = "# Pharmacist report" if language == "en" else "# Rapport pharmacien"
    scope = (
        "OTC/parapharmacy decision support only."
        if language == "en"
        else "Aide a la decision OTC/parapharmacie uniquement."
    )
    note = (
        "Do not change prescription treatment without medical advice."
        if language == "en"
        else "Ne modifiez pas votre traitement sur ordonnance sans avis medical."
    )

    lines: list[str] = [title, ""]
    lines.append(f"- Scope: {scope}")
    lines.append(f"- Note: {note}")
    lines.append("")

    lines.append("## Summary" if language == "en" else "## Synthese")
    lines.append(f"- Presenting problem: {_safe_text(intake_extracted.get('presenting_problem'))}")

    symptoms = []
    for s in intake_extracted.get("symptoms") or []:
        if isinstance(s, dict) and isinstance(s.get("label"), str):
            symptoms.append(_safe_text(s["label"]))
    if symptoms:
        lines.append(f"- Symptoms: {', '.join(symptoms)}")

    esc = recommendation.get("escalation")
    if isinstance(esc, dict) and esc.get("recommended") is True:
        lines.append("")
        lines.append("## Escalation" if language == "en" else "## Escalade")
        lines.append(
            f"- {_safe_text(esc.get('suggested_service'))}: "
            f"{_safe_text(esc.get('reason'))}"
        )

    lines.append("")
    lines.append("## Recommendations" if language == "en" else "## Recommandations")
    ranked = recommendation.get("ranked_products") or []
    if not ranked:
        lines.append("- (none)")
    else:
        for p in ranked:
            if not isinstance(p, dict):
                continue
            label = _product_label(p)
            score = _safe_text(p.get("score_0_100"))
            why = _safe_text(p.get("why"))
            refs = [r for r in (p.get("evidence_refs") or []) if isinstance(r, str)]
            cite = " ".join([f"[{r}]" for r in refs]) if refs else ""
            lines.append(f"- {label} (score {score}): {why} {cite}".strip())

    lines.append("")
    lines.append("## Safety" if language == "en" else "## Securite")
    warnings = recommendation.get("safety_warnings") or []
    if not warnings:
        lines.append("- (none)")
    else:
        for w in warnings:
            if isinstance(w, dict):
                lines.append(
                    f"- {_safe_text(w.get('severity'))}: "
                    f"{_safe_text(w.get('code'))} - {_safe_text(w.get('message'))}"
                )

    lines.append("")
    lines.append("## Evidence" if language == "en" else "## Sources")
    if not evidence_items:
        lines.append("- (none)")
    else:
        for ev in evidence_items:
            if not isinstance(ev, dict):
                continue
            ev_id = _safe_text(ev.get("evidence_id"))
            title_ev = _safe_text(ev.get("title"))
            pub = _safe_text(ev.get("publisher"))
            url = _safe_text(ev.get("url"))
            lines.append(f"- [{ev_id}] {title_ev} — {pub} ({url})")

    md = "\n".join(lines)
    return md[:20000]
