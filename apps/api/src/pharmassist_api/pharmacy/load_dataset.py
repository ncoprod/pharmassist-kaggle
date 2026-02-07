from __future__ import annotations

import gzip
import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pharmassist_api import db
from pharmassist_api.contracts.validate_schema import validate_instance


def default_dataset_dir() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "paris15_mini"


def resolve_dataset_dir() -> Path:
    env = os.getenv("PHARMASSIST_PHARMACY_DATA_DIR", "").strip()
    if env:
        return Path(env)
    return default_dataset_dir()


def default_catalog_demo_path() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "catalog" / "products.demo.json"


def resolve_catalog_demo_path() -> Path:
    env = os.getenv("PHARMASSIST_CATALOG_DEMO_PATH", "").strip()
    if env:
        return Path(env)
    return default_catalog_demo_path()


def _iter_jsonl_gz(path: Path) -> Iterable[Any]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def _sanitize_event_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """Whitelist pharmacy-event payload shapes.

    Defense-in-depth: if a user points `PHARMASSIST_PHARMACY_DATA_DIR` at an
    external dataset, we must never persist arbitrary free-text blobs (e.g.
    OCR/PDF text) into SQLite.
    """
    if event_type == "symptom_intake":
        intake_extracted = payload.get("intake_extracted")
        if not isinstance(intake_extracted, dict):
            return None
        validate_instance(intake_extracted, "intake_extracted")
        return {"intake_extracted": intake_extracted}

    if event_type == "otc_purchase":
        items = payload.get("items")
        if not isinstance(items, list):
            return None
        out_items: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            sku = item.get("sku")
            qty = item.get("qty")
            if not (isinstance(sku, str) and sku.strip()):
                continue
            if not isinstance(qty, int):
                continue
            if qty <= 0 or qty > 99:
                continue
            out_items.append({"sku": sku.strip(), "qty": qty})
        if not out_items:
            return None
        return {"items": out_items}

    if event_type == "prescription_added":
        meds = payload.get("rx_medications")
        if not isinstance(meds, list):
            return None
        out_meds = [m.strip() for m in meds if isinstance(m, str) and m.strip()]
        if not out_meds:
            return None
        return {"rx_medications": out_meds[:50]}

    if event_type == "document_uploaded":
        doc_ref = payload.get("doc_ref")
        sha12 = payload.get("sha256_12")
        page_count = payload.get("page_count")
        text_length = payload.get("text_length")
        redaction_applied = payload.get("redaction_applied")
        redaction_replacements = payload.get("redaction_replacements")

        if not (isinstance(doc_ref, str) and doc_ref.strip()):
            return None
        if not (isinstance(sha12, str) and len(sha12) == 12):
            return None
        if not isinstance(page_count, int) or page_count <= 0 or page_count > 200:
            return None
        if not isinstance(text_length, int) or text_length <= 0 or text_length > 50000:
            return None
        if not isinstance(redaction_applied, bool):
            return None
        if (
            not isinstance(redaction_replacements, int)
            or redaction_replacements < 0
            or redaction_replacements > 2000
        ):
            return None
        return {
            "doc_ref": doc_ref.strip(),
            "sha256_12": sha12.lower(),
            "page_count": page_count,
            "text_length": text_length,
            "redaction_applied": redaction_applied,
            "redaction_replacements": redaction_replacements,
        }

    # Unknown event types are ignored (safer than persisting unknown payloads).
    return None


def ensure_pharmacy_dataset_loaded(*, dataset_dir: Path | None = None) -> dict[str, int]:
    """Idempotently load the synthetic pharmacy dataset into SQLite.

    The Kaggle demo must remain CI-safe and fast:
    - default dataset is the committed mini subset
    - larger datasets can be loaded by pointing PHARMASSIST_PHARMACY_DATA_DIR to a directory
      containing the expected JSONL.gz files.
    """
    if db.count_patients() > 0 and db.count_visits() > 0 and db.count_inventory() > 0:
        return {
            "loaded": 0,
            "patients": db.count_patients(),
            "visits": db.count_visits(),
            "inventory": db.count_inventory(),
        }

    root = dataset_dir or resolve_dataset_dir()
    patients_path = root / "patients.jsonl.gz"
    visits_path = root / "visits.jsonl.gz"
    events_path = root / "events.jsonl.gz"
    inventory_path = root / "inventory.jsonl.gz"

    if not (
        patients_path.exists()
        and visits_path.exists()
        and events_path.exists()
        and inventory_path.exists()
    ):
        raise FileNotFoundError(
            "Pharmacy dataset directory missing required files: "
            "patients.jsonl.gz, visits.jsonl.gz, events.jsonl.gz, inventory.jsonl.gz"
        )

    patients_loaded = 0
    for raw in _iter_jsonl_gz(patients_path):
        if not isinstance(raw, dict):
            continue
        patient_ref = raw.get("patient_ref")
        llm_context = raw.get("llm_context")
        if not (isinstance(patient_ref, str) and patient_ref):
            continue
        if not isinstance(llm_context, dict):
            continue
        validate_instance(llm_context, "llm_context")
        db.upsert_patient(patient_ref=patient_ref, llm_context=llm_context)
        patients_loaded += 1

    visits_loaded = 0
    for raw in _iter_jsonl_gz(visits_path):
        if not isinstance(raw, dict):
            continue
        visit_ref = raw.get("visit_ref")
        patient_ref = raw.get("patient_ref")
        occurred_at = raw.get("occurred_at")
        primary_domain = raw.get("primary_domain")
        intents = raw.get("intents")
        intake_extracted = raw.get("intake_extracted")
        if not (isinstance(visit_ref, str) and visit_ref):
            continue
        if not (isinstance(patient_ref, str) and patient_ref):
            continue
        if not (isinstance(occurred_at, str) and occurred_at):
            continue
        if primary_domain is not None and not isinstance(primary_domain, str):
            primary_domain = None
        if not isinstance(intents, list) or not all(isinstance(x, str) for x in intents):
            intents = []
        if not isinstance(intake_extracted, dict):
            continue
        validate_instance(intake_extracted, "intake_extracted")

        db.upsert_visit(
            visit_ref=visit_ref,
            patient_ref=patient_ref,
            occurred_at=occurred_at,
            primary_domain=primary_domain,
            intents=intents,
            intake_extracted=intake_extracted,
        )
        visits_loaded += 1

    events_loaded = 0
    for raw in _iter_jsonl_gz(events_path):
        if not isinstance(raw, dict):
            continue
        event_ref = raw.get("event_ref")
        visit_ref = raw.get("visit_ref")
        patient_ref = raw.get("patient_ref")
        occurred_at = raw.get("occurred_at")
        event_type = raw.get("event_type")
        payload = raw.get("payload")
        if not (isinstance(event_ref, str) and event_ref):
            continue
        if not (isinstance(visit_ref, str) and visit_ref):
            continue
        if not (isinstance(patient_ref, str) and patient_ref):
            continue
        if not (isinstance(occurred_at, str) and occurred_at):
            continue
        if not (isinstance(event_type, str) and event_type):
            continue
        if not isinstance(payload, dict):
            payload = {}

        payload_sanitized = _sanitize_event_payload(event_type, payload)
        if payload_sanitized is None:
            continue
        validate_instance(payload_sanitized, "pharmacy_event_payload")

        db.upsert_pharmacy_event(
            event_ref=event_ref,
            visit_ref=visit_ref,
            patient_ref=patient_ref,
            occurred_at=occurred_at,
            event_type=event_type,
            payload=payload_sanitized,
        )
        events_loaded += 1

    inv_loaded = 0
    for raw in _iter_jsonl_gz(inventory_path):
        if not isinstance(raw, dict):
            continue
        sku = raw.get("sku")
        if not (isinstance(sku, str) and sku):
            continue
        validate_instance(raw, "product")
        db.upsert_inventory_product(sku=sku, product=raw)
        inv_loaded += 1

    catalog_loaded = _load_catalog_demo_products(resolve_catalog_demo_path())

    return {
        "loaded": 1,
        "patients_loaded": patients_loaded,
        "visits_loaded": visits_loaded,
        "events_loaded": events_loaded,
        "inventory_loaded": inv_loaded,
        "catalog_loaded": catalog_loaded,
        "patients": db.count_patients(),
        "visits": db.count_visits(),
        "inventory": db.count_inventory(),
    }


def _load_catalog_demo_products(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    if not isinstance(payload, list):
        return 0

    loaded = 0
    for row in payload:
        if not isinstance(row, dict):
            continue
        sku = row.get("sku")
        if not (isinstance(sku, str) and sku.strip()):
            continue
        try:
            validate_instance(row, "product")
        except Exception:
            continue
        db.upsert_inventory_product(sku=sku, product=row)
        loaded += 1
    return loaded
