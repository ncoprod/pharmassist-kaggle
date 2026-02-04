from __future__ import annotations

import re
from typing import Any

from pharmassist_api.evidence.load_corpus import load_evidence_corpus
from pharmassist_api.steps.a6_product_ranker import _infer_target_category


def retrieve_evidence(
    *,
    intake_extracted: dict[str, Any],
    llm_context: dict[str, Any],
    k: int = 5,
) -> list[dict[str, Any]]:
    """Retrieve top-K evidence items from the offline corpus (Day 7).

    Deterministic keyword-overlap scoring; safe for CI and public Kaggle demos.
    """
    corpus = load_evidence_corpus()
    query = _build_query(intake_extracted, llm_context)
    q_tokens = _tokens(query)

    target_category = _infer_target_category(intake_extracted)

    scored: list[tuple[int, dict[str, Any]]] = []
    for item in corpus:
        title = str(item.get("title") or "")
        summary = str(item.get("summary") or "")
        pub = str(item.get("publisher") or "")
        evidence_id = str(item.get("evidence_id") or "")

        text_tokens = _tokens(f"{title} {summary}")
        overlap = len(q_tokens & text_tokens)

        bonus = 0
        pub_up = pub.upper()
        if "HAS" in pub_up:
            bonus += 3
        if "NHS" in pub_up:
            bonus += 2
        if "CDC" in pub_up:
            bonus += 2
        if "WHO" in pub_up:
            bonus += 1

        if target_category != "general":
            if evidence_id.startswith(f"ev_{target_category}"):
                bonus += 3

        score = overlap + bonus
        scored.append((score, item))

    scored.sort(key=lambda t: t[0], reverse=True)
    top = [item for _score, item in scored[: max(1, k)]]
    return top


def _build_query(intake_extracted: dict[str, Any], llm_context: dict[str, Any]) -> str:
    parts: list[str] = []
    parts.append(str(intake_extracted.get("presenting_problem") or ""))
    for s in intake_extracted.get("symptoms") or []:
        if isinstance(s, dict) and isinstance(s.get("label"), str):
            parts.append(s["label"])
    for c in (llm_context.get("conditions") or []) if isinstance(llm_context, dict) else []:
        if isinstance(c, dict) and isinstance(c.get("label"), str):
            parts.append(c["label"])
    return " ".join(parts)[:500]


_TOKEN_RE = re.compile(r"[a-z0-9]{3,}")


def _tokens(text: str) -> set[str]:
    text = text.lower()
    return set(_TOKEN_RE.findall(text))

