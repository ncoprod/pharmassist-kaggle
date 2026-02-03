from pharmassist_api.validators.rx_advice_lint import lint_rx_advice


def test_rx_advice_lint_blocks_change_rx():
    violations = lint_rx_advice(
        "Arretez votre traitement sur ordonnance.",
        path="$.artifacts.report_markdown",
    )
    assert any(v.code == "RX_ADVICE" and v.severity == "BLOCKER" for v in violations)


def test_rx_advice_lint_allows_disclaimer():
    violations = lint_rx_advice(
        "Ne modifiez pas votre traitement sur ordonnance sans avis medical.",
        path="$.artifacts.report_markdown",
    )
    assert not violations


def test_rx_advice_lint_allows_generic_referral():
    violations = lint_rx_advice(
        "If symptoms worsen, consult a doctor.",
        path="$.artifacts.handout_markdown",
    )
    assert not violations

