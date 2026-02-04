from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def load_question_bank() -> dict[str, dict[str, Any]]:
    """Load the closed follow-up question bank from JSON.

    The bank is a safety boundary:
    - questions are stable via `question_id`
    - model-backed selection may only choose from this allowlist
    """
    path = Path(__file__).resolve().parent / "question_bank.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    bank = data.get("bank")
    if not isinstance(bank, dict):
        return {}

    out: dict[str, dict[str, Any]] = {}
    for qid, raw in bank.items():
        if not isinstance(qid, str) or not qid:
            continue
        if not isinstance(raw, dict):
            continue
        out[qid] = raw
    return out

