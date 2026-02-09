from __future__ import annotations

from pharmassist_api.steps.a7_report_composer import compose_report_markdown
from pharmassist_api.steps.a8_handout import compose_handout_markdown


def test_report_and_handout_escape_html_like_content():
    intake_extracted = {
        "schema_version": "0.0.0",
        "presenting_problem": "<script>alert('x')</script>",
        "symptoms": [
            {
                "label": "<img src=x onerror=alert(1)>",
                "severity": "mild",
                "duration_days": 2,
            }
        ],
        "red_flags": [],
    }
    recommendation = {
        "schema_version": "0.0.0",
        "ranked_products": [
            {
                "product_sku": "SKU-0001",
                "score_0_100": 88,
                "why": "<iframe src='https://evil.example'></iframe>",
                "evidence_refs": ["ev_0001"],
            }
        ],
        "safety_warnings": [
            {
                "code": "WARN_HTML",
                "message": "<script>run()</script>",
                "severity": "WARN",
            }
        ],
        "follow_up_questions": [],
        "confidence": 0.6,
    }

    report = compose_report_markdown(
        intake_extracted=intake_extracted,
        recommendation=recommendation,
        evidence_items=[
            {
                "schema_version": "0.0.0",
                "evidence_id": "ev_0001",
                "title": "<b>Safe title</b>",
                "publisher": "<script>evil</script>",
                "url": "https://example.com",
                "published_date": "2024-01-15",
                "summary": "summary",
                "quote_snippet": "snippet",
                "retrieved_at": "2026-02-09T10:00:00Z",
                "license": "example",
                "level_of_evidence": "guideline",
            }
        ],
        language="en",
    )

    handout = compose_handout_markdown(recommendation=recommendation, language="en")

    report_lower = report.lower()
    handout_lower = handout.lower()

    assert "<script" not in report_lower
    assert "<img" not in report_lower
    assert "<iframe" not in report_lower
    assert "<script" not in handout_lower
    assert "<iframe" not in handout_lower
