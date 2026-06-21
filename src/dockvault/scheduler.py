import logging
import os
import socket
import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from dockvault.commands.backup import run_backup
from dockvault.docker import JobDiscoveryError, create_docker_client, get_jobs

logger = logging.getLogger(__name__)
JOB_DISCOVERY_ATTEMPTS = 3
JOB_DISCOVERY_RETRY_DELAY_SECONDS = 1


def _get_backup_hostname() -> str:
    hostname = os.getenv("DOCKVAULT_HOSTNAME")

    if hostname:
        return hostname

    return socket.gethostname()


def _get_jobs_with_retry(client) -> list:
    for attempt in range(1, JOB_DISCOVERY_ATTEMPTS + 1):
        try:
            return list(get_jobs(client))
        except JobDiscoveryError:
            if attempt == JOB_DISCOVERY_ATTEMPTS:
                raise

            logger.warning(
                "Retrying docker job discovery attempt=%s/%s",
                attempt + 1,
                JOB_DISCOVERY_ATTEMPTS,
            )
            time.sleep(JOB_DISCOVERY_RETRY_DELAY_SECONDS)

    return []


def reconcile_backups(scheduler: AsyncIOScheduler) -> None:
    try:
        client = create_docker_client()
    except Exception as e:
        logger.warning("Reconcile loop could not connect to docker %s", e)

        return

    ids: list[str] = list()

    try:
        jobs = _get_jobs_with_retry(client)
    except JobDiscoveryError:
        return

    hostname = _get_backup_hostname()

    for job in jobs:
        try:
            _ = scheduler.add_job(
                run_backup,
                trigger=CronTrigger.from_crontab(job.schedule, timezone="UTC"),
                args=[job, hostname],
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
