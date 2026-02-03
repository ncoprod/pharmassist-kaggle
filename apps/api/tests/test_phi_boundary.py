import pytest

from pharmassist_api.privacy.phi_boundary import PhiBoundaryError, raise_if_phi, scan_text


def test_phi_boundary_blocks_email():
    with pytest.raises(PhiBoundaryError):
        raise_if_phi("contact: test@example.com", "$.intake_text_ocr")


def test_phi_boundary_blocks_french_phone():
    with pytest.raises(PhiBoundaryError):
        raise_if_phi("tel: 06 12 34 56 78", "$.intake_text_ocr")


def test_phi_boundary_blocks_nir_like():
    with pytest.raises(PhiBoundaryError):
        raise_if_phi("NIR 2 84 01 75 123 456 78", "$.intake_text_ocr")


def test_phi_boundary_warn_postal_code_does_not_raise():
    violations = scan_text("75015", "$.intake_text_ocr")
    assert any(v.code == "PHI_POSTAL_CODE" and v.severity == "WARN" for v in violations)
    raise_if_phi("75015", "$.intake_text_ocr")  # should not raise


def test_phi_boundary_allows_paris_15e():
    raise_if_phi("Paris 15e", "$.intake_text_ocr")


def test_phi_boundary_blocks_label_like_nom_colon():
    with pytest.raises(PhiBoundaryError):
        raise_if_phi("Nom: Martin", "$.intake_text_ocr")

