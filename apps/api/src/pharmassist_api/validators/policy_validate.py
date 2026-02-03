from __future__ import annotations

from typing import Any

from pharmassist_api.contracts.validate_schema import validate_or_return_errors

from .phi_scanner import scan_for_phi
from .rx_advice_lint import lint_rx_advice
from .types import Violation


def validate_payload(payload: Any, *, schema_name: str) -> list[Violation]:
    """Validate schema + policy lints.

    Note: this is a conservative MVP implementation for Kaggle. The production
    product will use a stricter PHI boundary and post-generation scanners.
    """
    violations: list[Violation] = []

    schema_errors = validate_or_return_errors(payload, schema_name)
    for err in schema_errors:
        violations.append(
            Violation(
                code="SCHEMA_INVALID",
                severity="BLOCKER",
                json_path=err.json_path,
                message=err.message,
            )
        )

    violations.extend(scan_for_phi(payload))

    # RX advice lint is only applied to known free-text artifacts (report/handout).
    if schema_name == "run" and isinstance(payload, dict):
        artifacts = payload.get("artifacts") or {}
        if isinstance(artifacts, dict):
            report = artifacts.get("report_markdown")
            if isinstance(report, str):
                violations.extend(lint_rx_advice(report, path="$.artifacts.report_markdown"))

            handout = artifacts.get("handout_markdown")
            if isinstance(handout, str):
                violations.extend(lint_rx_advice(handout, path="$.artifacts.handout_markdown"))

    return violations

