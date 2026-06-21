import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from dockvault.commands.backup import run_backup
from dockvault.docker import JobDiscoveryError, create_docker_client, get_jobs

logger = logging.getLogger(__name__)


def reconcile_backups(scheduler: AsyncIOScheduler) -> None:
    try:
        client = create_docker_client()
    except Exception as e:
        logger.warning("Reconcile loop could not connect to docker %s", e)

        return

    ids: list[str] = list()

    try:
        jobs = list(get_jobs(client))
    except JobDiscoveryError:
        return

    for job in jobs:
        try:
            _ = scheduler.add_job(
                run_backup,
                trigger=CronTrigger.from_crontab(job.schedule, timezone="UTC"),
                args=[job, "charon"],  # TODO: auto detect hostname, allow user to override
                id=f"backup:{job.name}",
                max_instances=1,
                replace_existing=True,
                coalesce=True,
            )
        except Exception as e:
            logger.warning("Failed to add job=%s: %s", job.name, e)
        finally:
            ids.append(f"backup:{job.name}")

    for job in scheduler.get_jobs():
        if job.id.startswith("backup:") and job.id not in ids:
            scheduler.remove_job(job.id)


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
