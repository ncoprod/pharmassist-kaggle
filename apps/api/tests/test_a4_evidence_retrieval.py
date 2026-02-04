from pharmassist_api.cases.load_case import load_case_bundle
from pharmassist_api.contracts.validate_schema import validate_instance
from pharmassist_api.steps.a4_evidence_retrieval import retrieve_evidence


def test_evidence_retrieval_returns_schema_valid_items():
    bundle = load_case_bundle("case_000042")

    items = retrieve_evidence(
        intake_extracted=bundle["intake_extracted"],
        llm_context=bundle["llm_context"],
        k=5,
    )
    assert len(items) == 5

    for ev in items:
        validate_instance(ev, "evidence_item")

    # Deterministic ordering.
    items2 = retrieve_evidence(
        intake_extracted=bundle["intake_extracted"],
        llm_context=bundle["llm_context"],
        k=5,
    )
    assert [e["evidence_id"] for e in items] == [e["evidence_id"] for e in items2]

