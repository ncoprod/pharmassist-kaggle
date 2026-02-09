from __future__ import annotations

import io
import multiprocessing as mp
import os
import re
import threading
import uuid
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Literal

from pypdf import PdfReader

from pharmassist_api.contracts.validate_schema import validate_instance
from pharmassist_api.privacy.phi_boundary import PhiBoundaryError, raise_if_phi
from pharmassist_api.steps.a1_intake_extraction import extract_intake

Language = Literal["fr", "en"]

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"\b(?:\+33|0)[1-9](?:[ .-]?\d{2}){4}\b")
_NIR_RE = re.compile(r"\b[12]\s?\d{2}\s?(?:0[1-9]|1[0-2])\s?\d{2}\s?\d{3}\s?\d{3}\s?\d{2}\b")
_LABEL_VALUE_RE = re.compile(
    r"(?im)\b("
    r"nom|prenom|name|first[\s-]*name|last[\s-]*name|surname|"
    r"date\s*de\s*naissance|date\s*of\s*birth|dob|"
    r"adresse|address|telephone|phone|email|mail"
    r")\s*:\s*([^\n\r]+)"
)


def max_upload_bytes() -> int:
    raw = (os.getenv("PHARMASSIST_MAX_PRESCRIPTION_UPLOAD_BYTES") or "").strip()
    if raw.isdigit():
        return max(64_000, min(int(raw), 20_000_000))
    return 5_000_000


def max_pdf_pages() -> int:
    raw = (os.getenv("PHARMASSIST_MAX_PRESCRIPTION_PAGES") or "").strip()
    if raw.isdigit():
        return max(1, min(int(raw), 500))
    return 200


def max_pdf_extract_seconds() -> float:
    raw = (os.getenv("PHARMASSIST_MAX_PRESCRIPTION_EXTRACT_SEC") or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return 4.0
    return max(0.2, min(value, 30.0))


def _sha256_12(data: bytes) -> str:
    return sha256(data).hexdigest()[:12]


def _extract_pdf_text_impl(data: bytes, *, max_text_len: int) -> tuple[str, str, int]:
    if not data.startswith(b"%PDF-"):
        raise ValueError("Invalid PDF header")

    reader = PdfReader(io.BytesIO(data), strict=False)
    if getattr(reader, "is_encrypted", False):
        raise ValueError("Encrypted PDF files are not supported")

    page_count = len(reader.pages)
    if page_count > max_pdf_pages():
        raise ValueError(f"PDF has too many pages (max {max_pdf_pages()})")

    chunks: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            chunks.append(text)

    merged_full = "\n".join(chunks).strip()
    merged_for_model = (
        merged_full[:max_text_len] if len(merged_full) > max_text_len else merged_full
    )
    return merged_full, merged_for_model, page_count


def _extract_pdf_text_worker(
    data: bytes,
    *,
    max_text_len: int,
    conn: Any,
) -> None:
    try:
        conn.send(("ok", _extract_pdf_text_impl(data, max_text_len=max_text_len)))
    except ValueError as e:
        conn.send(("value_error", str(e)))
    except Exception:
        conn.send(("error", "unreadable_pdf"))
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _text_from_pdf_bytes(data: bytes, *, max_text_len: int = 50_000) -> tuple[str, str, int]:
    timeout = max_pdf_extract_seconds()
    if threading.active_count() > 1:
        # Forking a multi-threaded process can deadlock; use spawn in that case.
        ctx = mp.get_context("spawn")
    else:
        try:
            ctx = mp.get_context("fork")
        except ValueError:
            # Fallback for platforms where fork is unavailable.
            ctx = mp.get_context("spawn")

    parent_conn, child_conn = ctx.Pipe(duplex=False)
    proc = ctx.Process(
        target=_extract_pdf_text_worker,
        kwargs={
            "data": data,
            "max_text_len": max_text_len,
            "conn": child_conn,
        },
        daemon=True,
    )
    proc.start()
    child_conn.close()

    try:
        if not parent_conn.poll(timeout):
            raise ValueError("PDF extraction timed out")

        try:
            result = parent_conn.recv()
        except EOFError:
            raise ValueError("Invalid or unreadable PDF payload") from None

        if not isinstance(result, tuple) or not result:
            raise ValueError("Invalid or unreadable PDF payload")

        if result[0] == "ok" and len(result) == 2:
            payload = result[1]
            if (
                isinstance(payload, tuple)
                and len(payload) == 3
                and isinstance(payload[0], str)
                and isinstance(payload[1], str)
                and isinstance(payload[2], int)
            ):
                return payload
            raise ValueError("Invalid or unreadable PDF payload")

        if result[0] == "value_error" and len(result) == 2:
            raise ValueError(str(result[1]) or "Invalid or unreadable PDF payload")

        raise ValueError("Invalid or unreadable PDF payload")
    finally:
        parent_conn.close()
        if proc.is_alive():
            proc.terminate()
        proc.join(timeout=0.2)


def redact_phi_text(text: str) -> tuple[str, dict[str, Any]]:
    out = text
    replacements = 0

    out, c = _LABEL_VALUE_RE.subn(lambda m: f"{m.group(1)}: [REDACTED]", out)
    replacements += c
    out, c = _EMAIL_RE.subn("[REDACTED_EMAIL]", out)
    replacements += c
    out, c = _PHONE_RE.subn("[REDACTED_PHONE]", out)
    replacements += c
    out, c = _NIR_RE.subn("[REDACTED_NIR]", out)
    replacements += c

    return out, {"replacements": replacements}


def infer_primary_domain(intake_extracted: dict[str, Any]) -> str:
    labels: list[str] = []
    for s in intake_extracted.get("symptoms") or []:
        if isinstance(s, dict) and isinstance(s.get("label"), str):
            labels.append(s["label"].lower())
    blob = " ".join(labels) + " " + str(intake_extracted.get("presenting_problem") or "").lower()

    if any(k in blob for k in ("sneez", "itchy eyes", "allerg")):
        return "allergy_ent"
    if any(k in blob for k in ("bloat", "digest", "nausea", "diarr")):
        return "digestive"
    if any(k in blob for k in ("dry skin", "rash", "skin", "eczema")):
        return "skin"
    if any(k in blob for k in ("eye", "conjunct", "ocular")):
        return "eye"
    if any(k in blob for k in ("urination", "urinary", "urolog")):
        return "urology"
    if any(k in blob for k in ("headache", "pain", "migraine")):
        return "pain"
    if any(k in blob for k in ("cough", "sore throat", "dyspnea", "breath", "respir")):
        return "respiratory"
    return "other"


def ingest_prescription_pdf(
    *,
    patient_ref: str,
    language: Language,
    pdf_bytes: bytes,
) -> dict[str, Any]:
    doc_ref = f"doc_{uuid.uuid4().hex[:12]}"
    event_ref = f"ev_doc_{uuid.uuid4().hex[:12]}"
    visit_ref = f"visit_doc_{uuid.uuid4().hex[:12]}"
    occurred_at = datetime.now(UTC).isoformat()
    sha12 = _sha256_12(pdf_bytes)

    extracted_text_full, extracted_text, page_count = _text_from_pdf_bytes(pdf_bytes)
    if not extracted_text_full:
        raise ValueError("PDF text-layer extraction returned empty text")

    # PHI boundary scan must cover the complete extracted text, not only the model slice.
    redacted_full_text, _ = redact_phi_text(extracted_text_full)
    redacted_text, redaction = redact_phi_text(extracted_text)

    try:
        raise_if_phi(redacted_full_text, "$.documents.prescription.redacted_text_full")
    except PhiBoundaryError as e:
        violations = [
            {
                "code": v.code,
                "severity": v.severity,
                "json_path": v.json_path,
                "message": v.message,
            }
            for v in e.violations
        ]
        return {
            "status": "failed_phi_boundary",
            "doc_ref": doc_ref,
            "event_ref": event_ref,
            "visit_ref": visit_ref,
            "patient_ref": patient_ref,
            "occurred_at": occurred_at,
            "sha256_12": sha12,
            "page_count": page_count,
            "text_length": len(extracted_text),
            "redacted_text_length": len(redacted_text),
            "redaction_replacements": int(redaction["replacements"]),
            "violations": violations,
            "intake_extracted": None,
            "primary_domain": "other",
            "event_payload": None,
        }

    intake_extracted = extract_intake(redacted_text, language)
    validate_instance(intake_extracted, "intake_extracted")

    primary_domain = infer_primary_domain(intake_extracted)
    event_payload = {
        "doc_ref": doc_ref,
        "sha256_12": sha12,
        "page_count": int(page_count),
        "text_length": len(extracted_text),
        "redaction_applied": int(redaction["replacements"]) > 0,
        "redaction_replacements": int(redaction["replacements"]),
    }
    validate_instance(event_payload, "pharmacy_event_payload")

    return {
        "status": "ingested",
        "doc_ref": doc_ref,
        "event_ref": event_ref,
        "visit_ref": visit_ref,
        "patient_ref": patient_ref,
        "occurred_at": occurred_at,
        "sha256_12": sha12,
        "page_count": page_count,
        "text_length": len(extracted_text),
        "redacted_text_length": len(redacted_text),
        "redaction_replacements": int(redaction["replacements"]),
        "violations": [],
        "intake_extracted": intake_extracted,
        "primary_domain": primary_domain,
        "event_payload": event_payload,
    }
