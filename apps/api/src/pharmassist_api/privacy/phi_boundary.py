from __future__ import annotations

import re
from dataclasses import dataclass

from pharmassist_api.validators.phi_scanner import scan_for_phi
from pharmassist_api.validators.types import Violation


@dataclass(frozen=True)
class PhiBoundaryError(Exception):
    """Raised when PHI-like patterns are detected in untrusted input."""

    violations: list[Violation]


# Heuristic markers: treat "field labels" as suspicious in untrusted text.
# We keep this conservative to avoid false positives (only triggers on "label:").
_PHI_LABEL_RE = re.compile(
    r"\b(nom|pr[eé]nom|adresse|email|mail|t[eé]l[eé]phone|telephone|nir|ssn)\s*:",
    flags=re.IGNORECASE,
)


def scan_text(text: str, json_path: str) -> list[Violation]:
    """Scan an untrusted text blob for PHI-like content.

    Note: This is a hard boundary. If any BLOCKER violation is found, we should
    not call any model with this text.
    """
    violations = scan_for_phi(text, path=json_path)

    if _PHI_LABEL_RE.search(text):
        violations.append(
            Violation(
                code="PHI_LABEL",
                severity="BLOCKER",
                json_path=json_path,
                message=(
                    "Identifier-like field label detected in untrusted text "
                    "(e.g. 'nom:', 'email:')."
                ),
            )
        )

    return violations


def raise_if_phi(text: str, json_path: str) -> None:
    violations = scan_text(text, json_path=json_path)
    blockers = [v for v in violations if v.severity == "BLOCKER"]
    if blockers:
        raise PhiBoundaryError(violations=blockers)
