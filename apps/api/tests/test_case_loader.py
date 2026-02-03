from pharmassist_api.cases.load_case import load_case_bundle
from pharmassist_api.contracts.validate_schema import validate_instance


def test_load_case_bundle_has_expected_shape():
    bundle = load_case_bundle("case_000042")

    assert bundle["case_ref"] == "case_000042"

    llm_context = bundle["llm_context"]
    assert validate_instance(llm_context, "llm_context") is None

    intake_text_ocr = bundle["intake_text_ocr"]
    assert isinstance(intake_text_ocr["fr"], str) and intake_text_ocr["fr"].strip()
    assert isinstance(intake_text_ocr["en"], str) and intake_text_ocr["en"].strip()

    for p in bundle["products"]:
        assert validate_instance(p, "product") is None

