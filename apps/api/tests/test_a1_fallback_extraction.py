from pharmassist_api.cases.load_case import load_case_bundle
from pharmassist_api.contracts.validate_schema import validate_instance
from pharmassist_api.steps.a1_intake_extraction import extract_intake


def test_a1_fallback_extraction_is_schema_valid_for_ocr_suite():
    for seed in (42, 43, 44):
        bundle = load_case_bundle(f"case_{seed:06d}")
        for lang in ("fr", "en"):
            ocr = bundle["intake_text_ocr"][lang]
            out = extract_intake(ocr, lang)  # MedGemma path is off by default
            assert validate_instance(out, "intake_extracted") is None
            assert out["symptoms"], "expected at least one symptom"


def test_a1_canonicalizes_noisy_itchy_eyes_label():
    bundle = load_case_bundle("case_000042")
    out = extract_intake(bundle["intake_text_ocr"]["fr"], "fr")
    labels = [s.get("label") for s in out["symptoms"] if isinstance(s, dict)]
    assert "itchy eyes" in labels


def test_a1_fallback_recovers_sneezing_even_if_ocr_breaks_lines():
    bundle = load_case_bundle("case_000042")
    out = extract_intake(bundle["intake_text_ocr"]["en"], "en")
    labels = [s.get("label") for s in out["symptoms"] if isinstance(s, dict)]
    assert "sneezing" in labels
    assert "itchy eyes" in labels
