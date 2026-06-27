from types import SimpleNamespace

import logging

from dockvault.commands import retention
from dockvault.models.repository import LocalRepository
from dockvault.models.restic import ResticExitError


def test_build_retention_command_appends_prune(monkeypatch) -> None:
    monkeypatch.setenv("DOCKVAULT_RETENTION_ARGS", "--keep-last 7")

    command = retention._build_retention_command("/repo")

    assert command == (
        "timeout 21600s restic -r /repo forget --json --keep-last 7 --prune"
    )


def test_build_retention_command_keeps_existing_prune(monkeypatch) -> None:
    monkeypatch.setenv("DOCKVAULT_RETENTION_ARGS", "--keep-last 7 --prune")

    command = retention._build_retention_command("/repo")

    assert command == (
        "timeout 21600s restic -r /repo forget --json --keep-last 7 --prune"
    )


def test_build_retention_command_uses_explicit_args(monkeypatch) -> None:
    monkeypatch.delenv("DOCKVAULT_RETENTION_ARGS", raising=False)

    command = retention._build_retention_command("/repo", "--keep-daily 14")

    assert command == (
        "timeout 21600s restic -r /repo forget --json --keep-daily 14 --prune"
    )


def test_run_retention_logs_completion(monkeypatch, caplog) -> None:
    repository_config = LocalRepository(type="local", path="/repo")
    container = SimpleNamespace(
        wait=lambda: {"StatusCode": 0},
        logs=lambda stdout, stderr: b'{"message_type":"summary"}',
    )

    class FakeRepository:
        def launch(self, volumes, command, hostname=None):
            self.volumes = volumes
            self.command = command

            class Context:
                def __enter__(self_inner):
                    return container

                def __exit__(self_inner, exc_type, exc, tb):
                    return False

            return Context()

        def get_repo_path(self) -> str:
            return "/repo"

    repo = FakeRepository()

    monkeypatch.setenv("DOCKVAULT_RETENTION_ARGS", "--keep-last 7")
    monkeypatch.setattr(retention, "_create_docker_client", lambda: object())
    monkeypatch.setattr(
        retention,
        "create_repository_handler",
        lambda config, client: repo,
    )

    caplog.set_level(logging.INFO)

    retention.run_retention(repository_config, "media")

    assert "Starting retention repo_name=media repo=/repo" in caplog.text
    assert "Retention completed repo_name=media repo=/repo" in caplog.text
    assert repo.volumes is None
    assert repo.command == [
        "-c",
        "timeout 21600s restic -r /repo forget --json --keep-last 7 --prune",
    ]


def test_run_retention_logs_exit_error(monkeypatch, caplog) -> None:
    repository_config = LocalRepository(type="local", path="/repo")
    error = ResticExitError(
        message_type="exit_error",
        code=1,
        message="repository is locked",
    )
    container = SimpleNamespace(
        wait=lambda: {"StatusCode": 1},
        logs=lambda stdout, stderr: error.model_dump_json().encode("utf-8"),
    )

    class FakeRepository:
        def launch(self, volumes, command, hostname=None):
            class Context:
                def __enter__(self_inner):
                    return container

                def __exit__(self_inner, exc_type, exc, tb):
                    return False

            return Context()

        def get_repo_path(self) -> str:
            return "/repo"

    monkeypatch.setenv("DOCKVAULT_RETENTION_ARGS", "--keep-last 7")
    monkeypatch.setattr(retention, "_create_docker_client", lambda: object())
    monkeypatch.setattr(
        retention,
        "create_repository_handler",
        lambda config, client: FakeRepository(),
    )

    caplog.set_level(logging.WARNING)

    retention.run_retention(repository_config, "media")

    assert "Retention failed repo_name=media repo=/repo error=repository is locked" in caplog.text
