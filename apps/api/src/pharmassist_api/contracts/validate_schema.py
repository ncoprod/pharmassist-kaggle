from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from .load_schema import load_schema_by_name, schema_registry


@dataclass(frozen=True)
class SchemaValidationIssue:
    json_path: str
    message: str


class SchemaValidationFailed(Exception):
    def __init__(self, schema_name: str, issues: list[SchemaValidationIssue]):
        super().__init__(f"Schema validation failed for {schema_name}: {len(issues)} issue(s)")
        self.schema_name = schema_name
        self.issues = issues


def validate_instance(instance: Any, schema_name: str) -> None:
    """Validate an instance against a named schema (raises on error)."""
    schema = load_schema_by_name(schema_name)
    validator = Draft202012Validator(schema, registry=schema_registry())

    issues: list[SchemaValidationIssue] = []
    for err in sorted(validator.iter_errors(instance), key=lambda e: str(e.json_path)):
        issues.append(SchemaValidationIssue(json_path=str(err.json_path), message=err.message))

    if issues:
        raise SchemaValidationFailed(schema_name=schema_name, issues=issues)


def validate_or_return_errors(instance: Any, schema_name: str) -> list[SchemaValidationIssue]:
    try:
        validate_instance(instance, schema_name)
    except SchemaValidationFailed as e:
        return e.issues
    except ValidationError as e:
        return [SchemaValidationIssue(json_path=str(e.json_path), message=e.message)]
    return []

