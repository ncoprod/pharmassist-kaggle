import json

from pharmassist_api.contracts.load_schema import examples_dir
from pharmassist_api.contracts.validate_schema import validate_instance


def test_contract_examples_validate_against_schemas():
    ex_dir = examples_dir()
    example_files = sorted(ex_dir.glob("*.example.json"))
    assert example_files, "No example files found"

    for path in example_files:
        schema_name = path.name.replace(".example.json", "")
        payload = json.loads(path.read_text(encoding="utf-8"))
        validate_instance(payload, schema_name)

