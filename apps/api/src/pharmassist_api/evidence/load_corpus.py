from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pharmassist_api.contracts.validate_schema import validate_instance


@lru_cache(maxsize=1)
def load_evidence_corpus() -> list[dict[str, Any]]:
    """Load the small offline evidence corpus (Day 7).

    The corpus is committed to the public Kaggle repo for reproducibility.
    """
    path = Path(__file__).resolve().parent / "corpus.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("evidence corpus must be a JSON array")

    out: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        validate_instance(item, "evidence_item")
        out.append(item)
    return out

