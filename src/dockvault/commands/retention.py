import logging
import os
import shlex
from types import SimpleNamespace
from typing import cast

from docker import DockerClient
from docker.models.containers import ExecResult

from dockvault.commands.backup import (
    RESTIC_TIMEOUT_SECONDS,
    parser_restic_exit_error,
)
from dockvault.models.repository import BackupRepository
from dockvault.repository.factory import create_repository_handler

logger = logging.getLogger(__name__)


def run_retention(
    repository_config: BackupRepository,
    repo_name: str,
    retention_args: str | None = None,
) -> None:
    client = _create_docker_client()
    repository = create_repository_handler(repository_config, client)
    repo_path = repository.get_repo_path()
    context = f"repo_name={repo_name} repo={repo_path}"

    logger.info("Starting retention %s", context)

    result: ExecResult | None = None
    command = _build_retention_command(repo_path, retention_args)

    with repository.launch(None, ["-c", command]) as container:
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
            logger.error("Retention failed %s error=%s", context, e)
        finally:
            if result is None:
                logger.error("Retention produced no result %s", context)
            else:
                _report_result(context, result)


def _build_retention_command(repository: str, retention_args: str | None = None) -> str:
    command = [
        "restic",
        "-r",
        repository,
        "forget",
        "--json",
        *shlex.split(retention_args or _get_retention_args()),
    ]

    if "--prune" not in command:
        command.append("--prune")

    return f"timeout {RESTIC_TIMEOUT_SECONDS}s {shlex.join(command)}"


def _get_retention_args() -> str:
    value = os.getenv("DOCKVAULT_RETENTION_ARGS")

    if value is None or not value.strip():
        raise RuntimeError(
            "Missing DOCKVAULT_RETENTION_ARGS for scheduled retention",
        )

    return value


def _create_docker_client() -> DockerClient:
    from dockvault.docker import create_docker_client

    return create_docker_client()


def _report_result(context: str, result: ExecResult) -> None:
    lines = cast(bytes, result.output or b"").decode("utf-8").splitlines()

    match result.exit_code:
        case 0:
            logger.info("Retention completed %s", context)
        case 1:
            msg = parser_restic_exit_error(lines)

            if msg:
                logger.warning("Retention failed %s error=%s", context, msg.message)
            else:
                logger.warning("Could not parse Restic output %s", context)
        case code:
            logger.warning("Unknown restic exit code %s %s", code, context)
