from datetime import datetime, timedelta, timezone

from dockvault import history


def test_recorded_history_is_persisted_to_sqlite(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DOCKVAULT_HISTORY_DB_PATH", str(tmp_path / "history.sqlite3"))

    started_at = datetime(2026, 6, 27, 1, 0, tzinfo=timezone.utc)
    finished_at = started_at + timedelta(minutes=1)

    history.record_backup_run(
        "alpha",
        "succeeded",
        started_at=started_at,
        finished_at=finished_at,
        snapshot_id="snap-123",
    )

    assert history.get_backup_history("alpha") == [
        {
            "status": "succeeded",
            "started_at": started_at,
            "finished_at": finished_at,
            "snapshot_id": "snap-123",
            "error": None,
        }
    ]
    assert history.get_last_backup_run("alpha") == {
        "status": "succeeded",
        "started_at": started_at,
        "finished_at": finished_at,
        "snapshot_id": "snap-123",
        "error": None,
    }


def test_history_keeps_only_recent_entries_per_job(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DOCKVAULT_HISTORY_DB_PATH", str(tmp_path / "history.sqlite3"))

    base = datetime(2026, 6, 27, 1, 0, tzinfo=timezone.utc)

    for index in range(history.MAX_HISTORY_PER_JOB + 5):
        started_at = base + timedelta(minutes=index)
        history.record_backup_run(
            "alpha",
            "succeeded",
            started_at=started_at,
            finished_at=started_at,
            snapshot_id=f"snap-{index}",
        )

    runs = history.get_backup_history("alpha")

    assert len(runs) == history.MAX_HISTORY_PER_JOB
    assert runs[0]["snapshot_id"] == "snap-24"
    assert runs[-1]["snapshot_id"] == "snap-5"


def test_clear_backup_history_removes_selected_jobs(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DOCKVAULT_HISTORY_DB_PATH", str(tmp_path / "history.sqlite3"))

    started_at = datetime(2026, 6, 27, 1, 0, tzinfo=timezone.utc)

    history.record_backup_run("alpha", "succeeded", started_at=started_at, finished_at=started_at)
    history.record_backup_run("beta", "failed", started_at=started_at, finished_at=started_at)

    history.clear_backup_history(["alpha"])

    assert history.get_backup_history("alpha") == []
    assert len(history.get_backup_history("beta")) == 1


def test_clear_backup_history_without_names_removes_all_rows(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DOCKVAULT_HISTORY_DB_PATH", str(tmp_path / "history.sqlite3"))

    started_at = datetime(2026, 6, 27, 1, 0, tzinfo=timezone.utc)

    history.record_backup_run("alpha", "succeeded", started_at=started_at, finished_at=started_at)
    history.record_backup_run("beta", "failed", started_at=started_at, finished_at=started_at)

    history.clear_backup_history()

    assert history.get_backup_history("alpha") == []
    assert history.get_backup_history("beta") == []
