from collections import defaultdict, deque
from collections.abc import Iterable
from datetime import datetime, timezone
from threading import Lock

MAX_HISTORY_PER_JOB = 20

_history: dict[str, deque[dict]] = defaultdict(lambda: deque(maxlen=MAX_HISTORY_PER_JOB))
_lock = Lock()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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

    record = {
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "snapshot_id": snapshot_id,
        "error": error,
    }

    with _lock:
        _history[job_name].appendleft(record)

    return record


def get_backup_history(job_name: str) -> list[dict]:
    with _lock:
        return list(_history.get(job_name, ()))


def get_last_backup_run(job_name: str) -> dict | None:
    history = get_backup_history(job_name)

    if not history:
        return None

    return history[0]


def clear_backup_history(job_names: Iterable[str] | None = None) -> None:
    with _lock:
        if job_names is None:
            _history.clear()
            return

        for job_name in job_names:
            _history.pop(job_name, None)
