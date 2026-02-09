from __future__ import annotations

from typing import Any, Literal

from pharmassist_api.validators.rx_advice_lint import lint_rx_advice

Language = Literal["fr", "en"]


def _safe_text(value: Any) -> str:
    return str(value or "").replace("<", "‹").replace(">", "›").strip()


def compose_handout_markdown(
    *,
    recommendation: dict[str, Any] | None,
    language: Language,
) -> str:
    """Compose a 1-page patient handout (Day 8).

    - Minimal, print-friendly, and OTC/parapharmacy only.
    - Never contains prescription-medication advice.
    """
    recommendation = recommendation or {}
    title = "# Patient handout" if language == "en" else "# Fiche patient"

    lines: list[str] = [title, ""]
    lines.append(
        "- Scope: OTC/parapharmacy decision support only."
        if language == "en"
        else "- Scope: OTC/parapharmacie uniquement."
    )
    lines.append(
        "- Do not change prescription treatment without medical advice."
        if language == "en"
        else "- Ne modifiez pas votre traitement sur ordonnance sans avis medical."
    )

    esc = recommendation.get("escalation")
    if isinstance(esc, dict) and esc.get("recommended") is True:
        lines.append("")
        lines.append("## When to seek care" if language == "en" else "## Quand consulter")
        lines.append(f"- {_safe_text(esc.get('reason'))}")
        lines.append(f"- Service: {_safe_text(esc.get('suggested_service'))}")

    ranked = recommendation.get("ranked_products") or []
    if ranked:
        lines.append("")
        lines.append("## Suggested products" if language == "en" else "## Produits proposes")
        for p in ranked[:3]:
            if isinstance(p, dict):
                lines.append(f"- {_safe_text(p.get('product_sku'))}: {_safe_text(p.get('why'))}")

    lines.append("")
    lines.append("## What to do now" if language == "en" else "## A faire")
    lines.append(
        "- Follow your pharmacist instructions."
        if language == "en"
        else "- Suivez les conseils du pharmacien."
    )
    lines.append(
        "- Monitor symptoms; if worsening, seek care."
        if language == "en"
        else "- Surveillez les symptomes; si aggravation, consultez."
    )

    md = "\n".join(lines)[:20000]

    # Safety net: if lint detects Rx advice, fall back to minimal safe content.
    if any(v.severity == "BLOCKER" for v in lint_rx_advice(md, path="$.handout_markdown")):
        if language == "fr":
            return (
                "# Fiche patient\n\n"
                "- Suivez les conseils du pharmacien.\n"
                "- Si aggravation, consultez un medecin.\n"
                "- Ne modifiez pas votre traitement sur ordonnance sans avis medical.\n"
            )
        return (
            "# Patient handout\n\n"
            "- Follow your pharmacist instructions.\n"
            "- If symptoms worsen, consult a doctor.\n"
            "- Do not change prescription treatment without medical advice.\n"
        )
    return md
