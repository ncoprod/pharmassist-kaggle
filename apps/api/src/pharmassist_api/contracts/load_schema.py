from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012


def repo_root() -> Path:
    # apps/api/src/pharmassist_api/contracts/load_schema.py -> repo root
    return Path(__file__).resolve().parents[5]


def schemas_dir() -> Path:
    return repo_root() / "packages" / "contracts" / "schemas"


def examples_dir() -> Path:
    return repo_root() / "packages" / "contracts" / "examples"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_schema_by_name(schema_name: str) -> dict[str, Any]:
    """Load a schema document by its base filename (without `.schema.json`)."""
    path = schemas_dir() / f"{schema_name}.schema.json"
    return _load_json(path)


@lru_cache
def _schemas_by_id() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(schemas_dir().glob("*.schema.json")):
        doc = _load_json(path)
        schema_id = doc.get("$id")
        if not schema_id:
            raise ValueError(f"Schema missing $id: {path}")
        out[str(schema_id)] = doc
    return out


@lru_cache
def schema_registry() -> Registry:
    """Registry for resolving `$ref` across our schema documents."""
    reg = Registry()
    for schema_id, doc in _schemas_by_id().items():
        reg = reg.with_resource(
            schema_id, Resource.from_contents(doc, default_specification=DRAFT202012)
        )
    return reg

