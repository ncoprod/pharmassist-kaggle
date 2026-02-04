import pytest

from pharmassist_api.cases.load_case import load_case_bundle
from pharmassist_api.contracts.validate_schema import validate_instance


@pytest.mark.parametrize(
    "case_ref",
    ["case_000042", "case_redflag_000101", "case_lowinfo_000102"],
)
def test_load_case_bundle_has_expected_shape(case_ref: str):
    bundle = load_case_bundle(case_ref)

    assert bundle["case_ref"] == case_ref

    llm_context = bundle["llm_context"]
    assert validate_instance(llm_context, "llm_context") is None

    intake_text_ocr = bundle["intake_text_ocr"]
    assert isinstance(intake_text_ocr["fr"], str) and intake_text_ocr["fr"].strip()
    assert isinstance(intake_text_ocr["en"], str) and intake_text_ocr["en"].strip()

    for p in bundle["products"]:
        assert validate_instance(p, "product") is None
