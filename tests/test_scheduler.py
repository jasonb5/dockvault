import logging
import threading
from types import SimpleNamespace

from docker.errors import APIError

from dockvault.models.job import BackupJobConfig
from dockvault.scheduler import (
    create_scheduler,
    reconcile_backups,
    run_backup_limited,
    run_retention_limited,
)


def _valid_labels(repo_path: str = "/repo") -> dict[str, str]:
    return {
        "dockvault.schedule": "0 1 * * *",
        "dockvault.source.type": "files",
        "dockvault.repository.type": "local",
        "dockvault.repository.path": repo_path,
    }


def _make_volume(name: str, labels: dict[str, str]) -> SimpleNamespace:
    return SimpleNamespace(name=name, attrs={"Labels": labels})


class _FakeClient:
    def __init__(self, volumes):
        self._volumes = volumes
        self.volumes = self

    def list(self, filters):
        return list(self._volumes)


class _BrokenClient:
    def __init__(self, exc: Exception):
        self._exc = exc
        self.volumes = self

    def list(self, filters):
        raise self._exc


class _FakeScheduler:
    def __init__(self, existing_ids=None, add_job_fails_for=None):
        self._jobs = {
            jid: SimpleNamespace(id=jid) for jid in (existing_ids or [])
        }
        self.added: list[tuple[tuple, dict]] = []
        self.removed: list[str] = []
        self._add_job_fails_for = set(add_job_fails_for or [])

    def add_job(self, *args, **kwargs):
        job_id = kwargs.get("id")
        if job_id in self._add_job_fails_for:
            raise RuntimeError(f"add_job failed for {job_id}")
        self.added.append((args, kwargs))
        if job_id:
            self._jobs[job_id] = SimpleNamespace(id=job_id)
        return SimpleNamespace(id=job_id)

    def get_jobs(self):
        return list(self._jobs.values())

    def remove_job(self, job_id):
        self.removed.append(job_id)
        self._jobs.pop(job_id, None)


def test_create_scheduler_registers_reconcile_job() -> None:
    scheduler = create_scheduler()

    job = scheduler.get_job("reconcile-backups")

    assert job is not None
    assert job.name == "reconcile_backups"
    assert job.args[0] == scheduler
    assert isinstance(job.args[1], threading.BoundedSemaphore)


def test_create_scheduler_logs_configuration(monkeypatch, caplog) -> None:
    monkeypatch.setenv("DOCKVAULT_MAX_CONCURRENT_BACKUPS", "2")
    monkeypatch.setenv("DOCKVAULT_RETENTION_SCHEDULE", "30 3 * * *")
    monkeypatch.setenv("DOCKVAULT_RETENTION_ARGS", "--keep-last 7")

    caplog.set_level(logging.INFO)

    scheduler = create_scheduler()

    assert scheduler.get_job("reconcile-backups") is not None
    assert any(
        "Scheduler configured max_concurrent_backups=2 retention_enabled=True retention_schedule=30 3 * * *"
        in record.getMessage()
        for record in caplog.records
    )


def test_reconcile_backups_schedules_each_job(monkeypatch) -> None:
    fake_scheduler = _FakeScheduler()
    jobs = [
        BackupJobConfig.model_validate(
            {
                "name": "media",
                "schedule": "0 1 * * *",
                "source": {"type": "files", "volume_name": "media-volume"},
                "repository": {"type": "local", "path": "/repo"},
            }
        )
    ]

    monkeypatch.setattr("dockvault.scheduler.create_docker_client", lambda: object())
    monkeypatch.setattr("dockvault.scheduler.get_jobs", lambda client: jobs)
    monkeypatch.setattr("dockvault.scheduler.socket.gethostname", lambda: "detected-host")
    monkeypatch.setattr(
        "dockvault.scheduler.CronTrigger.from_crontab",
        lambda schedule, timezone: f"cron:{schedule}:{timezone}",
    )

    semaphore = threading.BoundedSemaphore(1)

    reconcile_backups(fake_scheduler, semaphore)

    assert len(fake_scheduler.added) == 1
    args, kwargs = fake_scheduler.added[0]
    assert args[0].__name__ == "run_backup_limited"
    assert kwargs == {
        "trigger": "cron:0 1 * * *:UTC",
        "args": [jobs[0], "detected-host", semaphore],
        "id": "backup:media",
        "max_instances": 1,
        "replace_existing": True,
        "coalesce": True,
    }


def test_reconcile_logs_summary(monkeypatch, caplog) -> None:
    fake_scheduler = _FakeScheduler(existing_ids=["reconcile-backups", "backup:gone"])
    jobs = [
        BackupJobConfig.model_validate(
            {
                "name": "media",
                "schedule": "0 1 * * *",
                "source": {"type": "files", "volume_name": "media-volume"},
                "repository": {"type": "local", "path": "/repo"},
            }
        )
    ]

    monkeypatch.setattr("dockvault.scheduler.create_docker_client", lambda: object())
    monkeypatch.setattr("dockvault.scheduler.get_jobs", lambda client: jobs)
    monkeypatch.setattr("dockvault.scheduler.socket.gethostname", lambda: "detected-host")
    monkeypatch.setattr(
        "dockvault.scheduler.CronTrigger.from_crontab",
        lambda schedule, timezone: f"cron:{schedule}:{timezone}",
    )

    caplog.set_level(logging.INFO)

    reconcile_backups(fake_scheduler, threading.BoundedSemaphore(1))

    assert any(
        "Reconcile complete discovered_jobs=1 scheduled_backup_jobs=1 failed_backup_jobs=0 scheduled_retention_jobs=0 failed_retention_jobs=0 removed_backup_jobs=1 removed_retention_jobs=0"
        in record.getMessage()
        for record in caplog.records
    )


def test_reconcile_uses_hostname_override_when_present(monkeypatch) -> None:
    fake_scheduler = _FakeScheduler()
    jobs = [
        BackupJobConfig.model_validate(
            {
                "name": "media",
                "schedule": "0 1 * * *",
                "source": {"type": "files", "volume_name": "media-volume"},
                "repository": {"type": "local", "path": "/repo"},
            }
        )
    ]

    monkeypatch.setattr("dockvault.scheduler.create_docker_client", lambda: object())
    monkeypatch.setattr("dockvault.scheduler.get_jobs", lambda client: jobs)
    monkeypatch.setenv("DOCKVAULT_HOSTNAME", "configured-host")
    monkeypatch.setattr("dockvault.scheduler.socket.gethostname", lambda: "detected-host")
    monkeypatch.setattr(
        "dockvault.scheduler.CronTrigger.from_crontab",
        lambda schedule, timezone: f"cron:{schedule}:{timezone}",
    )

    semaphore = threading.BoundedSemaphore(1)

    reconcile_backups(fake_scheduler, semaphore)

    _, kwargs = fake_scheduler.added[0]
    assert kwargs["args"] == [jobs[0], "configured-host", semaphore]


def test_reconcile_schedules_one_retention_job_per_unique_repository(monkeypatch) -> None:
    fake_scheduler = _FakeScheduler()
    jobs = [
        BackupJobConfig.model_validate(
            {
                "name": "media-a",
                "schedule": "0 1 * * *",
                "source": {"type": "files", "volume_name": "media-a"},
                "repository": {"type": "local", "path": "/repo-shared"},
            }
        ),
        BackupJobConfig.model_validate(
            {
                "name": "media-b",
                "schedule": "30 1 * * *",
                "source": {"type": "files", "volume_name": "media-b"},
                "repository": {"type": "local", "path": "/repo-shared"},
            }
        ),
    ]

    monkeypatch.setattr("dockvault.scheduler.create_docker_client", lambda: object())
    monkeypatch.setattr("dockvault.scheduler.get_jobs", lambda client: jobs)
    monkeypatch.setattr("dockvault.scheduler.socket.gethostname", lambda: "detected-host")
    monkeypatch.setenv("DOCKVAULT_RETENTION_SCHEDULE", "15 3 * * *")
    monkeypatch.setenv("DOCKVAULT_RETENTION_ARGS", "--keep-last 7")
    monkeypatch.setattr(
        "dockvault.scheduler.CronTrigger.from_crontab",
        lambda schedule, timezone: f"cron:{schedule}:{timezone}",
    )

    semaphore = threading.BoundedSemaphore(1)

    reconcile_backups(fake_scheduler, semaphore)

    retention_jobs = [
        kwargs for args, kwargs in fake_scheduler.added if args[0].__name__ == "run_retention_limited"
    ]
    assert len(retention_jobs) == 1
    assert retention_jobs[0]["trigger"] == "cron:15 3 * * *:UTC"
    assert retention_jobs[0]["args"][1:] == ["/repo-shared", "--keep-last 7", semaphore]


def test_reconcile_removes_retention_jobs_when_retention_disabled(monkeypatch) -> None:
    fake_scheduler = _FakeScheduler(
        existing_ids=["reconcile-backups", "retention:deadbeef"]
    )

    monkeypatch.setattr("dockvault.scheduler.create_docker_client", lambda: object())
    monkeypatch.setattr("dockvault.scheduler.get_jobs", lambda client: [])

    reconcile_backups(fake_scheduler, threading.BoundedSemaphore(1))

    assert fake_scheduler.removed == ["retention:deadbeef"]


def test_reconcile_logs_and_skips_retention_when_args_missing(monkeypatch, caplog) -> None:
    fake_scheduler = _FakeScheduler()
    jobs = [
        BackupJobConfig.model_validate(
            {
                "name": "media",
                "schedule": "0 1 * * *",
                "source": {"type": "files", "volume_name": "media-volume"},
                "repository": {"type": "local", "path": "/repo"},
            }
        )
    ]

    monkeypatch.setattr("dockvault.scheduler.create_docker_client", lambda: object())
    monkeypatch.setattr("dockvault.scheduler.get_jobs", lambda client: jobs)
    monkeypatch.setattr("dockvault.scheduler.socket.gethostname", lambda: "detected-host")
    monkeypatch.setenv("DOCKVAULT_RETENTION_SCHEDULE", "15 3 * * *")
    monkeypatch.delenv("DOCKVAULT_RETENTION_ARGS", raising=False)
    monkeypatch.setattr(
        "dockvault.scheduler.CronTrigger.from_crontab",
        lambda schedule, timezone: f"cron:{schedule}:{timezone}",
    )

    caplog.set_level(logging.WARNING)

    reconcile_backups(fake_scheduler, threading.BoundedSemaphore(1))

    assert not any(
        args[0].__name__ == "run_retention_limited"
        for args, kwargs in fake_scheduler.added
    )
    assert any("DOCKVAULT_RETENTION_ARGS is empty" in record.getMessage() for record in caplog.records)


def test_reconcile_schedules_per_repo_retention_without_global_args(monkeypatch) -> None:
    fake_scheduler = _FakeScheduler()
    semaphore = threading.BoundedSemaphore(1)
    jobs = [
        BackupJobConfig.model_validate(
            {
                "name": "media",
                "schedule": "0 1 * * *",
                "source": {"type": "files", "volume_name": "media-volume"},
                "repository": {"type": "local", "path": "/repo"},
                "retention": {"keep_daily": 14},
            }
        )
    ]

    monkeypatch.setattr("dockvault.scheduler.create_docker_client", lambda: object())
    monkeypatch.setattr("dockvault.scheduler.get_jobs", lambda client: jobs)
    monkeypatch.setattr("dockvault.scheduler.socket.gethostname", lambda: "detected-host")
    monkeypatch.setenv("DOCKVAULT_RETENTION_SCHEDULE", "15 3 * * *")
    monkeypatch.delenv("DOCKVAULT_RETENTION_ARGS", raising=False)
    monkeypatch.setattr(
        "dockvault.scheduler.CronTrigger.from_crontab",
        lambda schedule, timezone: f"cron:{schedule}:{timezone}",
    )

    reconcile_backups(fake_scheduler, semaphore)

    retention_jobs = [
        kwargs for args, kwargs in fake_scheduler.added if args[0].__name__ == "run_retention_limited"
    ]
    assert len(retention_jobs) == 1
    assert retention_jobs[0]["args"][1:] == ["/repo", "--keep-daily 14", semaphore]


def test_reconcile_skips_repo_when_explicit_retention_disabled(monkeypatch) -> None:
    fake_scheduler = _FakeScheduler()
    jobs = [
        BackupJobConfig.model_validate(
            {
                "name": "media",
                "schedule": "0 1 * * *",
                "source": {"type": "files", "volume_name": "media-volume"},
                "repository": {"type": "local", "path": "/repo"},
                "retention": {"enabled": False},
            }
        )
    ]

    monkeypatch.setattr("dockvault.scheduler.create_docker_client", lambda: object())
    monkeypatch.setattr("dockvault.scheduler.get_jobs", lambda client: jobs)
    monkeypatch.setenv("DOCKVAULT_RETENTION_SCHEDULE", "15 3 * * *")
    monkeypatch.setenv("DOCKVAULT_RETENTION_ARGS", "--keep-last 7")

    reconcile_backups(fake_scheduler, threading.BoundedSemaphore(1))

    assert not any(
        args[0].__name__ == "run_retention_limited"
        for args, kwargs in fake_scheduler.added
    )


def test_reconcile_skips_conflicting_repo_retention_policies(monkeypatch, caplog) -> None:
    fake_scheduler = _FakeScheduler()
    jobs = [
        BackupJobConfig.model_validate(
            {
                "name": "media-a",
                "schedule": "0 1 * * *",
                "source": {"type": "files", "volume_name": "media-a"},
                "repository": {"type": "local", "path": "/repo"},
                "retention": {"keep_last": 7},
            }
        ),
        BackupJobConfig.model_validate(
            {
                "name": "media-b",
                "schedule": "30 1 * * *",
                "source": {"type": "files", "volume_name": "media-b"},
                "repository": {"type": "local", "path": "/repo"},
                "retention": {"keep_daily": 14},
            }
        ),
    ]

    monkeypatch.setattr("dockvault.scheduler.create_docker_client", lambda: object())
    monkeypatch.setattr("dockvault.scheduler.get_jobs", lambda client: jobs)
    monkeypatch.setenv("DOCKVAULT_RETENTION_SCHEDULE", "15 3 * * *")
    monkeypatch.setenv("DOCKVAULT_RETENTION_ARGS", "--keep-last 30")

    caplog.set_level(logging.WARNING)

    reconcile_backups(fake_scheduler, threading.BoundedSemaphore(1))

    assert not any(
        args[0].__name__ == "run_retention_limited"
        for args, kwargs in fake_scheduler.added
    )
    assert any("Conflicting retention policies" in record.getMessage() for record in caplog.records)


# ---------------------------------------------------------------------------
# H1 — per-volume / per-job failure isolation
# ---------------------------------------------------------------------------


def test_reconcile_continues_scheduling_when_one_volume_has_invalid_labels(
    monkeypatch, caplog
) -> None:
    volumes = [
        _make_volume("good-a", _valid_labels("/repo-a")),
        # Missing dockvault.source.* and dockvault.repository.* → unprocessable.
        _make_volume("broken", {"dockvault.schedule": "0 1 * * *"}),
        _make_volume("good-b", _valid_labels("/repo-b")),
    ]
    fake_client = _FakeClient(volumes)
    fake_scheduler = _FakeScheduler()

    monkeypatch.setattr(
        "dockvault.scheduler.create_docker_client", lambda: fake_client
    )

    caplog.set_level(logging.WARNING)
    reconcile_backups(fake_scheduler, threading.BoundedSemaphore(1))

    scheduled_ids = {kwargs["id"] for _, kwargs in fake_scheduler.added}
    assert scheduled_ids == {"backup:good-a", "backup:good-b"}
    assert any("broken" in record.getMessage() for record in caplog.records)


def test_reconcile_continues_scheduling_when_one_cron_expression_is_invalid(
    monkeypatch, caplog
) -> None:
    volumes = [
        _make_volume("good-a", _valid_labels("/repo-a")),
        _make_volume(
            "bad-cron",
            {**_valid_labels("/repo-bad"), "dockvault.schedule": "not-a-cron"},
        ),
        _make_volume("good-b", _valid_labels("/repo-b")),
    ]
    fake_client = _FakeClient(volumes)
    fake_scheduler = _FakeScheduler()

    monkeypatch.setattr(
        "dockvault.scheduler.create_docker_client", lambda: fake_client
    )

    caplog.set_level(logging.WARNING)
    reconcile_backups(fake_scheduler, threading.BoundedSemaphore(1))

    scheduled_ids = {kwargs["id"] for _, kwargs in fake_scheduler.added}
    assert scheduled_ids == {"backup:good-a", "backup:good-b"}
    assert any("bad-cron" in record.getMessage() for record in caplog.records)


def test_reconcile_does_not_raise_when_docker_client_fails(
    monkeypatch, caplog
) -> None:
    def _raise():
        raise RuntimeError("docker socket unavailable")

    monkeypatch.setattr("dockvault.scheduler.create_docker_client", _raise)

    fake_scheduler = _FakeScheduler(
        existing_ids=["reconcile-backups", "backup:a", "backup:b"]
    )
    caplog.set_level(logging.WARNING)

    reconcile_backups(fake_scheduler, threading.BoundedSemaphore(1))

    assert fake_scheduler.added == []
    # Safety: a transient docker failure must not wipe out previously scheduled jobs.
    assert fake_scheduler.removed == []
    assert any(
        "docker" in record.getMessage().lower() for record in caplog.records
    )


def test_reconcile_does_not_raise_when_volume_listing_fails(
    monkeypatch, caplog
) -> None:
    broken_client = _BrokenClient(APIError("docker daemon is unreachable"))

    monkeypatch.setattr(
        "dockvault.scheduler.create_docker_client", lambda: broken_client
    )

    fake_scheduler = _FakeScheduler(
        existing_ids=["reconcile-backups", "backup:a", "backup:b"]
    )
    caplog.set_level(logging.WARNING)

    reconcile_backups(fake_scheduler, threading.BoundedSemaphore(1))

    assert fake_scheduler.added == []
    # Discovery failure must not be treated like "all backup volumes were deleted".
    assert fake_scheduler.removed == []
    assert any(
        ("docker" in record.getMessage().lower())
        or ("volume" in record.getMessage().lower())
        for record in caplog.records
    )


def test_reconcile_retries_job_discovery_before_succeeding(monkeypatch) -> None:
    fake_scheduler = _FakeScheduler()
    jobs = [
        BackupJobConfig.model_validate(
            {
                "name": "media",
                "schedule": "0 1 * * *",
                "source": {"type": "files", "volume_name": "media-volume"},
                "repository": {"type": "local", "path": "/repo"},
            }
        )
    ]
    attempts = {"count": 0}
    sleeps: list[int] = []

    monkeypatch.setattr("dockvault.scheduler.create_docker_client", lambda: object())
    monkeypatch.setattr("dockvault.scheduler.socket.gethostname", lambda: "detected-host")
    monkeypatch.setattr(
        "dockvault.scheduler.CronTrigger.from_crontab",
        lambda schedule, timezone: f"cron:{schedule}:{timezone}",
    )
    monkeypatch.setattr("dockvault.scheduler.time.sleep", sleeps.append)

    def _get_jobs(client):
        attempts["count"] += 1

        if attempts["count"] < 3:
            from dockvault.docker import JobDiscoveryError

            raise JobDiscoveryError("failed")

        return jobs

    monkeypatch.setattr("dockvault.scheduler.get_jobs", _get_jobs)

    reconcile_backups(fake_scheduler, threading.BoundedSemaphore(1))

    assert attempts["count"] == 3
    assert sleeps == [1, 1]
    assert len(fake_scheduler.added) == 1


def test_reconcile_preserves_jobs_when_job_discovery_retries_are_exhausted(
    monkeypatch, caplog
) -> None:
    from dockvault.docker import JobDiscoveryError

    fake_scheduler = _FakeScheduler(
        existing_ids=["reconcile-backups", "backup:a", "backup:b"]
    )
    sleeps: list[int] = []

    monkeypatch.setattr("dockvault.scheduler.create_docker_client", lambda: object())
    monkeypatch.setattr(
        "dockvault.scheduler.get_jobs",
        lambda client: (_ for _ in ()).throw(JobDiscoveryError("failed")),
    )
    monkeypatch.setattr("dockvault.scheduler.time.sleep", sleeps.append)

    caplog.set_level(logging.WARNING)
    reconcile_backups(fake_scheduler, threading.BoundedSemaphore(1))

    assert fake_scheduler.added == []
    assert fake_scheduler.removed == []
    assert sleeps == [1, 1]
    assert any("retrying docker job discovery" in record.getMessage().lower() for record in caplog.records)


def test_reconcile_continues_when_add_job_fails_for_one_job(
    monkeypatch, caplog
) -> None:
    volumes = [
        _make_volume("good-a", _valid_labels("/repo-a")),
        _make_volume("explode", _valid_labels("/repo-explode")),
        _make_volume("good-b", _valid_labels("/repo-b")),
    ]
    fake_client = _FakeClient(volumes)
    fake_scheduler = _FakeScheduler(add_job_fails_for={"backup:explode"})

    monkeypatch.setattr(
        "dockvault.scheduler.create_docker_client", lambda: fake_client
    )

    caplog.set_level(logging.WARNING)
    reconcile_backups(fake_scheduler, threading.BoundedSemaphore(1))

    scheduled_ids = {kwargs["id"] for _, kwargs in fake_scheduler.added}
    assert scheduled_ids == {"backup:good-a", "backup:good-b"}
    assert any("explode" in record.getMessage() for record in caplog.records)


# ---------------------------------------------------------------------------
# H2 — stale-job cleanup
# ---------------------------------------------------------------------------


def test_reconcile_removes_stale_backup_jobs_for_deleted_volumes(monkeypatch) -> None:
    volumes = [_make_volume("still-here", _valid_labels("/repo"))]
    fake_client = _FakeClient(volumes)
    fake_scheduler = _FakeScheduler(
        existing_ids=[
            "reconcile-backups",
            "backup:still-here",
            "backup:gone",
        ]
    )

    monkeypatch.setattr(
        "dockvault.scheduler.create_docker_client", lambda: fake_client
    )

    reconcile_backups(fake_scheduler, threading.BoundedSemaphore(1))

    assert fake_scheduler.removed == ["backup:gone"]


def test_reconcile_removes_all_backup_jobs_when_no_volumes_remain(monkeypatch) -> None:
    fake_client = _FakeClient([])
    fake_scheduler = _FakeScheduler(
        existing_ids=[
            "reconcile-backups",
            "backup:a",
            "backup:b",
            "backup:c",
        ]
    )

    monkeypatch.setattr(
        "dockvault.scheduler.create_docker_client", lambda: fake_client
    )

    reconcile_backups(fake_scheduler, threading.BoundedSemaphore(1))

    assert set(fake_scheduler.removed) == {"backup:a", "backup:b", "backup:c"}


def test_reconcile_does_not_remove_jobs_for_volumes_still_present(monkeypatch) -> None:
    volumes = [
        _make_volume("a", _valid_labels("/repo-a")),
        _make_volume("b", _valid_labels("/repo-b")),
    ]
    fake_client = _FakeClient(volumes)
    fake_scheduler = _FakeScheduler(
        existing_ids=["reconcile-backups", "backup:a", "backup:b"]
    )

    monkeypatch.setattr(
        "dockvault.scheduler.create_docker_client", lambda: fake_client
    )

    reconcile_backups(fake_scheduler, threading.BoundedSemaphore(1))

    assert fake_scheduler.removed == []


def test_reconcile_does_not_touch_non_backup_jobs(monkeypatch) -> None:
    fake_client = _FakeClient([])
    fake_scheduler = _FakeScheduler(
        existing_ids=[
            "reconcile-backups",
            "some-other-job",
            "another:unrelated",
        ]
    )

    monkeypatch.setattr(
        "dockvault.scheduler.create_docker_client", lambda: fake_client
    )

    reconcile_backups(fake_scheduler, threading.BoundedSemaphore(1))

    assert fake_scheduler.removed == []


def test_reconcile_does_not_remove_anything_when_docker_call_fails(
    monkeypatch,
) -> None:
    def _raise():
        raise RuntimeError("docker down")

    monkeypatch.setattr("dockvault.scheduler.create_docker_client", _raise)

    fake_scheduler = _FakeScheduler(
        existing_ids=["reconcile-backups", "backup:a", "backup:b"]
    )

    reconcile_backups(fake_scheduler, threading.BoundedSemaphore(1))


def test_run_backup_limited_calls_run_backup(monkeypatch) -> None:
    calls = []
    job = BackupJobConfig.model_validate(
        {
            "name": "media",
            "schedule": "0 1 * * *",
            "source": {"type": "files", "volume_name": "media-volume"},
            "repository": {"type": "local", "path": "/repo"},
        }
    )

    monkeypatch.setattr(
        "dockvault.scheduler.run_backup",
        lambda selected, hostname=None: calls.append((selected, hostname)),
    )

    run_backup_limited(job, "detected-host", threading.BoundedSemaphore(1))

    assert calls == [(job, "detected-host")]


def test_run_backup_limited_logs_when_waiting_for_slot(monkeypatch, caplog) -> None:
    calls = []
    job = BackupJobConfig.model_validate(
        {
            "name": "media",
            "schedule": "0 1 * * *",
            "source": {"type": "files", "volume_name": "media-volume"},
            "repository": {"type": "local", "path": "/repo"},
        }
    )
    class FakeSemaphore:
        def __init__(self) -> None:
            self.acquire_calls = []
            self.release_calls = 0

        def acquire(self, blocking=True):
            self.acquire_calls.append(blocking)

            if blocking is False:
                return False

            return True

        def release(self) -> None:
            self.release_calls += 1

    semaphore = FakeSemaphore()

    monkeypatch.setattr(
        "dockvault.scheduler.run_backup",
        lambda selected, hostname=None: calls.append((selected, hostname)),
    )

    caplog.set_level(logging.INFO)

    run_backup_limited(job, "detected-host", semaphore)

    assert calls == [(job, "detected-host")]
    assert semaphore.acquire_calls == [False, True]
    assert semaphore.release_calls == 1
    assert any(
        "waiting for concurrency slot job=media" in record.getMessage()
        for record in caplog.records
    )


def test_run_retention_limited_logs_when_waiting_for_slot(monkeypatch, caplog) -> None:
    calls = []

    class FakeSemaphore:
        def __init__(self) -> None:
            self.acquire_calls = []
            self.release_calls = 0

        def acquire(self, blocking=True):
            self.acquire_calls.append(blocking)

            if blocking is False:
                return False

            return True

        def release(self) -> None:
            self.release_calls += 1

    semaphore = FakeSemaphore()
    repository = SimpleNamespace(type="local", path="/repo", password_env="RESTIC_PASSWORD")

    monkeypatch.setattr(
        "dockvault.scheduler.run_retention",
        lambda selected, repo_name=None, retention_args=None: calls.append((selected, repo_name, retention_args)),
    )

    caplog.set_level(logging.INFO)

    run_retention_limited(repository, "/repo", "--keep-last 7", semaphore)

    assert calls == [(repository, "/repo", "--keep-last 7")]
    assert semaphore.acquire_calls == [False, True]
    assert semaphore.release_calls == 1
    assert any(
        "Retention waiting for concurrency slot repo=/repo" in record.getMessage()
        for record in caplog.records
    )
