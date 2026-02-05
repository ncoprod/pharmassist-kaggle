import json

from pharmassist_api.contracts.load_schema import examples_dir
from pharmassist_api.validators.policy_validate import validate_payload


def test_policy_validate_lints_rx_advice_in_recommendation_strings():
    run = json.loads((examples_dir() / "run.example.json").read_text(encoding="utf-8"))
    run["artifacts"]["recommendation"]["ranked_products"][0]["why"] = (
        "Stop taking your prescription medication."
    )

    violations = validate_payload(run, schema_name="run")
    assert any(
        v.code == "RX_ADVICE"
        and v.severity == "BLOCKER"
        and v.json_path == "$.artifacts.recommendation.ranked_products[0].why"
        for v in violations
    )

