import os
from collections.abc import Generator
from contextlib import AbstractContextManager, contextmanager
from typing import Protocol

from docker import DockerClient
from docker.models.containers import Container

from dockvault.models.repository import BackupRepository

RESTIC_IMAGE = "restic/restic:0.19.0"


class BackupRepositoryHandler(Protocol):
    def launch(
        self,
        volumes: dict[str, dict[str, str]],
        command: list[str],
        hostname: str | None = None,
    ) -> AbstractContextManager[Container]: ...
    def get_repo_path(self) -> str: ...


class BaseContainerRepositoryHandler:
    config: BackupRepository
    client: DockerClient

    def __init__(self, config: BackupRepository, client: DockerClient):
        self.config = config
        self.client = client

    @contextmanager
    def launch(
        self,
        volumes: dict[str, dict[str, str]] | None,
        command: list[str],
        hostname: str | None = None,
    ) -> Generator[Container]:
        if volumes is None:
            volumes = {}

        container = self._create(volumes, command, hostname)

        try:
            container.start()
            yield container
        finally:
            container.remove(force=True)

    def _create(
        self,
        volumes: dict[str, dict[str, str]],
        command: list[str],
        hostname: str | None,
    ) -> Container:
        volumes.update(self.build_volumes())

        container = self.client.containers.create(
            RESTIC_IMAGE,
            command,
            environment=self.get_environment(),
            entrypoint=["/bin/sh"],
            hostname=hostname,
            volumes=volumes,
        )

        return container

    def get_environment(self) -> dict[str, str]:
        try:
            password = os.environ[self.config.password_env]
        except KeyError as exc:
            raise RuntimeError(
                f"Missing restic password environment variable {self.config.password_env}"
            ) from exc

        return {"RESTIC_PASSWORD": password}

    def build_volumes(self) -> dict[str, dict[str, str]]:
        return {}

    def get_repo_path(self) -> str:
        return ""
