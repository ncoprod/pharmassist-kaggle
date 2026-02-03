from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_CASE_REF_RE = re.compile(r"^case_\d{6}$")


def load_case_bundle(case_ref: str) -> dict[str, Any]:
    """Load a synthetic case bundle from the repo fixtures.

    Kaggle demo only: fixtures are committed to the public repo and contain no PHI.
    """
    if not _CASE_REF_RE.match(case_ref):
        raise ValueError("unknown case_ref")

    fixtures_dir = Path(__file__).resolve().parent / "fixtures"
    path = fixtures_dir / f"{case_ref}.json"
    if not path.exists():
        raise ValueError("unknown case_ref")

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("invalid case fixture")

    return payload
