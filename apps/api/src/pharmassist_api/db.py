from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def repo_root() -> Path:
    # apps/api/src/pharmassist_api/db.py -> repo root
    return Path(__file__).resolve().parents[5]


def db_path() -> Path:
    env = os.getenv("PHARMASSIST_DB_PATH")
    if env:
        return Path(env)
    return repo_root() / ".data" / "pharmassist.db"


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
              run_id TEXT PRIMARY KEY,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              status TEXT NOT NULL,
              input_json TEXT NOT NULL,
              artifacts_json TEXT NOT NULL,
              policy_violations_json TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              run_id TEXT NOT NULL,
              ts TEXT NOT NULL,
              type TEXT NOT NULL,
              data_json TEXT NOT NULL,
              FOREIGN KEY(run_id) REFERENCES runs(run_id)
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_run_id_id ON events(run_id, id);")


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def create_run(run: dict[str, Any]) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO runs(
              run_id,
              created_at,
              updated_at,
              status,
              input_json,
              artifacts_json,
              policy_violations_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run["run_id"],
                run["created_at"],
                run["created_at"],
                run["status"],
                json.dumps(run["input"], ensure_ascii=False, separators=(",", ":")),
                json.dumps(run["artifacts"], ensure_ascii=False, separators=(",", ":")),
                json.dumps(run["policy_violations"], ensure_ascii=False, separators=(",", ":")),
            ),
        )


def get_run(run_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if not row:
            return None
        return {
            "schema_version": "0.0.0",
            "run_id": row["run_id"],
            "created_at": row["created_at"],
            "status": row["status"],
            "input": json.loads(row["input_json"]),
            "artifacts": json.loads(row["artifacts_json"]),
            "policy_violations": json.loads(row["policy_violations_json"]),
        }


def update_run(
    run_id: str,
    *,
    status: str | None = None,
    artifacts: dict[str, Any] | None = None,
    policy_violations: list[dict[str, Any]] | None = None,
) -> None:
    updates: list[str] = ["updated_at = ?"]
    params: list[Any] = [now_iso()]

    if status is not None:
        updates.append("status = ?")
        params.append(status)

    if artifacts is not None:
        updates.append("artifacts_json = ?")
        params.append(json.dumps(artifacts, ensure_ascii=False, separators=(",", ":")))

    if policy_violations is not None:
        updates.append("policy_violations_json = ?")
        params.append(json.dumps(policy_violations, ensure_ascii=False, separators=(",", ":")))

    params.append(run_id)

    with _connect() as conn:
        conn.execute(f"UPDATE runs SET {', '.join(updates)} WHERE run_id = ?", params)


def insert_event(run_id: str, event_type: str, payload: dict[str, Any]) -> int:
    ts = payload.get("ts") or now_iso()
    payload = {**payload, "ts": ts, "type": event_type}

    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO events(run_id, ts, type, data_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                run_id,
                ts,
                event_type,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        return int(cur.lastrowid)


def list_events(run_id: str, *, after_id: int = 0) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, data_json FROM events WHERE run_id = ? AND id > ? ORDER BY id ASC",
            (run_id, after_id),
        ).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        data = json.loads(row["data_json"])
        out.append({"id": int(row["id"]), "data": data})
    return out
