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

def _ensure_parent_dir(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        # If this fails, sqlite will raise a more actionable OperationalError below.
        pass


def _connect() -> sqlite3.Connection:
    path = db_path()
    _ensure_parent_dir(path)

    # sqlite3.connect accepts PathLike, but convert explicitly to str to avoid
    # platform-specific edge cases (observed flakiness on some temp paths).
    #
    # We also retry once: some environments can delete temp dirs during test runs
    # (e.g. concurrent pytest sessions). Retrying after re-creating the parent dir
    # makes tests and local dev more robust.
    last_err: sqlite3.OperationalError | None = None
    conn: sqlite3.Connection | None = None
    for _attempt in range(2):
        try:
            conn = sqlite3.connect(str(path), check_same_thread=False)
            break
        except sqlite3.OperationalError as e:
            last_err = e
            _ensure_parent_dir(path)
            conn = None
    if conn is None:
        assert last_err is not None
        raise last_err

    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    # Best-effort WAL mode (reduces lock contention). Some temp dirs / FS setups
    # may not support WAL reliably; fall back silently.
    try:
        conn.execute("PRAGMA journal_mode = WAL;")
    except sqlite3.OperationalError:
        pass
    # Avoid transient "database is locked" errors under light concurrency.
    conn.execute("PRAGMA busy_timeout = 5000;")
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

        # Migration: older versions stored run SSE events in a table named `events`.
        # We now reserve `events` for the pharmacy dataset and store run events in `run_events`.
        has_events = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='events'"
        ).fetchone()
        has_run_events = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='run_events'"
        ).fetchone()
        if has_events and not has_run_events:
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(events)").fetchall()]
            if {"run_id", "data_json"} <= set(cols):
                conn.execute("ALTER TABLE events RENAME TO run_events;")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS run_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              run_id TEXT NOT NULL,
              ts TEXT NOT NULL,
              type TEXT NOT NULL,
              data_json TEXT NOT NULL,
              FOREIGN KEY(run_id) REFERENCES runs(run_id)
            );
            """
        )
        # Drop any legacy index name that might conflict with dataset tables.
        conn.execute("DROP INDEX IF EXISTS idx_events_run_id_id;")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_run_events_run_id_id ON run_events(run_id, id);"
        )

        # Synthetic pharmacy dataset tables (Feb 6 step-up).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS patients (
              patient_ref TEXT PRIMARY KEY,
              llm_context_json TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS visits (
              visit_ref TEXT PRIMARY KEY,
              patient_ref TEXT NOT NULL,
              occurred_at TEXT NOT NULL,
              primary_domain TEXT,
              intents_json TEXT NOT NULL,
              intake_extracted_json TEXT NOT NULL,
              FOREIGN KEY(patient_ref) REFERENCES patients(patient_ref)
            );
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_visits_patient_ref_occurred_at "
            "ON visits(patient_ref, occurred_at);"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
              event_ref TEXT PRIMARY KEY,
              visit_ref TEXT NOT NULL,
              patient_ref TEXT NOT NULL,
              occurred_at TEXT NOT NULL,
              event_type TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              FOREIGN KEY(visit_ref) REFERENCES visits(visit_ref),
              FOREIGN KEY(patient_ref) REFERENCES patients(patient_ref)
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_visit_ref ON events(visit_ref);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_patient_ref ON events(patient_ref);")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS inventory (
              sku TEXT PRIMARY KEY,
              product_json TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
              doc_ref TEXT PRIMARY KEY,
              metadata_json TEXT NOT NULL
            );
            """
        )


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
            INSERT INTO run_events(run_id, ts, type, data_json)
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
            "SELECT id, data_json FROM run_events WHERE run_id = ? AND id > ? ORDER BY id ASC",
            (run_id, after_id),
        ).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        data = json.loads(row["data_json"])
        out.append({"id": int(row["id"]), "data": data})
    return out


def upsert_patient(*, patient_ref: str, llm_context: dict[str, Any]) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO patients(patient_ref, llm_context_json)
            VALUES(?, ?)
            ON CONFLICT(patient_ref) DO UPDATE SET
              llm_context_json = excluded.llm_context_json
            """,
            (
                patient_ref,
                json.dumps(llm_context, ensure_ascii=False, separators=(",", ":")),
            ),
        )


def get_patient(patient_ref: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT patient_ref, llm_context_json FROM patients WHERE patient_ref = ?",
            (patient_ref,),
        ).fetchone()
        if not row:
            return None
        return {
            "patient_ref": row["patient_ref"],
            "llm_context": json.loads(row["llm_context_json"]),
        }


def search_patients(*, query_prefix: str, limit: int = 20) -> list[dict[str, Any]]:
    q = (query_prefix or "").strip()
    if not q:
        return []

    like = f"{q}%"
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT patient_ref, llm_context_json
            FROM patients
            WHERE patient_ref LIKE ?
            ORDER BY patient_ref ASC
            LIMIT ?
            """,
            (like, int(limit)),
        ).fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        llm_context = json.loads(r["llm_context_json"])
        demo = llm_context.get("demographics") if isinstance(llm_context, dict) else None
        out.append(
            {
                "patient_ref": r["patient_ref"],
                "demographics": demo if isinstance(demo, dict) else {},
            }
        )
    return out


def upsert_visit(
    *,
    visit_ref: str,
    patient_ref: str,
    occurred_at: str,
    primary_domain: str | None,
    intents: list[str],
    intake_extracted: dict[str, Any],
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO visits(
              visit_ref,
              patient_ref,
              occurred_at,
              primary_domain,
              intents_json,
              intake_extracted_json
            )
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(visit_ref) DO UPDATE SET
              patient_ref = excluded.patient_ref,
              occurred_at = excluded.occurred_at,
              primary_domain = excluded.primary_domain,
              intents_json = excluded.intents_json,
              intake_extracted_json = excluded.intake_extracted_json
            """,
            (
                visit_ref,
                patient_ref,
                occurred_at,
                primary_domain,
                json.dumps(intents, ensure_ascii=False, separators=(",", ":")),
                json.dumps(intake_extracted, ensure_ascii=False, separators=(",", ":")),
            ),
        )


def get_visit(visit_ref: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT
              visit_ref,
              patient_ref,
              occurred_at,
              primary_domain,
              intents_json,
              intake_extracted_json
            FROM visits
            WHERE visit_ref = ?
            """,
            (visit_ref,),
        ).fetchone()
        if not row:
            return None
        return {
            "visit_ref": row["visit_ref"],
            "patient_ref": row["patient_ref"],
            "occurred_at": row["occurred_at"],
            "primary_domain": row["primary_domain"],
            "intents": json.loads(row["intents_json"]),
            "intake_extracted": json.loads(row["intake_extracted_json"]),
        }


def list_patient_visits(*, patient_ref: str, limit: int = 50) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT
              visit_ref,
              patient_ref,
              occurred_at,
              primary_domain,
              intents_json,
              intake_extracted_json
            FROM visits
            WHERE patient_ref = ?
            ORDER BY occurred_at DESC, visit_ref DESC
            LIMIT ?
            """,
            (patient_ref, int(limit)),
        ).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        intake = json.loads(row["intake_extracted_json"])
        presenting = intake.get("presenting_problem") if isinstance(intake, dict) else None
        out.append(
            {
                "visit_ref": row["visit_ref"],
                "occurred_at": row["occurred_at"],
                "primary_domain": row["primary_domain"],
                "intents": json.loads(row["intents_json"]),
                "presenting_problem": presenting if isinstance(presenting, str) else "",
            }
        )
    return out


def upsert_pharmacy_event(
    *,
    event_ref: str,
    visit_ref: str,
    patient_ref: str,
    occurred_at: str,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO events(
              event_ref,
              visit_ref,
              patient_ref,
              occurred_at,
              event_type,
              payload_json
            )
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_ref) DO UPDATE SET
              visit_ref = excluded.visit_ref,
              patient_ref = excluded.patient_ref,
              occurred_at = excluded.occurred_at,
              event_type = excluded.event_type,
              payload_json = excluded.payload_json
            """,
            (
                event_ref,
                visit_ref,
                patient_ref,
                occurred_at,
                event_type,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            ),
        )


def upsert_inventory_product(*, sku: str, product: dict[str, Any]) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO inventory(sku, product_json)
            VALUES(?, ?)
            ON CONFLICT(sku) DO UPDATE SET
              product_json = excluded.product_json
            """,
            (
                sku,
                json.dumps(product, ensure_ascii=False, separators=(",", ":")),
            ),
        )


def list_inventory(*, limit: int | None = None) -> list[dict[str, Any]]:
    sql = "SELECT product_json FROM inventory ORDER BY sku ASC"
    params: tuple[Any, ...] = ()
    if isinstance(limit, int):
        sql += " LIMIT ?"
        params = (limit,)

    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [json.loads(r["product_json"]) for r in rows]


def count_patients() -> int:
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(1) AS c FROM patients").fetchone()
        return int(row["c"]) if row else 0


def count_visits() -> int:
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(1) AS c FROM visits").fetchone()
        return int(row["c"]) if row else 0


def count_inventory() -> int:
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(1) AS c FROM inventory").fetchone()
        return int(row["c"]) if row else 0
