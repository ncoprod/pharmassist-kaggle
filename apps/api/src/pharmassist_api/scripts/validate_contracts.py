from __future__ import annotations

import json
import sys

from pharmassist_api.contracts.load_schema import examples_dir
from pharmassist_api.validators.policy_validate import validate_payload


def main() -> int:
    ex_dir = examples_dir()
    example_files = sorted(ex_dir.glob("*.example.json"))

    if not example_files:
        sys.stderr.write(f"No examples found in {ex_dir}\n")
        return 1

    had_blocker = False
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

    if had_blocker:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
