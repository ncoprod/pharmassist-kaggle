from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Literal

from pharmassist_api.contracts.validate_schema import validate_or_return_errors
from pharmassist_api.models.medgemma_client import medgemma_extract_json
from pharmassist_api.privacy.phi_boundary import raise_if_phi
from pharmassist_api.validators.phi_scanner import scan_for_phi

SCHEMA_VERSION = "0.0.0"


def extract_intake(ocr_text: str, language: Literal["fr", "en"]) -> dict[str, Any]:
    """Extract `intake_extracted` from untrusted OCR text.

    - Hard PHI boundary: do not call any model if PHI-like patterns are found.
    - Try MedGemma (optional) -> parse -> validate. On any failure, fallback deterministically.
    """
    raise_if_phi(ocr_text, "$.intake_text_ocr")

    # Optional MedGemma path (returns {"_raw": "..."}), disabled by default in CI.
    model_out = medgemma_extract_json(ocr_text, language)
    if isinstance(model_out, dict) and isinstance(model_out.get("_raw"), str):
        parsed = _parse_first_json_object(model_out["_raw"])
        if isinstance(parsed, dict):
            parsed = _canonicalize_intake_extracted(parsed, language)
            parsed.setdefault("schema_version", SCHEMA_VERSION)
            if not validate_or_return_errors(parsed, "intake_extracted"):
                # Defense-in-depth: ensure the model didn't echo identifiers.
                violations = scan_for_phi(parsed, path="$")
                if not any(v.severity == "BLOCKER" for v in violations):
                    return parsed

    return _extract_intake_fallback(ocr_text, language)


def _parse_first_json_object(text: str) -> Any:
    """Best-effort JSON object extraction from a model output."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    chunk = text[start : end + 1]
    try:
        return json.loads(chunk)
    except Exception:
        return None


def _extract_intake_fallback(ocr_text: str, language: Literal["fr", "en"]) -> dict[str, Any]:
    norm = _normalize(ocr_text)
    compact = norm.replace(" ", "")

    symptoms: list[dict[str, Any]] = []

    # Coarse detection (works with OCR spacing errors).
    has_sneezing = ("sneez" in compact) or ("eternu" in compact)

    has_itchy_eyes = ("itchy" in compact and "eye" in compact) or (
        "gratt" in compact and ("yeux" in compact or "oeil" in compact)
    )

    has_dry_skin = ("dryskin" in compact) or ("peausech" in compact)

    has_bloating = ("bloat" in compact) or ("ballonn" in compact)

    # Attempt to parse per-line details: "- label (severity, 7d)".
    for line in ocr_text.splitlines():
        s = _parse_symptom_line(line)
        if s:
            symptoms.append(s)

    # If we successfully parsed symptoms, prefer them for coarse flags too.
    labels_compact = _normalize(
        " ".join([str(s.get("label") or "") for s in symptoms if isinstance(s, dict)])
    ).replace(" ", "")
    if labels_compact:
        has_sneezing = has_sneezing or ("sneez" in labels_compact) or ("eternu" in labels_compact)
        has_itchy_eyes = has_itchy_eyes or ("itchyeyes" in labels_compact)
        has_dry_skin = has_dry_skin or ("dryskin" in labels_compact) or (
            "peausech" in labels_compact
        )
        has_bloating = has_bloating or ("bloat" in labels_compact) or ("ballonn" in labels_compact)

    # If OCR noise broke the structured lines, build symptoms from coarse flags.
    if not symptoms:
        if has_sneezing:
            symptoms.append({"label": "sneezing", "severity": "unknown"})
        if has_itchy_eyes:
            symptoms.append({"label": "itchy eyes", "severity": "unknown"})
        if has_dry_skin:
            symptoms.append({"label": "dry skin", "severity": "unknown"})
        if has_bloating:
            symptoms.append({"label": "bloating", "severity": "unknown"})

    if not symptoms:
        symptoms = [{"label": "unspecified symptom", "severity": "unknown"}]

    presenting_problem = _infer_presenting_problem(
        has_sneezing=has_sneezing,
        has_itchy_eyes=has_itchy_eyes,
        has_dry_skin=has_dry_skin,
        has_bloating=has_bloating,
        language=language,
    )

    out = {
        "schema_version": SCHEMA_VERSION,
        "presenting_problem": presenting_problem,
        "symptoms": symptoms,
        "red_flags": [],
    }
    # Ensure schema compliance (fallback must never crash).
    if validate_or_return_errors(out, "intake_extracted"):
        # Last-resort minimal object.
        return {
            "schema_version": SCHEMA_VERSION,
            "presenting_problem": "unspecified",
            "symptoms": [{"label": "unspecified symptom", "severity": "unknown"}],
            "red_flags": [],
        }
    return out


def _canonicalize_intake_extracted(
    payload: dict[str, Any], language: Literal["fr", "en"]
) -> dict[str, Any]:
    """Best-effort cleanup of model outputs (spacing/leetspeak) for downstream rules."""
    out = dict(payload)

    # Canonicalize symptom labels (e.g. "snee zing" -> "sneezing") when possible.
    cleaned_symptoms: list[dict[str, Any]] = []
    for s in out.get("symptoms") or []:
        if not isinstance(s, dict):
            continue
        item = dict(s)
        label = item.get("label")
        if isinstance(label, str) and label.strip():
            compact = _normalize(label).replace(" ", "")
            canonical = _canonical_label(_deleet(compact)) or _canonical_label(compact)
            if canonical:
                item["label"] = canonical
            else:
                item["label"] = re.sub(r"\s+", " ", label).strip()
        cleaned_symptoms.append(item)
    if cleaned_symptoms:
        out["symptoms"] = cleaned_symptoms

    # If the presenting_problem is empty/unspecified, infer it from the (cleaned) symptom labels.
    pp = out.get("presenting_problem")
    pp_norm = _normalize(pp) if isinstance(pp, str) else ""
    if not pp_norm or "unspecified" in pp_norm or "non specifie" in pp_norm:
        labels_compact = _normalize(
            " ".join([str(s.get("label") or "") for s in cleaned_symptoms])
        ).replace(" ", "")
        has_sneezing = ("sneez" in labels_compact) or ("eternu" in labels_compact)
        has_itchy_eyes = "itchyeyes" in labels_compact
        has_dry_skin = ("dryskin" in labels_compact) or ("peausech" in labels_compact)
        has_bloating = ("bloat" in labels_compact) or ("ballonn" in labels_compact)
        out["presenting_problem"] = _infer_presenting_problem(
            has_sneezing=has_sneezing,
            has_itchy_eyes=has_itchy_eyes,
            has_dry_skin=has_dry_skin,
            has_bloating=has_bloating,
            language=language,
        )

    return out


def _infer_presenting_problem(
    *,
    has_sneezing: bool,
    has_itchy_eyes: bool,
    has_dry_skin: bool,
    has_bloating: bool,
    language: Literal["fr", "en"],
) -> str:
    if has_sneezing and has_itchy_eyes:
        return (
            "Eternuements et yeux qui grattent"
            if language == "fr"
            else "Sneezing and itchy eyes"
        )
    if has_dry_skin:
        return "Peau seche" if language == "fr" else "Dry skin"
    if has_bloating:
        return "Ballonnements" if language == "fr" else "Bloating after meals"
    return "Symptomes non specifie(s)" if language == "fr" else "Unspecified symptoms"


_SYMPTOM_LINE_RE = re.compile(
    r"^\s*-\s*(?P<label>[^()]{1,80})\s*\((?P<meta>[^)]{1,80})\)\s*$"
)


def _parse_symptom_line(line: str) -> dict[str, Any] | None:
    m = _SYMPTOM_LINE_RE.match(line.strip())
    if not m:
        return None

    label_raw = m.group("label").strip()
    meta = m.group("meta").lower()

    # Undo common OCR spacing breaks.
    label_norm = _normalize(label_raw).replace(" ", "")
    label = _canonical_label(_deleet(label_norm)) or _canonical_label(label_norm)
    if not label:
        # fallback to raw, trimmed
        label = label_raw.strip()

    severity = "unknown"
    if "mild" in meta or "leger" in meta:
        severity = "mild"
    elif "moderate" in meta or "modere" in meta:
        severity = "moderate"
    elif "severe" in meta or "sever" in meta:
        severity = "severe"

    duration_days: int | None = None
    dur = _parse_duration_days(meta)
    if dur is not None:
        duration_days = dur

    out: dict[str, Any] = {"label": label, "severity": severity}
    if duration_days is not None:
        out["duration_days"] = duration_days
    return out


def _parse_duration_days(text: str) -> int | None:
    # Accept "7d", "7 d", "7j", "7 jours".
    m = re.search(r"\b(\d{1,4})\s*(?:d|j|jour|jours)\b", text)
    if not m:
        return None
    try:
        v = int(m.group(1))
    except ValueError:
        return None
    if 0 <= v <= 3650:
        return v
    return None


def _canonical_label(compact_norm: str) -> str | None:
    if "sneez" in compact_norm or "eternu" in compact_norm:
        return "sneezing"
    if ("itchy" in compact_norm and "eye" in compact_norm) or (
        "gratt" in compact_norm and ("yeux" in compact_norm or "oeil" in compact_norm)
    ):
        return "itchy eyes"
    if "dryskin" in compact_norm or "peausech" in compact_norm:
        return "dry skin"
    if "bloat" in compact_norm or "ballonn" in compact_norm:
        return "bloating"
    return None


def _deleet(text: str) -> str:
    # Common OCR/leetspeak substitutions (helps normalize labels like "3ye5" -> "eyes").
    return text.translate(
        str.maketrans(
            {
                "0": "o",
                "1": "i",
                "2": "z",
                "3": "e",
                "4": "a",
                "5": "s",
                "6": "g",
                "7": "t",
                "8": "b",
                "9": "g",
            }
        )
    )


def _normalize(text: str) -> str:
    norm = unicodedata.normalize("NFKD", text)
    norm = "".join(ch for ch in norm if not unicodedata.combining(ch))
    norm = norm.lower()
    norm = re.sub(r"[^a-z0-9\s:/().,-]+", " ", norm)
    norm = re.sub(r"\s+", " ", norm).strip()
    return norm
