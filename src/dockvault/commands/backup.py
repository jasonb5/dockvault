import logging
from typing import Annotated, cast

import typer
from docker import DockerClient
from docker.models.containers import ExecResult
from pydantic import ValidationError

from dockvault.docker import get_jobs
from dockvault.models.job import BackupJobConfig
from dockvault.models.restic import (
    ResticExitError,
    ResticMessageAdapter,
    ResticSummary,
)
from dockvault.repository.factory import create_repository_handler
from dockvault.source.factory import create_source_handler

logger = logging.getLogger(__name__)

app = typer.Typer()


@app.command()
def list_jobs():
    client = DockerClient.from_env()

    jobs = get_jobs(client)

    for job in jobs:
        print(job.name)


@app.command()
def create(name: str, hostname: Annotated[str | None, typer.Argument()] = None):
    client = DockerClient.from_env()

    labels = [
        f"dockvault.name={name}",
    ]

    jobs = get_jobs(client, labels)

    for job in jobs:
        run_backup(job, hostname)


def run_backup(job: BackupJobConfig, hostname: str | None = None) -> None:
    client = DockerClient.from_env()

    source = create_source_handler(job.source)
    repository = create_repository_handler(job.repository, client)

    volumes = source.get_volumes()

    result: ExecResult | None = None

    with repository.launch(volumes) as container:
        try:
            repo_path = repository.get_repo_path()

            cmd = source.build_backup_command(repo_path, hostname)

            result = container.exec_run(cmd)
        except Exception as e:
            logger.error("Backup job %s failed with %s", job.name, e)
        finally:
            if result is None:
                logger.error("Backup job %s produced no result", job.name)
            else:
                report_result(job, result)


def report_result(job: BackupJobConfig, result: ExecResult) -> None:
    lines = cast(bytes, result.output or b"").decode("utf-8").splitlines()

    match result.exit_code:
        case 0:
            msg = parser_restic_summary(lines)

            if msg:
                logger.info(
                    "Backup completed volume=%s snapshot=%s files=%s added=%s duration=%s",
                    job.name,
                    msg.snapshot_id,
                    msg.files_changed,
                    _format_bytes(msg.data_added),
                    msg.total_duration,
                )
            else:
                logger.warning("Could not parse Restic output from backup job %s", job.name)
        case 1:
            msg = parser_restic_exit_error(lines)

            if msg:
                logger.warning(
                    "Backup failed volume=%s repository=%s error=%s",
                    job.name,
                    job.repository.path,
                    msg.message,
                )
            else:
                logger.warning("Could not parse Restic output from backup job %s", job.name)
        case code:
            logger.warning("Unknown restic exit code %s for backup job %s", code, job.name)


def parser_restic_summary(lines: list[str]) -> ResticSummary | None:
    for line in reversed(lines):
        try:
            msg = ResticMessageAdapter.validate_json(line)
        except ValidationError:
            logger.debug("Failed to parse restic message=%s", line)

            continue

        if msg.message_type == "summary":
            return msg

    return None


def parser_restic_exit_error(lines: list[str]) -> ResticExitError | None:
    for line in reversed(lines):
        try:
            msg = ResticMessageAdapter.validate_json(line)
        except ValidationError:
            logger.debug("Failed to parse restic message=%s", line)

            continue

        if msg.message_type == "exit_error":
            return msg

    return None


def _format_bytes(num: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if num < 1024:
            return f"{num:.1f} {unit}"

        num //= 1024

    return f"{num:.1f} PiB"
