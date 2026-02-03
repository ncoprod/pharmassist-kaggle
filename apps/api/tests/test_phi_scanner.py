from pharmassist_api.validators.phi_scanner import scan_for_phi


def test_phi_scanner_detects_forbidden_keys():
    violations = scan_for_phi({"first_name": "John"})
    assert any(v.code == "PHI_KEY" and v.severity == "BLOCKER" for v in violations)


def test_phi_scanner_detects_email():
    violations = scan_for_phi({"note": "contact me at test@example.com"})
    assert any(v.code == "PHI_EMAIL" and v.severity == "BLOCKER" for v in violations)


def test_phi_scanner_detects_french_phone():
    violations = scan_for_phi({"note": "Call 06 12 34 56 78"})
    assert any(v.code == "PHI_PHONE_FR" and v.severity == "BLOCKER" for v in violations)


def test_phi_scanner_detects_nir_like():
    violations = scan_for_phi({"note": "NIR 2 84 01 75 123 456 78"})
    assert any(v.code == "PHI_NIR" and v.severity == "BLOCKER" for v in violations)


def test_phi_scanner_warns_on_standalone_postal_code():
    violations = scan_for_phi({"note": "75015"})
    assert any(v.code == "PHI_POSTAL_CODE" and v.severity == "WARN" for v in violations)


def test_phi_scanner_does_not_flag_paris_15e_text():
    violations = scan_for_phi({"note": "Paris 15e"})
    assert not violations
