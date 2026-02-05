from __future__ import annotations

import re
import unicodedata

from .types import Violation


def _normalize_text(text: str) -> str:
    # Normalize smart apostrophes and strip accents so our regex stays ASCII-only.
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


_ALLOWLIST_RE = re.compile(
    r"(?i)\b(ne|n')\s*(modifi(?:ez|er)|arret(?:ez|er)|stoppez|stopper|"
    r"chang(?:ez|er)|augment(?:ez|er)|diminu(?:ez|er)|commenc(?:ez|er)|"
    r"debut(?:ez|er)|repren(?:ez|dre)|suspend(?:ez|re)|interromp(?:ez|re)).{0,60}\b"
    r"(traitement|posologie|dose|ordonnance)\b.{0,60}\b(sans avis|sans l'avis)\b"
)

_ALLOWLIST_EN_RE = re.compile(
    r"(?i)\bdo\s+not\b.{0,30}\b(change|stop|discontinue|start|initiate|resume|hold|taper|increase|decrease)\b.{0,60}\b"
    r"(prescription|medication|dose)\b"
)

_BLOCK_RE = re.compile(
    r"(?i)\b(arret(?:ez|er)|stoppez|stopper|modifi(?:ez|er)|augment(?:ez|er)|"
    r"diminu(?:ez|er)|chang(?:ez|er))\b.{0,60}\b("
    r"traitement|posologie|dose|ordonnance|antibiotique|corticoide|anticoagulant|"
    r"insuline|antidepresseur|chimiotherapie)\b"
)

_BLOCK_START_RE = re.compile(
    r"(?i)\b(commenc(?:ez|er)|debut(?:ez|er)|repren(?:ez|dre)|initier|initiez|"
    r"suspend(?:ez|re)|interromp(?:ez|re))\b.{0,60}\b("
    r"ordonnance|antibiotique|corticoide|anticoagulant|insuline|antidepresseur|"
    r"chimiotherapie)\b"
)

_BLOCK_EN_RE = re.compile(
    r"(?i)\b(stop|discontinue|change|increase|decrease)\b.{0,60}\b"
    r"(prescription|medication|dose)\b"
)

_BLOCK_START_EN_RE = re.compile(
    r"(?i)\b(start|initiate|resume|hold|taper)\b.{0,60}\b("
    r"prescription|antibiotic|steroid|anticoagulant|insulin|antidepressant|chemotherapy)\b"
)


def lint_rx_advice(text: str, *, path: str) -> list[Violation]:
    if not text.strip():
        return []

    normalized = _normalize_text(text)

    # Common safe disclaimer patterns should never fail the run.
    if _ALLOWLIST_RE.search(normalized) or _ALLOWLIST_EN_RE.search(normalized):
        return []

    if (
        _BLOCK_RE.search(normalized)
        or _BLOCK_START_RE.search(normalized)
        or _BLOCK_EN_RE.search(normalized)
        or _BLOCK_START_EN_RE.search(normalized)
    ):
        return [
            Violation(
                code="RX_ADVICE",
                severity="BLOCKER",
                json_path=path,
                message="Potential prescription-medication advice detected (start/stop/change).",
            )
        ]

    return []
