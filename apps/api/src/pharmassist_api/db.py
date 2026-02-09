from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def repo_root() -> Path:
    # Locate the repository root robustly from this file location.
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "apps" / "api" / "src" / "pharmassist_api").exists() and (
            parent / "packages" / "contracts"
        ).exists():
            return parent
    # Fallback for unexpected layouts.
    return current.parents[4]


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


def _should_enable_wal() -> bool:
    """Whether to enable WAL journal mode for SQLite connections.

    WAL reduces lock contention for the running API, but it can be flaky on some
    temp directories during unit tests (macOS `tmp_path` cases were observed).
    """
    raw = os.getenv("PHARMASSIST_SQLITE_WAL", "").strip().lower()
    if raw in {"0", "false", "no"}:
        return False
    if raw in {"1", "true", "yes"}:
        return True

    # Pytest sets this env var for each test. Disable WAL for unit tests to
    # improve filesystem portability.
    if os.getenv("PYTEST_CURRENT_TEST"):
        return False

    return True


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
    # Best-effort WAL mode (reduces lock contention). Some filesystems and
    # temp dirs are flaky with WAL, so we disable it under pytest and also
    # keep it best-effort in general.
    if _should_enable_wal():
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS patient_analysis_state (
              patient_ref TEXT PRIMARY KEY,
              status TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              last_run_id TEXT,
              last_error TEXT,
              changed_since_last_analysis INTEGER NOT NULL DEFAULT 0,
              refresh_reason TEXT,
              FOREIGN KEY(patient_ref) REFERENCES patients(patient_ref)
            );
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_patient_analysis_state_status
            ON patient_analysis_state(status, updated_at DESC);
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_audit_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts TEXT NOT NULL,
              endpoint TEXT NOT NULL,
              method TEXT NOT NULL,
              client_ip TEXT NOT NULL,
              action TEXT NOT NULL,
              reason TEXT NOT NULL,
              meta_json TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_admin_audit_events_ts
            ON admin_audit_events(ts DESC, id DESC);
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


def insert_admin_audit_event(
    *,
    endpoint: str,
    method: str,
    client_ip: str,
    action: str,
    reason: str,
    meta: dict[str, Any] | None = None,
) -> None:
    payload = meta if isinstance(meta, dict) else {}
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO admin_audit_events(
              ts,
              endpoint,
              method,
              client_ip,
              action,
              reason,
              meta_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_iso(),
                endpoint.strip(),
                method.strip().upper(),
                client_ip.strip(),
                action.strip().lower(),
                reason.strip().lower(),
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            ),
        )


def list_admin_audit_events(*, limit: int = 200) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, ts, endpoint, method, client_ip, action, reason, meta_json
            FROM admin_audit_events
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "id": int(row["id"]),
                "ts": row["ts"],
                "endpoint": row["endpoint"],
                "method": row["method"],
                "client_ip": row["client_ip"],
                "action": row["action"],
                "reason": row["reason"],
                "meta": _json_load_object(row["meta_json"]),
            }
        )
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


def upsert_document(*, doc_ref: str, metadata: dict[str, Any]) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO documents(doc_ref, metadata_json)
            VALUES(?, ?)
            ON CONFLICT(doc_ref) DO UPDATE SET
              metadata_json = excluded.metadata_json
            """,
            (
                doc_ref,
                json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
            ),
        )


def get_document(doc_ref: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT doc_ref, metadata_json FROM documents WHERE doc_ref = ?",
            (doc_ref,),
        ).fetchone()
    if not row:
        return None
    return {
        "doc_ref": row["doc_ref"],
        "metadata": _json_load_object(row["metadata_json"]),
    }


def set_patient_analysis_state(
    *,
    patient_ref: str,
    status: str,
    last_run_id: str | None = None,
    last_error: str | None = None,
    changed_since_last_analysis: bool | None = None,
    refresh_reason: str | None = None,
) -> None:
    updates: list[str] = ["status = excluded.status", "updated_at = excluded.updated_at"]
    if last_run_id is not None:
        updates.append("last_run_id = excluded.last_run_id")
    if last_error is not None:
        updates.append("last_error = excluded.last_error")
    if changed_since_last_analysis is not None:
        updates.append("changed_since_last_analysis = excluded.changed_since_last_analysis")
    if refresh_reason is not None:
        updates.append("refresh_reason = excluded.refresh_reason")

    with _connect() as conn:
        conn.execute(
            f"""
            INSERT INTO patient_analysis_state(
              patient_ref,
              status,
              updated_at,
              last_run_id,
              last_error,
              changed_since_last_analysis,
              refresh_reason
            )
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(patient_ref) DO UPDATE SET
              {", ".join(updates)}
            """,
            (
                patient_ref,
                status,
                now_iso(),
                last_run_id,
                last_error,
                1 if changed_since_last_analysis else 0,
                refresh_reason,
            ),
        )


def get_patient_analysis_state(patient_ref: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT
              patient_ref,
              status,
              updated_at,
              last_run_id,
              last_error,
              changed_since_last_analysis,
              refresh_reason
            FROM patient_analysis_state
            WHERE patient_ref = ?
            """,
            (patient_ref,),
        ).fetchone()
    if not row:
        return None
    return {
        "patient_ref": row["patient_ref"],
        "status": row["status"],
        "updated_at": row["updated_at"],
        "last_run_id": row["last_run_id"],
        "last_error": row["last_error"],
        "changed_since_last_analysis": bool(int(row["changed_since_last_analysis"] or 0)),
        "refresh_reason": row["refresh_reason"],
    }


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


def count_documents() -> int:
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(1) AS c FROM documents").fetchone()
        return int(row["c"]) if row else 0


def list_patient_refs_with_visits(*, limit: int | None = 200) -> list[str]:
    with _connect() as conn:
        if limit is None:
            rows = conn.execute(
                """
                SELECT patient_ref
                FROM visits
                GROUP BY patient_ref
                ORDER BY MAX(occurred_at) DESC, patient_ref ASC
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT patient_ref
                FROM visits
                GROUP BY patient_ref
                ORDER BY MAX(occurred_at) DESC, patient_ref ASC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
    return [str(r["patient_ref"]) for r in rows if isinstance(r["patient_ref"], str)]


def get_latest_patient_visit(*, patient_ref: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT visit_ref, occurred_at, primary_domain
            FROM visits
            WHERE patient_ref = ?
            ORDER BY occurred_at DESC, visit_ref DESC
            LIMIT 1
            """,
            (patient_ref,),
        ).fetchone()
    if not row:
        return None
    return {
        "visit_ref": row["visit_ref"],
        "occurred_at": row["occurred_at"],
        "primary_domain": row["primary_domain"],
    }


def get_latest_run_for_patient(
    *,
    patient_ref: str,
    trigger: str | None = None,
    status: str | None = None,
) -> dict[str, Any] | None:
    conditions = ["json_extract(input_json, '$.patient_ref') = ?"]
    params: list[Any] = [patient_ref]
    if isinstance(trigger, str) and trigger.strip():
        conditions.append("json_extract(input_json, '$.trigger') = ?")
        params.append(trigger.strip())
    if isinstance(status, str) and status.strip():
        conditions.append("status = ?")
        params.append(status.strip())

    where = " AND ".join(conditions)
    with _connect() as conn:
        row = conn.execute(
            f"""
            SELECT run_id, created_at, status, input_json
            FROM runs
            WHERE {where}
            ORDER BY created_at DESC, run_id DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
    if not row:
        return None
    input_payload = _json_load_object(row["input_json"])
    visit_ref = input_payload.get("visit_ref")
    language = input_payload.get("language")
    return {
        "run_id": row["run_id"],
        "created_at": row["created_at"],
        "status": row["status"],
        "visit_ref": visit_ref if isinstance(visit_ref, str) else None,
        "language": language if isinstance(language, str) else None,
    }


_DB_PREVIEW_TABLES = (
    "runs",
    "run_events",
    "patients",
    "visits",
    "events",
    "inventory",
    "documents",
    "patient_analysis_state",
)
_DB_PREVIEW_LIMIT_MAX = 100


def _json_load_object(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _json_load_list(raw: str) -> list[Any]:
    try:
        value = json.loads(raw)
    except Exception:
        return []
    return value if isinstance(value, list) else []


def list_db_preview_tables() -> list[str]:
    return list(_DB_PREVIEW_TABLES)


def preview_db_table(*, table: str, query: str = "", limit: int = 50) -> dict[str, Any]:
    table_norm = (table or "").strip().lower()
    if table_norm not in _DB_PREVIEW_TABLES:
        raise ValueError(f"Unsupported table for preview: {table_norm}")

    query_norm = (query or "").strip()
    limit_norm = max(1, min(int(limit), _DB_PREVIEW_LIMIT_MAX))

    with _connect() as conn:
        if table_norm == "runs":
            columns, rows, count = _preview_runs(conn, query_norm, limit_norm)
        elif table_norm == "run_events":
            columns, rows, count = _preview_run_events(conn, query_norm, limit_norm)
        elif table_norm == "patients":
            columns, rows, count = _preview_patients(conn, query_norm, limit_norm)
        elif table_norm == "visits":
            columns, rows, count = _preview_visits(conn, query_norm, limit_norm)
        elif table_norm == "events":
            columns, rows, count = _preview_events(conn, query_norm, limit_norm)
        elif table_norm == "inventory":
            columns, rows, count = _preview_inventory(conn, query_norm, limit_norm)
        elif table_norm == "patient_analysis_state":
            columns, rows, count = _preview_patient_analysis_state(conn, query_norm, limit_norm)
        else:
            columns, rows, count = _preview_documents(conn, query_norm, limit_norm)

    return {
        "schema_version": "0.0.0",
        "table": table_norm,
        "query": query_norm,
        "limit": limit_norm,
        "count": int(count),
        "redacted": True,
        "columns": columns,
        "rows": rows,
    }


def _preview_runs(
    conn: sqlite3.Connection, query: str, limit: int
) -> tuple[list[str], list[dict[str, Any]], int]:
    where = ""
    params: tuple[Any, ...] = ()
    if query:
        where = "WHERE run_id LIKE ?"
        params = (f"{query}%",)

    count = conn.execute(f"SELECT COUNT(1) AS c FROM runs {where}", params).fetchone()
    rows = conn.execute(
        f"""
        SELECT run_id, created_at, status, input_json, artifacts_json, policy_violations_json
        FROM runs
        {where}
        ORDER BY created_at DESC, run_id DESC
        LIMIT ?
        """,
        (*params, int(limit)),
    ).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        input_obj = _json_load_object(row["input_json"])
        artifacts_obj = _json_load_object(row["artifacts_json"])
        policy_violations = _json_load_list(row["policy_violations_json"])
        recommendation = artifacts_obj.get("recommendation")
        follow_up_questions = (
            recommendation.get("follow_up_questions")
            if isinstance(recommendation, dict)
            else []
        )
        out.append(
            {
                "run_id": row["run_id"],
                "created_at": row["created_at"],
                "status": row["status"],
                "language": str(input_obj.get("language") or ""),
                "case_ref": str(input_obj.get("case_ref") or ""),
                "patient_ref": str(input_obj.get("patient_ref") or ""),
                "visit_ref": str(input_obj.get("visit_ref") or ""),
                "follow_up_questions_count": len(follow_up_questions)
                if isinstance(follow_up_questions, list)
                else 0,
                "has_report": isinstance(artifacts_obj.get("report_markdown"), str),
                "has_handout": isinstance(artifacts_obj.get("handout_markdown"), str),
                "has_trace": isinstance(artifacts_obj.get("trace"), dict),
                "policy_violations_count": len(policy_violations),
            }
        )

    return (
        [
            "run_id",
            "created_at",
            "status",
            "language",
            "case_ref",
            "patient_ref",
            "visit_ref",
            "follow_up_questions_count",
            "has_report",
            "has_handout",
            "has_trace",
            "policy_violations_count",
        ],
        out,
        int(count["c"]) if count else 0,
    )


def _preview_run_events(
    conn: sqlite3.Connection, query: str, limit: int
) -> tuple[list[str], list[dict[str, Any]], int]:
    where = ""
    params: tuple[Any, ...] = ()
    if query:
        where = "WHERE run_id LIKE ? OR type LIKE ?"
        params = (f"{query}%", f"{query}%")

    count = conn.execute(f"SELECT COUNT(1) AS c FROM run_events {where}", params).fetchone()
    rows = conn.execute(
        f"""
        SELECT id, run_id, ts, type, data_json
        FROM run_events
        {where}
        ORDER BY id DESC
        LIMIT ?
        """,
        (*params, int(limit)),
    ).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        data = _json_load_object(row["data_json"])
        out.append(
            {
                "id": int(row["id"]),
                "run_id": row["run_id"],
                "ts": row["ts"],
                "type": row["type"],
                "step": str(data.get("step") or ""),
                "message": str(data.get("message") or ""),
                "tool_name": str(data.get("tool_name") or ""),
                "result_summary": str(data.get("result_summary") or ""),
                "rule_id": str(data.get("rule_id") or ""),
                "severity": str(data.get("severity") or ""),
            }
        )

    return (
        [
            "id",
            "run_id",
            "ts",
            "type",
            "step",
            "message",
            "tool_name",
            "result_summary",
            "rule_id",
            "severity",
        ],
        out,
        int(count["c"]) if count else 0,
    )


def _preview_patients(
    conn: sqlite3.Connection, query: str, limit: int
) -> tuple[list[str], list[dict[str, Any]], int]:
    where = ""
    params: tuple[Any, ...] = ()
    if query:
        where = "WHERE patient_ref LIKE ?"
        params = (f"{query}%",)

    count = conn.execute(f"SELECT COUNT(1) AS c FROM patients {where}", params).fetchone()
    rows = conn.execute(
        f"""
        SELECT patient_ref, llm_context_json
        FROM patients
        {where}
        ORDER BY patient_ref ASC
        LIMIT ?
        """,
        (*params, int(limit)),
    ).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        llm_context = _json_load_object(row["llm_context_json"])
        demo = llm_context.get("demographics") if isinstance(llm_context, dict) else {}
        allergies = llm_context.get("allergies") if isinstance(llm_context, dict) else []
        conditions = llm_context.get("conditions") if isinstance(llm_context, dict) else []
        current_meds = (
            llm_context.get("current_medications") if isinstance(llm_context, dict) else []
        )
        out.append(
            {
                "patient_ref": row["patient_ref"],
                "age_years": int(demo.get("age_years"))
                if isinstance(demo, dict) and isinstance(demo.get("age_years"), int)
                else None,
                "sex": str(demo.get("sex") or "") if isinstance(demo, dict) else "",
                "allergies_count": len(allergies) if isinstance(allergies, list) else 0,
                "conditions_count": len(conditions) if isinstance(conditions, list) else 0,
                "current_medications_count": len(current_meds)
                if isinstance(current_meds, list)
                else 0,
            }
        )

    return (
        [
            "patient_ref",
            "age_years",
            "sex",
            "allergies_count",
            "conditions_count",
            "current_medications_count",
        ],
        out,
        int(count["c"]) if count else 0,
    )


def _preview_visits(
    conn: sqlite3.Connection, query: str, limit: int
) -> tuple[list[str], list[dict[str, Any]], int]:
    where = ""
    params: tuple[Any, ...] = ()
    if query:
        where = "WHERE visit_ref LIKE ? OR patient_ref LIKE ?"
        params = (f"{query}%", f"{query}%")

    count = conn.execute(f"SELECT COUNT(1) AS c FROM visits {where}", params).fetchone()
    rows = conn.execute(
        f"""
        SELECT
            visit_ref,
            patient_ref,
            occurred_at,
            primary_domain,
            intents_json,
            intake_extracted_json
        FROM visits
        {where}
        ORDER BY occurred_at DESC, visit_ref DESC
        LIMIT ?
        """,
        (*params, int(limit)),
    ).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        intents = _json_load_list(row["intents_json"])
        intake = _json_load_object(row["intake_extracted_json"])
        presenting_problem = intake.get("presenting_problem")
        out.append(
            {
                "visit_ref": row["visit_ref"],
                "patient_ref": row["patient_ref"],
                "occurred_at": row["occurred_at"],
                "primary_domain": row["primary_domain"] or "",
                "intents_count": len(intents),
                "presenting_problem": presenting_problem
                if isinstance(presenting_problem, str)
                else "",
            }
        )

    return (
        [
            "visit_ref",
            "patient_ref",
            "occurred_at",
            "primary_domain",
            "intents_count",
            "presenting_problem",
        ],
        out,
        int(count["c"]) if count else 0,
    )


def _preview_events(
    conn: sqlite3.Connection, query: str, limit: int
) -> tuple[list[str], list[dict[str, Any]], int]:
    where = ""
    params: tuple[Any, ...] = ()
    if query:
        where = (
            "WHERE event_ref LIKE ? OR visit_ref LIKE ? OR "
            "patient_ref LIKE ? OR event_type LIKE ?"
        )
        params = (f"{query}%", f"{query}%", f"{query}%", f"{query}%")

    count = conn.execute(f"SELECT COUNT(1) AS c FROM events {where}", params).fetchone()
    rows = conn.execute(
        f"""
        SELECT event_ref, visit_ref, patient_ref, occurred_at, event_type, payload_json
        FROM events
        {where}
        ORDER BY occurred_at DESC, event_ref DESC
        LIMIT ?
        """,
        (*params, int(limit)),
    ).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        payload = _json_load_object(row["payload_json"])
        payload_keys = sorted([k for k in payload.keys() if isinstance(k, str)])[:20]
        out.append(
            {
                "event_ref": row["event_ref"],
                "visit_ref": row["visit_ref"],
                "patient_ref": row["patient_ref"],
                "occurred_at": row["occurred_at"],
                "event_type": row["event_type"],
                "payload_keys": payload_keys,
            }
        )

    return (
        ["event_ref", "visit_ref", "patient_ref", "occurred_at", "event_type", "payload_keys"],
        out,
        int(count["c"]) if count else 0,
    )


def _preview_inventory(
    conn: sqlite3.Connection, query: str, limit: int
) -> tuple[list[str], list[dict[str, Any]], int]:
    where = ""
    params: tuple[Any, ...] = ()
    if query:
        where = "WHERE sku LIKE ?"
        params = (f"{query}%",)

    count = conn.execute(f"SELECT COUNT(1) AS c FROM inventory {where}", params).fetchone()
    rows = conn.execute(
        f"""
        SELECT sku, product_json
        FROM inventory
        {where}
        ORDER BY sku ASC
        LIMIT ?
        """,
        (*params, int(limit)),
    ).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        product = _json_load_object(row["product_json"])
        out.append(
            {
                "sku": row["sku"],
                "category": str(product.get("category") or ""),
                "in_stock": bool(product.get("in_stock")),
                "stock_qty": int(product.get("stock_qty"))
                if isinstance(product.get("stock_qty"), int)
                else 0,
                "price_eur": float(product.get("price_eur"))
                if isinstance(product.get("price_eur"), int | float)
                else None,
            }
        )

    return (
        ["sku", "category", "in_stock", "stock_qty", "price_eur"],
        out,
        int(count["c"]) if count else 0,
    )


def _preview_documents(
    conn: sqlite3.Connection, query: str, limit: int
) -> tuple[list[str], list[dict[str, Any]], int]:
    where = ""
    params: tuple[Any, ...] = ()
    if query:
        where = "WHERE doc_ref LIKE ?"
        params = (f"{query}%",)

    count = conn.execute(f"SELECT COUNT(1) AS c FROM documents {where}", params).fetchone()
    rows = conn.execute(
        f"""
        SELECT doc_ref, metadata_json
        FROM documents
        {where}
        ORDER BY doc_ref ASC
        LIMIT ?
        """,
        (*params, int(limit)),
    ).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        metadata = _json_load_object(row["metadata_json"])
        keys = sorted([k for k in metadata.keys() if isinstance(k, str)])[:20]
        out.append({"doc_ref": row["doc_ref"], "metadata_keys": keys})

    return (
        ["doc_ref", "metadata_keys"],
        out,
        int(count["c"]) if count else 0,
    )


def _preview_patient_analysis_state(
    conn: sqlite3.Connection, query: str, limit: int
) -> tuple[list[str], list[dict[str, Any]], int]:
    where = ""
    params: tuple[Any, ...] = ()
    if query:
        where = "WHERE patient_ref LIKE ?"
        params = (f"{query}%",)

    count = conn.execute(
        f"SELECT COUNT(1) AS c FROM patient_analysis_state {where}",
        params,
    ).fetchone()
    rows = conn.execute(
        f"""
        SELECT
          patient_ref,
          status,
          updated_at,
          last_run_id,
          changed_since_last_analysis,
          refresh_reason
        FROM patient_analysis_state
        {where}
        ORDER BY updated_at DESC, patient_ref ASC
        LIMIT ?
        """,
        (*params, int(limit)),
    ).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "patient_ref": row["patient_ref"],
                "status": row["status"],
                "updated_at": row["updated_at"],
                "last_run_id": row["last_run_id"] or "",
                "changed_since_last_analysis": bool(int(row["changed_since_last_analysis"] or 0)),
                "refresh_reason": row["refresh_reason"] or "",
            }
        )

    return (
        [
            "patient_ref",
            "status",
            "updated_at",
            "last_run_id",
            "changed_since_last_analysis",
            "refresh_reason",
        ],
        out,
        int(count["c"]) if count else 0,
    )
