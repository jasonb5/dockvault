import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from docker import DockerClient

from dockvault.commands.backup import run_backup
from dockvault.docker import get_jobs

logger = logging.getLogger(__name__)


def reconcile_backups(scheduler: AsyncIOScheduler) -> None:
    client: DockerClient = DockerClient.from_env()

    jobs = get_jobs(client)

    for job in jobs:
        scheduled = scheduler.add_job(
            run_backup,
            trigger=CronTrigger.from_crontab(job.schedule, timezone="UTC"),
            args=[job, "charon"],  # TODO: auto detect hostname, allow user to override
            id=job.name,
            max_instances=1,
            replace_existing=True,
            coalesce=True,
        )


def create_scheduler() -> AsyncIOScheduler:
    scheduler: AsyncIOScheduler = AsyncIOScheduler(timezone="UTC")

    _ = scheduler.add_job(
        reconcile_backups,
        args=[
            scheduler,
        ],
        trigger="interval",
        seconds=60,
        id="reconcile-backups",
        replace_existing=True,
        max_instances=1,
    )

    return scheduler
