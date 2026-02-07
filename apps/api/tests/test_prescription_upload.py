from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path

from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas


def _make_pdf(lines: list[str]) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, invariant=1)
    y = 800
    for line in lines:
        c.drawString(40, y, line[:160])
        y -= 16
    c.save()
    return buf.getvalue()


def _make_pdf_pages(pages: list[list[str]]) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, invariant=1)
    for idx, lines in enumerate(pages):
        y = 800
        for line in lines:
            c.drawString(40, y, line[:160])
            y -= 16
        if idx < len(pages) - 1:
            c.showPage()
    c.save()
    return buf.getvalue()


def test_upload_prescription_pdf_phi_present_is_rejected(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))

    from pharmassist_api import db
    from pharmassist_api.main import app

    lines = [
        "PHARMASSIST SYNTHETIC PRESCRIPTION",
        "Name: Lucy Martin",
        "Date of birth: 1987-06-14",
        "Phone: +33611223344",
        "Symptoms: sneezing and itchy eyes",
    ]

    with TestClient(app) as client:
        resp = client.post(
            "/documents/prescription",
            data={"patient_ref": "pt_000000", "language": "en"},
            files={"file": ("rx.pdf", _make_pdf(lines), "application/pdf")},
        )
    assert resp.status_code == 400
    detail = resp.json().get("detail")
    assert isinstance(detail, dict)
    assert detail.get("error") == "PHI detected after redaction"
    doc_ref = detail.get("doc_ref")
    assert isinstance(doc_ref, str) and doc_ref

    doc = db.get_document(doc_ref)
    assert isinstance(doc, dict)
    metadata = doc.get("metadata")
    assert isinstance(metadata, dict)
    assert metadata.get("status") == "failed_phi_boundary"

    dumped = json.dumps(metadata, ensure_ascii=False)
    assert "Lucy Martin" not in dumped
    assert "+33611223344" not in dumped


def test_upload_prescription_pdf_phi_free_is_ingested_and_runnable(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))

    from pharmassist_api import db
    from pharmassist_api.contracts.validate_schema import validate_instance
    from pharmassist_api.main import app
    from pharmassist_api.orchestrator import run_pipeline

    lines = [
        "PHARMASSIST SYNTHETIC PRESCRIPTION",
        "Symptoms: sneezing and itchy eyes for 7 days",
        "- sneezing (moderate, 7d)",
        "- itchy eyes (mild, 7d)",
    ]

    with TestClient(app) as client:
        resp = client.post(
            "/documents/prescription",
            data={"patient_ref": "pt_000000", "language": "en"},
            files={"file": ("rx.pdf", _make_pdf(lines), "application/pdf")},
        )
        assert resp.status_code == 200
        receipt = resp.json()
        validate_instance(receipt, "document_upload_receipt")
        visit_ref = receipt["visit_ref"]
        assert isinstance(db.get_visit(visit_ref), dict)

        run_resp = client.post(
            "/runs",
            json={
                "patient_ref": "pt_000000",
                "visit_ref": visit_ref,
                "language": "en",
                "trigger": "ocr_upload",
            },
        )
        assert run_resp.status_code == 200
        run = run_resp.json()
        asyncio.run(run_pipeline(run["run_id"]))
        stored = db.get_run(run["run_id"])
        assert stored is not None
        assert stored["status"] == "completed"


def test_upload_prescription_rejects_non_pdf(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))

    from pharmassist_api.main import app

    with TestClient(app) as client:
        resp = client.post(
            "/documents/prescription",
            data={"patient_ref": "pt_000000", "language": "fr"},
            files={"file": ("rx.txt", b"hello", "text/plain")},
        )
    assert resp.status_code == 415


def test_upload_prescription_pdf_phi_after_many_pages_is_rejected(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))

    from pharmassist_api.main import app

    pages: list[list[str]] = []
    for idx in range(20):
        pages.append([f"Page {idx + 1}", "Symptoms: sneezing and itchy eyes"])
    pages.append(
        [
            "Final page",
            "Name: Hidden Patient",
            "Date of birth: 1988-01-01",
        ]
    )

    with TestClient(app) as client:
        resp = client.post(
            "/documents/prescription",
            data={"patient_ref": "pt_000000", "language": "en"},
            files={"file": ("rx_many_pages.pdf", _make_pdf_pages(pages), "application/pdf")},
        )
    assert resp.status_code == 400
    detail = resp.json().get("detail")
    assert isinstance(detail, dict)
    assert detail.get("error") == "PHI detected after redaction"
