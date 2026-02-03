import pytest

from pharmassist_api.contracts.validate_schema import SchemaValidationFailed, validate_instance


def test_schema_rejects_invalid_uri_format():
    payload = {
        "schema_version": "0.0.0",
        "evidence_id": "ev_test",
        "title": "t",
        "publisher": "p",
        "url": "not-a-uri",
        "summary": "s",
        "retrieved_at": "2026-02-03T19:30:00Z",
    }
    with pytest.raises(SchemaValidationFailed):
        validate_instance(payload, "evidence_item")


def test_schema_rejects_invalid_datetime_format():
    payload = {
        "schema_version": "0.0.0",
        "run_id": "3f0e3a9d-4d3c-4e4a-8d7e-2f6c2a3f9d1b",
        "created_at": "not-a-datetime",
        "status": "created",
        "input": {"case_ref": "case_000001", "language": "fr", "trigger": "manual"},
        "artifacts": {},
        "policy_violations": [],
    }
    with pytest.raises(SchemaValidationFailed):
        validate_instance(payload, "run")

