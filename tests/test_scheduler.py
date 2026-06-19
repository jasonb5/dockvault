from types import SimpleNamespace

from dockvault.models.job import BackupJobConfig
from dockvault.scheduler import create_scheduler, reconcile_backups


def test_create_scheduler_registers_reconcile_job() -> None:
    scheduler = create_scheduler()

    job = scheduler.get_job("reconcile-backups")

    assert job is not None
    assert job.name == "reconcile_backups"
    assert job.args == (scheduler,)


def test_reconcile_backups_schedules_each_job(monkeypatch) -> None:
    scheduled = []
    fake_scheduler = SimpleNamespace(add_job=lambda *args, **kwargs: scheduled.append((args, kwargs)))
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

    monkeypatch.setattr("dockvault.scheduler.DockerClient.from_env", lambda: object())
    monkeypatch.setattr("dockvault.scheduler.get_jobs", lambda client: jobs)
    monkeypatch.setattr(
        "dockvault.scheduler.CronTrigger.from_crontab",
        lambda schedule, timezone: f"cron:{schedule}:{timezone}",
    )

    reconcile_backups(fake_scheduler)

    assert len(scheduled) == 1
    args, kwargs = scheduled[0]
    assert args[0].__name__ == "run_backup"
    assert kwargs == {
        "trigger": "cron:0 1 * * *:UTC",
        "args": [jobs[0], "charon"],
        "id": "backup:media",
        "max_instances": 1,
        "replace_existing": True,
        "coalesce": True,
    }
