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

            # Defense-in-depth: lint other user-facing strings (not only markdown).
            reco = artifacts.get("recommendation")
            if isinstance(reco, dict):
                ranked = reco.get("ranked_products")
                if isinstance(ranked, list):
                    for idx, item in enumerate(ranked):
                        if not isinstance(item, dict):
                            continue
                        why = item.get("why")
                        if isinstance(why, str):
                            violations.extend(
                                lint_rx_advice(
                                    why,
                                    path=f"$.artifacts.recommendation.ranked_products[{idx}].why",
                                )
                            )

                safety_warnings = reco.get("safety_warnings")
                if isinstance(safety_warnings, list):
                    for idx, item in enumerate(safety_warnings):
                        if not isinstance(item, dict):
                            continue
                        msg = item.get("message")
                        if isinstance(msg, str):
                            violations.extend(
                                lint_rx_advice(
                                    msg,
                                    path=f"$.artifacts.recommendation.safety_warnings[{idx}].message",
                                )
                            )

                escalation = reco.get("escalation")
                if isinstance(escalation, dict):
                    reason = escalation.get("reason")
                    if isinstance(reason, str):
                        violations.extend(
                            lint_rx_advice(
                                reason,
                                path="$.artifacts.recommendation.escalation.reason",
                            )
                        )

            prebrief = artifacts.get("prebrief")
            if isinstance(prebrief, dict):
                for key in (
                    "top_actions",
                    "top_risks",
                    "top_questions",
                    "what_changed",
                    "new_rx_delta",
                ):
                    values = prebrief.get(key)
                    if not isinstance(values, list):
                        continue
                    for idx, value in enumerate(values):
                        if isinstance(value, str):
                            violations.extend(
                                lint_rx_advice(
                                    value,
                                    path=f"$.artifacts.prebrief.{key}[{idx}]",
                                )
                            )

            plan = artifacts.get("plan")
            if isinstance(plan, dict):
                safety_checks = plan.get("safety_checks")
                if isinstance(safety_checks, list):
                    for idx, value in enumerate(safety_checks):
                        if isinstance(value, str):
                            violations.extend(
                                lint_rx_advice(
                                    value,
                                    path=f"$.artifacts.plan.safety_checks[{idx}]",
                                )
                            )

                steps = plan.get("steps")
                if isinstance(steps, list):
                    for idx, step in enumerate(steps):
                        if not isinstance(step, dict):
                            continue
                        title = step.get("title")
                        detail = step.get("detail")
                        if isinstance(title, str):
                            violations.extend(
                                lint_rx_advice(
                                    title,
                                    path=f"$.artifacts.plan.steps[{idx}].title",
                                )
                            )
                        if isinstance(detail, str):
                            violations.extend(
                                lint_rx_advice(
                                    detail,
                                    path=f"$.artifacts.plan.steps[{idx}].detail",
                                )
                            )

    return violations
