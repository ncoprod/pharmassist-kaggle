from __future__ import annotations

import re
from typing import Any

from .types import Violation

_FORBIDDEN_KEYS = {
    "surname",
    "first_name",
    "last_name",
    "full_name",
    "patient_name",
    "patient_first_name",
    "patient_last_name",
    "email",
    "phone",
    "address",
    "street",
    "city",
    "postal_code",
    "zip",
    "dob",
    "date_of_birth",
    "nir",
    "ssn",
    # French common variants
    "nom",
    "prenom",
    "adresse",
    "telephone",
    "téléphone",
    "mail",
    "code_postal",
    "ville",
    "date_naissance",
}

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)

# French phone: +33X XX XX XX XX  or 0X XX XX XX XX (separators optional)
_PHONE_FR_RE = re.compile(r"\b(?:\+33|0)[1-9](?:[ .-]?\d{2}){4}\b")

# Approximate NIR: 1/2 + YY + MM(01-12) + ... + key (spaces optional).
_NIR_RE = re.compile(r"\b[12]\s?\d{2}\s?(?:0[1-9]|1[0-2])\s?\d{2}\s?\d{3}\s?\d{3}\s?\d{2}\b")

_POSTAL_CODE_EXACT_RE = re.compile(r"^\d{5}$")


def scan_for_phi(payload: Any, *, path: str = "$") -> list[Violation]:
    violations: list[Violation] = []

    if isinstance(payload, dict):
        for k, v in payload.items():
            key = str(k)
            key_lower = key.lower()
            child_path = f"{path}.{key}"

            if key_lower in _FORBIDDEN_KEYS:
                violations.append(
                    Violation(
                        code="PHI_KEY",
                        severity="BLOCKER",
                        json_path=child_path,
                        message=f"Forbidden identifier-like key detected: {key}",
                    )
                )

            violations.extend(scan_for_phi(v, path=child_path))

    elif isinstance(payload, list):
        for idx, item in enumerate(payload):
            violations.extend(scan_for_phi(item, path=f"{path}[{idx}]"))

    elif isinstance(payload, str):
        violations.extend(_scan_text(payload, path=path))

    return violations


def _scan_text(text: str, *, path: str) -> list[Violation]:
    violations: list[Violation] = []
    if _EMAIL_RE.search(text):
        violations.append(
            Violation(
                code="PHI_EMAIL",
                severity="BLOCKER",
                json_path=path,
                message="Email-like pattern detected in text.",
            )
        )

    if _PHONE_FR_RE.search(text):
        violations.append(
            Violation(
                code="PHI_PHONE_FR",
                severity="BLOCKER",
                json_path=path,
                message="French phone-like pattern detected in text.",
            )
        )

    if _NIR_RE.search(text):
        violations.append(
            Violation(
                code="PHI_NIR",
                severity="BLOCKER",
                json_path=path,
                message="NIR-like pattern detected in text.",
            )
        )

    if _POSTAL_CODE_EXACT_RE.match(text.strip()):
        violations.append(
            Violation(
                code="PHI_POSTAL_CODE",
                severity="WARN",
                json_path=path,
                message="Standalone 5-digit postal code detected.",
            )
        )

    return violations
