from __future__ import annotations

import io
import time

import pytest
from pypdf import PdfWriter
from reportlab.pdfgen import canvas

from pharmassist_api.pharmacy.prescription_upload import ingest_prescription_pdf


def _simple_pdf() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, invariant=1)
    c.drawString(40, 780, "Symptoms: sneezing and itchy eyes")
    c.save()
    return buf.getvalue()


def _encrypted_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt("secret")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_ingest_rejects_invalid_pdf_header():
    with pytest.raises(ValueError, match="Invalid PDF header"):
        ingest_prescription_pdf(
            patient_ref="pt_000000",
            language="en",
            pdf_bytes=b"not-a-pdf",
        )


def test_ingest_rejects_encrypted_pdf():
    with pytest.raises(ValueError, match="Encrypted PDF"):
        ingest_prescription_pdf(
            patient_ref="pt_000000",
            language="en",
            pdf_bytes=_encrypted_pdf(),
        )


def test_ingest_rejects_timeout(monkeypatch):
    from pharmassist_api.pharmacy import prescription_upload as mod

    def _slow_extract(_data: bytes, *, max_text_len: int):
        time.sleep(0.6)
        return ("", "", 0)

    monkeypatch.setenv("PHARMASSIST_MAX_PRESCRIPTION_EXTRACT_SEC", "0.2")
    monkeypatch.setattr(mod, "_extract_pdf_text_impl", _slow_extract)

    with pytest.raises(ValueError, match="timed out"):
        ingest_prescription_pdf(
            patient_ref="pt_000000",
            language="en",
            pdf_bytes=_simple_pdf(),
        )
