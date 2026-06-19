import logging
from typing import Annotated, cast

import typer
from docker import DockerClient

from dockvault.docker import get_jobs
from dockvault.models.job import BackupJobConfig
from dockvault.models.restic import ResticExitError, ResticMessageAdapter, ResticSummary
from dockvault.repository.factory import create_repository_handler
from dockvault.source.factory import create_source_handler

logger = logging.getLogger(__name__)

app = typer.Typer()


@app.command()
def list():
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

    with repository.launch(volumes) as container:
        repo_path = repository.get_repo_path()

        cmd = source.build_backup_command(repo_path, hostname)

        result = container.exec_run(cmd)

        output = [
            ResticMessageAdapter.validate_json(x)
            for x in cast(bytes, result.output).decode("utf-8").splitlines()
        ]

        final = output[-1]

        if isinstance(final, ResticSummary):
            logger.info(
                "Backup completed volume=%s snapshot=%s files=%s added=%s duration=%s",
                job.name,
                final.snapshot_id,
                final.files_changed,
                _format_bytes(final.data_added),
                final.total_duration,
            )
        elif isinstance(final, ResticExitError):
            logger.info(
                "Backup failed volume=%s repository=%s error=%s",
                job.name,
                job.repository.path,
                final.message,
            )


def _format_bytes(num: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if num < 1024:
            return f"{num:.1f} {unit}"

        num //= 1024

    return f"{num:.1f} PiB"
