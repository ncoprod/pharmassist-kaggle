from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Severity = Literal["BLOCKER", "WARN"]


@dataclass(frozen=True)
class Violation:
    code: str
    severity: Severity
    json_path: str
    message: str

