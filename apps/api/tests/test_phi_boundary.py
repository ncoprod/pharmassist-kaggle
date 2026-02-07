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


def test_phi_boundary_blocks_label_like_date_de_naissance_colon():
    with pytest.raises(PhiBoundaryError):
        raise_if_phi("Date de naissance: 2001-01-02", "$.intake_text_ocr")


def test_phi_boundary_blocks_label_like_name_colon():
    with pytest.raises(PhiBoundaryError):
        raise_if_phi("Name: John", "$.intake_text_ocr")


def test_phi_boundary_blocks_label_like_address_colon():
    with pytest.raises(PhiBoundaryError):
        raise_if_phi("Address: 10 Downing Street", "$.intake_text_ocr")


def test_phi_boundary_blocks_label_like_dob_colon():
    with pytest.raises(PhiBoundaryError):
        raise_if_phi("DOB: 01/02/2003", "$.intake_text_ocr")


def test_phi_boundary_blocks_birth_date_without_colon():
    with pytest.raises(PhiBoundaryError):
        raise_if_phi("Date de naissance 2001-01-02", "$.intake_text_ocr")


def test_phi_boundary_blocks_street_address_without_label():
    with pytest.raises(PhiBoundaryError):
        raise_if_phi("15 rue de Vaugirard 75015 Paris", "$.intake_text_ocr")
