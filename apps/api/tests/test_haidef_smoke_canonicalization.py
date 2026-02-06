from pharmassist_api.contracts.validate_schema import validate_instance
from pharmassist_api.scripts.haidef_smoke import _canonicalize_payload


def test_haidef_smoke_canonicalizes_noisy_model_labels():
    payload = {
        "schema_version": "0.0.0",
        "presenting_problem": "Unspecified symptoms",
        "symptoms": [
            {
                "label": "snee zing",
                "severity": "mild",
                "duration_days": 7,
            },
            {
                "label": "itchy 3ye5",
                "severity": "mild",
                "duration_days": 7,
            },
        ],
        "red_flags": [],
    }

    out = _canonicalize_payload(payload, "en")
    labels = [s.get("label") for s in out["symptoms"] if isinstance(s, dict)]
    assert "sneezing" in labels
    assert "itchy eyes" in labels
    assert validate_instance(out, "intake_extracted") is None
