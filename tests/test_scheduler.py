import logging
from types import SimpleNamespace

from docker.errors import APIError

from dockvault.models.job import BackupJobConfig
from dockvault.scheduler import create_scheduler, reconcile_backups


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
    assert job.args == (scheduler,)


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
    monkeypatch.setattr(
        "dockvault.scheduler.CronTrigger.from_crontab",
        lambda schedule, timezone: f"cron:{schedule}:{timezone}",
    )

    reconcile_backups(fake_scheduler)

    assert len(fake_scheduler.added) == 1
    args, kwargs = fake_scheduler.added[0]
    assert args[0].__name__ == "run_backup"
    assert kwargs == {
        "trigger": "cron:0 1 * * *:UTC",
        "args": [jobs[0], "charon"],
        "id": "backup:media",
        "max_instances": 1,
        "replace_existing": True,
        "coalesce": True,
    }


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
    reconcile_backups(fake_scheduler)

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
    reconcile_backups(fake_scheduler)

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

    reconcile_backups(fake_scheduler)

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

    reconcile_backups(fake_scheduler)

    assert fake_scheduler.added == []
    # Discovery failure must not be treated like "all backup volumes were deleted".
    assert fake_scheduler.removed == []
    assert any(
        ("docker" in record.getMessage().lower())
        or ("volume" in record.getMessage().lower())
        for record in caplog.records
    )


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
    reconcile_backups(fake_scheduler)

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

    reconcile_backups(fake_scheduler)

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

    reconcile_backups(fake_scheduler)

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

    reconcile_backups(fake_scheduler)

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

    reconcile_backups(fake_scheduler)

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

    reconcile_backups(fake_scheduler)

    assert fake_scheduler.removed == []
