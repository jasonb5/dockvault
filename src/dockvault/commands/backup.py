import json
import logging
import shlex
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Annotated, cast

import typer
from docker import DockerClient
from docker.models.containers import ExecResult
from pydantic import ValidationError

from dockvault.docker import get_jobs
from dockvault.history import record_backup_run
from dockvault.models.job import BackupJobConfig
from dockvault.models.restic import (
    ResticExitError,
    ResticMessageAdapter,
    ResticSummary,
)
from dockvault.repository.factory import create_repository_handler
from dockvault.source.factory import create_source_handler

logger = logging.getLogger(__name__)
RESTIC_TIMEOUT_SECONDS = 6 * 60 * 60

app = typer.Typer()


@app.command()
def list_jobs():
    client = _create_docker_client()

    jobs = get_jobs(client)

    for job in jobs:
        print(job.name)


@app.command()
def create(name: str, hostname: Annotated[str | None, typer.Argument()] = None):
    client = _create_docker_client()

    labels = [
        f"dockvault.name={name}",
    ]

    jobs = get_jobs(client, labels)

    for job in jobs:
        run_backup(job, hostname)


@app.command()
def restore(
    name: str,
    snapshot: str,
    target_volume: Annotated[str | None, typer.Argument()] = None,
):
    client = _create_docker_client()

    labels = [
        f"dockvault.name={name}",
    ]

    jobs = get_jobs(client, labels)

    for job in jobs:
        run_restore(job, snapshot, target_volume)


def run_backup(job: BackupJobConfig, hostname: str | None = None) -> None:
    client = _create_docker_client()

    source = create_source_handler(job.source)
    repository = create_repository_handler(job.repository, client)
    context = _job_context(job, repository.get_repo_path())
    started_at = datetime.now(timezone.utc)

    volumes = source.get_volumes()
    logger.info("Starting backup %s", context)

    result: ExecResult | None = None
    cmd = _with_timeout(source.build_backup_command(repository.get_repo_path(), hostname))

    with repository.launch(volumes, ["-c", cmd], hostname) as container:
        try:
            status = cast(dict[str, int], container.wait())
            result = cast(
                ExecResult,
                SimpleNamespace(
                    output=container.logs(stdout=True, stderr=True),
                    exit_code=status["StatusCode"],
                ),
            )
        except Exception as e:
            logger.error("Backup failed %s error=%s", context, e)
        finally:
            if result is None:
                logger.error("Backup produced no result %s", context)
                record_backup_run(job.name, "failed", started_at=started_at, error="no result")
            else:
                report_result(job, context, result, started_at)


def run_restore(
    job: BackupJobConfig,
    snapshot: str,
    target_volume: str | None = None,
) -> None:
    client = _create_docker_client()

    source = create_source_handler(job.source)
    repository = create_repository_handler(job.repository, client)
    restore_target = target_volume or job.source.volume_name
    context = _restore_context(job, repository.get_repo_path(), snapshot, restore_target)

    volumes = source.get_restore_volumes(target_volume)
    logger.info("Starting restore %s", context)

    result: ExecResult | None = None
    cmd = _with_timeout(source.build_restore_command(repository.get_repo_path(), snapshot))

    with repository.launch(volumes, ["-c", cmd]) as container:
        try:
            status = cast(dict[str, int], container.wait())
            result = cast(
                ExecResult,
                SimpleNamespace(
                    output=container.logs(stdout=True, stderr=True),
                    exit_code=status["StatusCode"],
                ),
            )
        except Exception as e:
            logger.error("Restore failed %s error=%s", context, e)
        finally:
            if result is None:
                logger.error("Restore produced no result %s", context)
            else:
                report_restore_result(context, result)


def list_snapshots_for_job(job: BackupJobConfig) -> list[dict]:
    client = _create_docker_client()
    repository = create_repository_handler(job.repository, client)
    context = _job_context(job, repository.get_repo_path())
    command = _with_timeout_command(
        [
            "restic",
            "-r",
            repository.get_repo_path(),
            "snapshots",
            "--json",
            "--tag",
            job.source.volume_name,
        ]
    )

    with repository.launch(None, ["-c", command]) as container:
        status = cast(dict[str, int], container.wait())
        result = cast(
            ExecResult,
            SimpleNamespace(
                output=container.logs(stdout=True, stderr=True),
                exit_code=status["StatusCode"],
            ),
        )

    lines = _decode_output_lines(result)

    if result.exit_code != 0:
        msg = parser_restic_exit_error(lines)
        message = msg.message if msg else (_last_output_line(lines) or "unknown error")
        logger.warning("Snapshot lookup failed %s error=%s", context, message)
        raise RuntimeError(message)

    try:
        snapshots = json.loads(cast(bytes, result.output or b"[]").decode("utf-8"))
    except json.JSONDecodeError as exc:
        logger.warning("Snapshot lookup returned invalid JSON %s", context)
        raise RuntimeError("invalid snapshot payload") from exc

    if not isinstance(snapshots, list):
        logger.warning("Snapshot lookup returned non-list payload %s", context)
        raise RuntimeError("invalid snapshot payload")

    return sorted(snapshots, key=lambda snapshot: snapshot.get("time") or "", reverse=True)


def report_result(
    job: BackupJobConfig,
    context: str,
    result: ExecResult,
    started_at: datetime,
) -> None:
    lines = cast(bytes, result.output or b"").decode("utf-8").splitlines()

    match result.exit_code:
        case 0:
            msg = parser_restic_summary(lines)

            if msg:
                logger.info(
                    "Backup completed %s snapshot=%s files=%s added=%s duration=%s",
                    context,
                    msg.snapshot_id,
                    msg.files_changed,
                    _format_bytes(msg.data_added),
                    msg.total_duration,
                )
                record_backup_run(
                    job.name,
                    "succeeded",
                    started_at=started_at,
                    snapshot_id=msg.snapshot_id,
                )
            else:
                logger.warning("Could not parse Restic output %s", context)
                record_backup_run(job.name, "succeeded", started_at=started_at)
        case 1:
            msg = parser_restic_exit_error(lines)

            if msg:
                logger.warning(
                    "Backup failed %s error=%s",
                    context,
                    msg.message,
                )
                record_backup_run(
                    job.name,
                    "failed",
                    started_at=started_at,
                    error=msg.message,
                )
            else:
                logger.warning("Could not parse Restic output %s", context)
                record_backup_run(job.name, "failed", started_at=started_at)
        case code:
            logger.warning("Unknown restic exit code %s %s", code, context)
            record_backup_run(
                job.name,
                "failed",
                started_at=started_at,
                error=f"exit_code={code}",
            )


def _job_context(job: BackupJobConfig, repository_path: str) -> str:
    return (
        f"job={job.name} volume={job.source.volume_name} "
        f"repo={repository_path} schedule={job.schedule}"
    )


def _restore_context(
    job: BackupJobConfig,
    repository_path: str,
    snapshot: str,
    target_volume: str,
) -> str:
    return (
        f"job={job.name} snapshot={snapshot} target_volume={target_volume} "
        f"repo={repository_path}"
    )


def _with_timeout(command: str) -> str:
    return f"timeout {RESTIC_TIMEOUT_SECONDS}s {command}"


def _with_timeout_command(command: list[str]) -> str:
    return _with_timeout(shlex.join(command))


def _create_docker_client() -> DockerClient:
    from dockvault.docker import create_docker_client

    return create_docker_client()


def report_restore_result(context: str, result: ExecResult) -> None:
    lines = _decode_output_lines(result)

    match result.exit_code:
        case 0:
            logger.info("Restore completed %s", context)
        case code:
            message = _last_output_line(lines)

            if message:
                logger.warning("Restore failed %s exit_code=%s error=%s", context, code, message)
            else:
                logger.warning("Restore failed %s exit_code=%s", context, code)


def _decode_output_lines(result: ExecResult) -> list[str]:
    return cast(bytes, result.output or b"").decode("utf-8", errors="replace").splitlines()


def _last_output_line(lines: list[str]) -> str | None:
    for line in reversed(lines):
        stripped = line.strip()

        if stripped:
            return stripped

    return None


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
