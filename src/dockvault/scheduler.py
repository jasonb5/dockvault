import logging
import os
import socket
import hashlib
import threading
import time
from typing import Literal

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from dockvault.commands.backup import run_backup
from dockvault.commands.retention import run_retention
from dockvault.docker import JobDiscoveryError, create_docker_client, get_jobs
from dockvault.history import clear_backup_history
from dockvault.models.job import BackupJobConfig
from dockvault.models.repository import BackupRepository
from dockvault.models.retention import RetentionConfig

logger = logging.getLogger(__name__)
JOB_DISCOVERY_ATTEMPTS = 3
JOB_DISCOVERY_RETRY_DELAY_SECONDS = 1
DEFAULT_MAX_CONCURRENT_BACKUPS = 1


def _get_backup_hostname() -> str:
    hostname = os.getenv("DOCKVAULT_HOSTNAME")

    if hostname:
        return hostname

    return socket.gethostname()


def _get_max_concurrent_backups() -> int:
    raw_value = os.getenv("DOCKVAULT_MAX_CONCURRENT_BACKUPS")

    if raw_value is None:
        return DEFAULT_MAX_CONCURRENT_BACKUPS

    try:
        value = int(raw_value)
    except ValueError:
        logger.warning(
            "Invalid DOCKVAULT_MAX_CONCURRENT_BACKUPS=%r, using default=%s",
            raw_value,
            DEFAULT_MAX_CONCURRENT_BACKUPS,
        )
        return DEFAULT_MAX_CONCURRENT_BACKUPS

    if value < 1:
        logger.warning(
            "DOCKVAULT_MAX_CONCURRENT_BACKUPS must be >= 1, using default=%s",
            DEFAULT_MAX_CONCURRENT_BACKUPS,
        )
        return DEFAULT_MAX_CONCURRENT_BACKUPS

    return value


def _get_retention_schedule() -> str | None:
    value = os.getenv("DOCKVAULT_RETENTION_SCHEDULE")

    if value is None or not value.strip():
        return None

    return value.strip()


def _retention_is_enabled() -> bool:
    return _get_retention_schedule() is not None


def _get_global_retention_args() -> str | None:
    value = os.getenv("DOCKVAULT_RETENTION_ARGS")

    if value is None or not value.strip():
        return None

    return value.strip()


def _build_retention_args(policy: RetentionConfig) -> str:
    args: list[str] = []

    for flag, value in (
        ("--keep-last", policy.keep_last),
        ("--keep-daily", policy.keep_daily),
        ("--keep-weekly", policy.keep_weekly),
        ("--keep-monthly", policy.keep_monthly),
        ("--keep-yearly", policy.keep_yearly),
    ):
        if value is not None:
            args.extend([flag, str(value)])

    if not args:
        raise ValueError(
            "retention override requires at least one keep_* option",
        )

    return " ".join(args)


def _get_explicit_retention_policy(
    job: BackupJobConfig,
) -> tuple[Literal["disabled", "args"], str | None] | None:
    policy = job.retention

    if policy is None:
        return None

    if policy.enabled is False:
        return ("disabled", None)

    if policy.has_options():
        return ("args", _build_retention_args(policy))

    if policy.enabled is True:
        raise ValueError("dockvault.retention.enabled=true requires keep_* labels")

    return None


def _retention_job_id(repository: BackupRepository) -> str:
    key = f"{repository.type}:{repository.path}:{repository.password_env}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]

    return f"retention:{digest}"


def _retention_repo_name(repository: BackupRepository) -> str:
    return repository.path


def run_backup_limited(
    job: BackupJobConfig,
    hostname: str | None,
    semaphore: threading.BoundedSemaphore,
) -> None:
    if not semaphore.acquire(blocking=False):
        logger.info("Backup waiting for concurrency slot job=%s", job.name)
        semaphore.acquire()

    try:
        run_backup(job, hostname)
    finally:
        semaphore.release()


def run_retention_limited(
    repository: BackupRepository,
    repo_name: str,
    retention_args: str,
    semaphore: threading.BoundedSemaphore,
) -> None:
    if not semaphore.acquire(blocking=False):
        logger.info("Retention waiting for concurrency slot repo=%s", repo_name)
        semaphore.acquire()

    try:
        run_retention(repository, repo_name, retention_args)
    finally:
        semaphore.release()


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


def reconcile_backups(
    scheduler: AsyncIOScheduler,
    semaphore: threading.BoundedSemaphore,
) -> None:
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
    retention_ids: list[str] = []
    repositories: dict[str, tuple[BackupRepository, str]] = {}
    repo_policies: dict[str, tuple[Literal["disabled", "args"], str | None] | None] = {}
    invalid_retention_repos: set[str] = set()
    scheduled_backup_jobs = 0
    failed_backup_jobs = 0
    scheduled_retention_jobs = 0
    failed_retention_jobs = 0
    global_retention_args = _get_global_retention_args()

    if _get_retention_schedule() is not None and global_retention_args is None:
        logger.warning(
            "Retention schedule configured but DOCKVAULT_RETENTION_ARGS is empty; only repositories with per-repo retention labels will be scheduled",
        )

    for job in jobs:
        retention_id = _retention_job_id(job.repository)
        repositories.setdefault(retention_id, (job.repository, _retention_repo_name(job.repository)))

        try:
            explicit_policy = _get_explicit_retention_policy(job)
        except ValueError as exc:
            invalid_retention_repos.add(retention_id)
            failed_retention_jobs += 1
            logger.warning(
                "Invalid retention policy job=%s repo=%s: %s",
                job.name,
                _retention_repo_name(job.repository),
                exc,
            )
            explicit_policy = None

        if explicit_policy is not None:
            existing_policy = repo_policies.get(retention_id)
            if existing_policy is None:
                repo_policies[retention_id] = explicit_policy
            elif existing_policy != explicit_policy:
                invalid_retention_repos.add(retention_id)
                failed_retention_jobs += 1
                logger.warning(
                    "Conflicting retention policies for repo=%s",
                    _retention_repo_name(job.repository),
                )

        try:
            _ = scheduler.add_job(
                run_backup_limited,
                trigger=CronTrigger.from_crontab(job.schedule, timezone="UTC"),
                args=[job, hostname, semaphore],
                id=f"backup:{job.name}",
                max_instances=1,
                replace_existing=True,
                coalesce=True,
            )
            scheduled_backup_jobs += 1
        except Exception as e:
            failed_backup_jobs += 1
            logger.warning("Failed to add job=%s: %s", job.name, e)
        finally:
            ids.append(f"backup:{job.name}")

    if _retention_is_enabled():
        retention_schedule = _get_retention_schedule()

        for retention_id, (repository, repo_name) in repositories.items():
            if retention_id in invalid_retention_repos:
                continue

            policy = repo_policies.get(retention_id)
            if policy is None:
                if global_retention_args is None:
                    continue
                retention_args = global_retention_args
            elif policy[0] == "disabled":
                continue
            else:
                retention_args = policy[1]

            try:
                _ = scheduler.add_job(
                    run_retention_limited,
                    trigger=CronTrigger.from_crontab(retention_schedule, timezone="UTC"),
                    args=[repository, repo_name, retention_args, semaphore],
                    id=retention_id,
                    max_instances=1,
                    replace_existing=True,
                    coalesce=True,
                )
                scheduled_retention_jobs += 1
            except Exception as e:
                failed_retention_jobs += 1
                logger.warning(
                    "Failed to add retention job repo=%s: %s",
                    repo_name,
                    e,
                )
            finally:
                retention_ids.append(retention_id)

    removed_backup_jobs = 0
    removed_retention_jobs = 0

    for job in scheduler.get_jobs():
        if job.id.startswith("backup:") and job.id not in ids:
            clear_backup_history([job.id.removeprefix("backup:")])
            scheduler.remove_job(job.id)
            removed_backup_jobs += 1
        if job.id.startswith("retention:") and job.id not in retention_ids:
            scheduler.remove_job(job.id)
            removed_retention_jobs += 1

    logger.info(
        "Reconcile complete discovered_jobs=%s scheduled_backup_jobs=%s failed_backup_jobs=%s scheduled_retention_jobs=%s failed_retention_jobs=%s removed_backup_jobs=%s removed_retention_jobs=%s",
        len(jobs),
        scheduled_backup_jobs,
        failed_backup_jobs,
        scheduled_retention_jobs,
        failed_retention_jobs,
        removed_backup_jobs,
        removed_retention_jobs,
    )


def create_scheduler() -> AsyncIOScheduler:
    scheduler: AsyncIOScheduler = AsyncIOScheduler(timezone="UTC")
    max_concurrent_backups = _get_max_concurrent_backups()
    retention_schedule = _get_retention_schedule()
    semaphore = threading.BoundedSemaphore(max_concurrent_backups)

    logger.info(
        "Scheduler configured max_concurrent_backups=%s retention_enabled=%s retention_schedule=%s",
        max_concurrent_backups,
        _retention_is_enabled(),
        retention_schedule,
    )

    _ = scheduler.add_job(
        reconcile_backups,
        args=[
            scheduler,
            semaphore,
        ],
        trigger="interval",
        seconds=60,
        id="reconcile-backups",
        replace_existing=True,
        max_instances=1,
    )

    return scheduler
