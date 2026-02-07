from __future__ import annotations

from pathlib import Path


def test_default_db_path_is_repo_local(monkeypatch):
    monkeypatch.delenv("PHARMASSIST_DB_PATH", raising=False)

    from pharmassist_api import db

    path = db.db_path()
    root = db.repo_root()
    assert path == root / ".data" / "pharmassist.db"
    assert root.name == "pharmassist-kaggle"
    assert (root / "apps" / "api" / "src" / "pharmassist_api").exists()
    assert (root / "packages" / "contracts").exists()

