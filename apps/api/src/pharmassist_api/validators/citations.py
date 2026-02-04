from __future__ import annotations

import re

from pharmassist_api.validators.types import Violation

_CITATION_RE = re.compile(r"\[(ev_[a-z0-9_]+)\]")


def lint_citations(markdown: str, *, evidence_ids: set[str], path: str) -> list[Violation]:
    """Ensure bracket-style citations reference known evidence ids.

    Expected format: `[ev_allergy_001]` etc. Unknown refs are BLOCKER.
    """
    refs = set(_CITATION_RE.findall(markdown or ""))
    unknown = sorted(r for r in refs if r not in evidence_ids)
    if not unknown:
        return []
    return [
        Violation(
            code="CITATION_UNKNOWN",
            severity="BLOCKER",
            json_path=path,
            message=f"Unknown evidence citation(s): {', '.join(unknown)}",
        )
    ]
