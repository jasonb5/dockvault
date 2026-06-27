import os
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

MAX_HISTORY_PER_JOB = 20
DEFAULT_HISTORY_DB_PATH = "dockvault-history.sqlite3"

_lock = Lock()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _get_history_db_path() -> Path:
    value = os.getenv("DOCKVAULT_HISTORY_DB_PATH")

    if value is None or not value.strip():
        return Path(DEFAULT_HISTORY_DB_PATH)

    return Path(value.strip())


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS backup_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_name TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            snapshot_id TEXT,
            error TEXT
        )
        """
    )


def _connect() -> sqlite3.Connection:
    db_path = _get_history_db_path()

    if db_path.parent != Path("."):
        db_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(db_path)
    _ensure_schema(connection)

    return connection


def _serialize_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc).isoformat()


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def _row_to_record(row: sqlite3.Row) -> dict:
    return {
        "status": row["status"],
        "started_at": _parse_datetime(row["started_at"]),
        "finished_at": _parse_datetime(row["finished_at"]),
        "snapshot_id": row["snapshot_id"],
        "error": row["error"],
    }


def record_backup_run(
    job_name: str,
    status: str,
    *,
    started_at: datetime,
    finished_at: datetime | None = None,
    snapshot_id: str | None = None,
    error: str | None = None,
) -> dict:
    if finished_at is None:
        finished_at = _utc_now()

    with _lock:
        with _connect() as connection:
            connection.execute(
                """
                INSERT INTO backup_history (
                    job_name,
                    status,
                    started_at,
                    finished_at,
                    snapshot_id,
                    error
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    job_name,
                    status,
                    _serialize_datetime(started_at),
                    _serialize_datetime(finished_at),
                    snapshot_id,
                    error,
                ),
            )
            connection.execute(
                """
                DELETE FROM backup_history
                WHERE job_name = ?
                  AND id NOT IN (
                      SELECT id
                      FROM backup_history
                      WHERE job_name = ?
                      ORDER BY finished_at DESC, id DESC
                      LIMIT ?
                  )
                """,
                (job_name, job_name, MAX_HISTORY_PER_JOB),
            )

    return {
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "snapshot_id": snapshot_id,
        "error": error,
    }


def get_backup_history(job_name: str) -> list[dict]:
    with _lock:
        with _connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT status, started_at, finished_at, snapshot_id, error
                FROM backup_history
                WHERE job_name = ?
                ORDER BY finished_at DESC, id DESC
                """,
                (job_name,),
            ).fetchall()

    return [_row_to_record(row) for row in rows]


def get_last_backup_run(job_name: str) -> dict | None:
    history = get_backup_history(job_name)

    if not history:
        return None

    return history[0]


def clear_backup_history(job_names: Iterable[str] | None = None) -> None:
    with _lock:
        with _connect() as connection:
            if job_names is None:
                connection.execute("DELETE FROM backup_history")
                return

            connection.executemany(
                "DELETE FROM backup_history WHERE job_name = ?",
                ((job_name,) for job_name in job_names),
            )
