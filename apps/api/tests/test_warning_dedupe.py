from pharmassist_api.orchestrator import _dedupe_warnings


def test_dedupe_warnings_collapses_same_warning_across_skus():
    warnings = [
        {
            "code": "PREGNANCY_STATUS_UNKNOWN",
            "severity": "WARN",
            "message": "Pregnancy status is unknown. Confirm before recommending if relevant.",
            "related_product_sku": "SKU-0001",
        },
        {
            "code": "PREGNANCY_STATUS_UNKNOWN",
            "severity": "WARN",
            "message": "Pregnancy status is unknown. Confirm before recommending if relevant.",
            "related_product_sku": "SKU-0005",
        },
    ]

    out = _dedupe_warnings(warnings)

    assert len(out) == 1
    assert out[0]["code"] == "PREGNANCY_STATUS_UNKNOWN"
    assert "related_product_sku" not in out[0]


def test_dedupe_warnings_keeps_distinct_messages():
    warnings = [
        {
            "code": "PREGNANCY_STATUS_UNKNOWN",
            "severity": "WARN",
            "message": "Pregnancy status is unknown. Confirm before recommending if relevant.",
            "related_product_sku": "SKU-0001",
        },
        {
            "code": "ESCALATION_RECOMMENDED",
            "severity": "WARN",
            "message": "Escalation recommended.",
        },
    ]

    out = _dedupe_warnings(warnings)

    assert len(out) == 2
