from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from dockvault.api import (
    _readiness_payload,
    get_job,
    get_job_history,
    get_job_snapshots,
    health,
    list_jobs,
)
from dockvault.models.job import BackupJobConfig


def test_health_endpoint_returns_ok() -> None:
    assert health() == {"status": "ok"}


def test_readiness_payload_returns_ok_when_scheduler_and_docker_are_ready(monkeypatch) -> None:
    app = SimpleNamespace(state=SimpleNamespace(scheduler=SimpleNamespace(running=True)))

    monkeypatch.setattr("dockvault.api.create_docker_client", lambda: object())
    monkeypatch.setattr("dockvault.api.get_jobs", lambda client: [])

    assert _readiness_payload(app) == ({"status": "ok"}, 200)


def test_readiness_payload_fails_when_scheduler_is_missing() -> None:
    app = SimpleNamespace(state=SimpleNamespace())

    assert _readiness_payload(app) == (
        {"status": "error", "reason": "scheduler_unavailable"},
        503,
    )


def test_readiness_payload_fails_when_scheduler_is_stopped() -> None:
    app = SimpleNamespace(state=SimpleNamespace(scheduler=SimpleNamespace(running=False)))

    assert _readiness_payload(app) == (
        {"status": "error", "reason": "scheduler_stopped"},
        503,
    )


def test_readiness_payload_fails_when_docker_client_creation_fails(monkeypatch) -> None:
    app = SimpleNamespace(state=SimpleNamespace(scheduler=SimpleNamespace(running=True)))

    def _raise():
        raise RuntimeError("docker down")

    monkeypatch.setattr("dockvault.api.create_docker_client", _raise)

    assert _readiness_payload(app) == (
        {"status": "error", "reason": "docker_unavailable"},
        503,
    )


def test_readiness_payload_fails_when_job_discovery_fails(monkeypatch) -> None:
    from dockvault.docker import JobDiscoveryError

    app = SimpleNamespace(state=SimpleNamespace(scheduler=SimpleNamespace(running=True)))

    monkeypatch.setattr("dockvault.api.create_docker_client", lambda: object())

    def _raise(client):
        raise JobDiscoveryError("failed")

    monkeypatch.setattr("dockvault.api.get_jobs", _raise)

    assert _readiness_payload(app) == (
        {"status": "error", "reason": "job_discovery_failed"},
        503,
    )


def test_list_jobs_returns_discovered_jobs_with_next_run_time(monkeypatch) -> None:
    scheduler = SimpleNamespace(
        get_job=lambda job_id: (
            SimpleNamespace(next_run_time=datetime(2026, 6, 27, 1, 0, tzinfo=timezone.utc))
            if job_id == "backup:alpha"
            else None
        )
    )

    jobs = [
        BackupJobConfig.model_validate(
            {
                "name": "beta",
                "schedule": "0 2 * * *",
                "source": {"type": "files", "volume_name": "beta-volume"},
                "repository": {"type": "local", "path": "/repo-beta"},
            }
        ),
        BackupJobConfig.model_validate(
            {
                "name": "alpha",
                "schedule": "0 1 * * *",
                "source": {"type": "files", "volume_name": "alpha-volume"},
                "repository": {"type": "local", "path": "/repo-alpha"},
            }
        ),
    ]

    monkeypatch.setattr("dockvault.api.create_docker_client", lambda: object())
    monkeypatch.setattr("dockvault.api.get_jobs", lambda client: jobs)
    monkeypatch.setattr(
        "dockvault.api.get_last_backup_run",
        lambda name: {
            "status": "succeeded",
            "started_at": datetime(2026, 6, 27, 1, 0, tzinfo=timezone.utc),
            "finished_at": datetime(2026, 6, 27, 1, 1, tzinfo=timezone.utc),
            "snapshot_id": "snap-123",
            "error": None,
        }
        if name == "alpha"
        else None,
    )

    response = list_jobs(SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(scheduler=scheduler))))

    assert response == {
        "jobs": [
            {
                "name": "alpha",
                "schedule": "0 1 * * *",
                "source": {"type": "files", "volume_name": "alpha-volume"},
                "repository": {"type": "local", "path": "/repo-alpha", "password_env": "RESTIC_PASSWORD"},
                "next_run_time": "2026-06-27T01:00:00Z",
                "last_run": {
                    "status": "succeeded",
                    "started_at": "2026-06-27T01:00:00Z",
                    "finished_at": "2026-06-27T01:01:00Z",
                    "snapshot_id": "snap-123",
                    "error": None,
                },
            },
            {
                "name": "beta",
                "schedule": "0 2 * * *",
                "source": {"type": "files", "volume_name": "beta-volume"},
                "repository": {"type": "local", "path": "/repo-beta", "password_env": "RESTIC_PASSWORD"},
                "next_run_time": None,
                "last_run": None,
            },
        ]
    }


def test_get_job_returns_matching_job(monkeypatch) -> None:
    scheduler = SimpleNamespace(get_job=lambda job_id: SimpleNamespace(next_run_time=None))

    jobs = [
        BackupJobConfig.model_validate(
            {
                "name": "alpha",
                "schedule": "0 1 * * *",
                "source": {"type": "files", "volume_name": "alpha-volume"},
                "repository": {"type": "local", "path": "/repo-alpha"},
            }
        )
    ]

    monkeypatch.setattr("dockvault.api.create_docker_client", lambda: object())
    monkeypatch.setattr("dockvault.api.get_jobs", lambda client: jobs)
    monkeypatch.setattr("dockvault.api.get_last_backup_run", lambda name: None)

    response = get_job(
        "alpha",
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(scheduler=scheduler))),
    )

    assert response == {
        "name": "alpha",
        "schedule": "0 1 * * *",
        "source": {"type": "files", "volume_name": "alpha-volume"},
        "repository": {"type": "local", "path": "/repo-alpha", "password_env": "RESTIC_PASSWORD"},
        "next_run_time": None,
        "last_run": None,
    }


def test_get_job_returns_404_when_job_is_missing(monkeypatch) -> None:
    scheduler = SimpleNamespace(get_job=lambda job_id: None)

    monkeypatch.setattr("dockvault.api.create_docker_client", lambda: object())
    monkeypatch.setattr("dockvault.api.get_jobs", lambda client: [])

    with pytest.raises(HTTPException, match="404") as excinfo:
        get_job(
            "missing",
            SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(scheduler=scheduler))),
        )

    assert excinfo.value.status_code == 404
    assert excinfo.value.detail == {
        "code": "job_not_found",
        "message": "No discovered job found with name 'missing'",
        "name": "missing",
    }


def test_list_jobs_returns_503_when_docker_is_unavailable(monkeypatch) -> None:
    scheduler = SimpleNamespace(get_job=lambda job_id: None)

    def _raise():
        raise RuntimeError("docker down")

    monkeypatch.setattr("dockvault.api.create_docker_client", _raise)

    with pytest.raises(HTTPException, match="503") as excinfo:
        list_jobs(SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(scheduler=scheduler))))

    assert excinfo.value.status_code == 503
    assert excinfo.value.detail == {
        "code": "docker_unavailable",
        "message": "Docker is unavailable for job discovery",
        "error": "docker down",
    }


def test_list_jobs_returns_503_when_job_discovery_fails(monkeypatch) -> None:
    from dockvault.docker import JobDiscoveryError

    scheduler = SimpleNamespace(get_job=lambda job_id: None)

    monkeypatch.setattr("dockvault.api.create_docker_client", lambda: object())

    def _raise(client):
        raise JobDiscoveryError("failed to list docker volumes")

    monkeypatch.setattr("dockvault.api.get_jobs", _raise)

    with pytest.raises(HTTPException, match="503") as excinfo:
        list_jobs(SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(scheduler=scheduler))))

    assert excinfo.value.status_code == 503
    assert excinfo.value.detail == {
        "code": "job_discovery_failed",
        "message": "Job discovery failed",
        "error": "failed to list docker volumes",
    }


def test_get_job_snapshots_returns_snapshot_list(monkeypatch) -> None:
    job = BackupJobConfig.model_validate(
        {
            "name": "alpha",
            "schedule": "0 1 * * *",
            "source": {"type": "files", "volume_name": "alpha-volume"},
            "repository": {"type": "local", "path": "/repo-alpha"},
        }
    )

    monkeypatch.setattr("dockvault.api.create_docker_client", lambda: object())
    monkeypatch.setattr("dockvault.api.get_jobs", lambda client: [job])
    monkeypatch.setattr(
        "dockvault.api.list_snapshots_for_job",
        lambda selected: [{"id": "abc123", "time": "2026-06-27T01:00:00Z"}],
    )

    assert get_job_snapshots("alpha") == {
        "snapshots": [{"id": "abc123", "time": "2026-06-27T01:00:00Z"}]
    }


def test_get_job_snapshots_returns_502_when_snapshot_lookup_fails(monkeypatch) -> None:
    job = BackupJobConfig.model_validate(
        {
            "name": "alpha",
            "schedule": "0 1 * * *",
            "source": {"type": "files", "volume_name": "alpha-volume"},
            "repository": {"type": "local", "path": "/repo-alpha"},
        }
    )

    monkeypatch.setattr("dockvault.api.create_docker_client", lambda: object())
    monkeypatch.setattr("dockvault.api.get_jobs", lambda client: [job])

    def _raise(selected):
        raise RuntimeError("restic failed")

    monkeypatch.setattr("dockvault.api.list_snapshots_for_job", _raise)

    with pytest.raises(HTTPException, match="502") as excinfo:
        get_job_snapshots("alpha")

    assert excinfo.value.status_code == 502
    assert excinfo.value.detail == {
        "code": "snapshot_lookup_failed",
        "message": "Snapshot lookup failed for job 'alpha'",
        "name": "alpha",
        "error": "restic failed",
    }


def test_get_job_history_returns_recent_runs(monkeypatch) -> None:
    job = BackupJobConfig.model_validate(
        {
            "name": "alpha",
            "schedule": "0 1 * * *",
            "source": {"type": "files", "volume_name": "alpha-volume"},
            "repository": {"type": "local", "path": "/repo-alpha"},
        }
    )

    monkeypatch.setattr("dockvault.api.create_docker_client", lambda: object())
    monkeypatch.setattr("dockvault.api.get_jobs", lambda client: [job])
    monkeypatch.setattr(
        "dockvault.api.get_backup_history",
        lambda name: [
            {
                "status": "failed",
                "started_at": datetime(2026, 6, 27, 1, 5, tzinfo=timezone.utc),
                "finished_at": datetime(2026, 6, 27, 1, 6, tzinfo=timezone.utc),
                "snapshot_id": None,
                "error": "repository is locked",
            },
            {
                "status": "succeeded",
                "started_at": datetime(2026, 6, 26, 1, 0, tzinfo=timezone.utc),
                "finished_at": datetime(2026, 6, 26, 1, 1, tzinfo=timezone.utc),
                "snapshot_id": "snap-122",
                "error": None,
            },
        ],
    )

    assert get_job_history("alpha") == {
        "runs": [
            {
                "status": "failed",
                "started_at": "2026-06-27T01:05:00Z",
                "finished_at": "2026-06-27T01:06:00Z",
                "snapshot_id": None,
                "error": "repository is locked",
            },
            {
                "status": "succeeded",
                "started_at": "2026-06-26T01:00:00Z",
                "finished_at": "2026-06-26T01:01:00Z",
                "snapshot_id": "snap-122",
                "error": None,
            },
        ]
    }
