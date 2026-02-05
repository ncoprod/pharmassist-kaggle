from __future__ import annotations

import json
import sys

from pharmassist_api.contracts.load_schema import examples_dir, repo_root, schemas_dir
from pharmassist_api.contracts.validate_schema import validate_or_return_errors
from pharmassist_api.validators.policy_validate import validate_payload


def main() -> int:
    ex_dir = examples_dir()
    example_files = sorted(ex_dir.glob("*.example.json"))

    if not example_files:
        sys.stderr.write(f"No examples found in {ex_dir}\n")
        return 1

    had_blocker = False

    # Contracts-first: every schema must have a corresponding example file.
    schema_files = sorted(schemas_dir().glob("*.schema.json"))
    example_names = {p.name.replace(".example.json", "") for p in example_files}
    for schema_path in schema_files:
        schema_name = schema_path.name.replace(".schema.json", "")
        if schema_name == "_meta":
            continue
        if schema_name not in example_names:
            sys.stderr.write(
                f"[BLOCKER] missing example for schema {schema_name} "
                f"(expected {schema_name}.example.json)\n"
            )
            had_blocker = True
    for path in example_files:
        schema_name = path.name.replace(".example.json", "")
        payload = json.loads(path.read_text(encoding="utf-8"))

        try:
            violations = validate_payload(payload, schema_name=schema_name)
        except Exception as e:  # noqa: BLE001 - tooling script
            sys.stderr.write(f"[BLOCKER] {path.name}: exception during validation: {e}\n")
            had_blocker = True
            continue

        for v in violations:
            line = f"[{v.severity}] {path.name} {v.json_path}: {v.code}: {v.message}\n"
            if v.severity == "BLOCKER":
                had_blocker = True
                sys.stderr.write(line)
            else:
                sys.stdout.write(line)

    # Validate the closed question bank as a first-class contract input.
    qb_path = (
        repo_root()
        / "apps"
        / "api"
        / "src"
        / "pharmassist_api"
        / "steps"
        / "question_bank.json"
    )
    try:
        qb_payload = json.loads(qb_path.read_text(encoding="utf-8"))
        qb_violations = validate_payload(qb_payload, schema_name="question_bank")
    except Exception as e:  # noqa: BLE001 - tooling script
        sys.stderr.write(f"[BLOCKER] question_bank.json: exception during validation: {e}\n")
        had_blocker = True
    else:
        for v in qb_violations:
            line = f"[{v.severity}] question_bank.json {v.json_path}: {v.code}: {v.message}\n"
            if v.severity == "BLOCKER":
                had_blocker = True
                sys.stderr.write(line)
            else:
                sys.stdout.write(line)

    # Validate case fixtures' structured subparts (schema-only; fixtures may include PHI-like OCR).
    fixtures_dir = (
        repo_root()
        / "apps"
        / "api"
        / "src"
        / "pharmassist_api"
        / "cases"
        / "fixtures"
    )
    for case_path in sorted(fixtures_dir.glob("*.json")):
        try:
            bundle = json.loads(case_path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001 - tooling script
            sys.stderr.write(f"[BLOCKER] {case_path.name}: invalid JSON: {e}\n")
            had_blocker = True
            continue

        if not isinstance(bundle, dict):
            sys.stderr.write(f"[BLOCKER] {case_path.name}: fixture must be a JSON object\n")
            had_blocker = True
            continue

        for key, schema_name in [
            ("llm_context", "llm_context"),
            ("intake_extracted", "intake_extracted"),
        ]:
            issues = validate_or_return_errors(bundle.get(key), schema_name)
            for i in issues:
                sys.stderr.write(
                    f"[BLOCKER] {case_path.name} $.{key}{i.json_path}: "
                    f"SCHEMA_INVALID: {i.message}\n"
                )
                had_blocker = True

        products = bundle.get("products")
        if isinstance(products, list):
            for idx, p in enumerate(products):
                issues = validate_or_return_errors(p, "product")
                for i in issues:
                    sys.stderr.write(
                        f"[BLOCKER] {case_path.name} $.products[{idx}]{i.json_path}: "
                        f"SCHEMA_INVALID: {i.message}\n"
                    )
                    had_blocker = True

    if had_blocker:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
