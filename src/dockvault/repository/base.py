import os
from collections.abc import Generator
from contextlib import AbstractContextManager, contextmanager
from typing import Protocol

from docker import DockerClient
from docker.models.containers import Container

from dockvault.models.repository import BackupRepository


class BackupRepositoryHandler(Protocol):
    def launch(self, volumes: dict[str, dict[str, str]]) -> AbstractContextManager[Container]: ...
    def get_repo_path(self) -> str: ...


class BaseContainerRepositoryHandler:
    config: BackupRepository
    client: DockerClient

    def __init__(self, config: BackupRepository, client: DockerClient):
        self.config = config
        self.client = client

    @contextmanager
    def launch(self, volumes: dict[str, dict[str, str]] | None) -> Generator[Container]:
        if volumes is None:
            volumes = {}

        container = self._create(volumes)

        try:
            container.start()
            yield container
        finally:
            container.remove(force=True)

    def _create(self, volumes: dict[str, dict[str, str]]) -> Container:
        volumes.update(self.build_volumes())

        container = self.client.containers.create(
            "ghcr.io/jasonb5/dockvault-restic:latest",
            "/bin/sleep infinity",
            environment=self.get_environment(),
            volumes=volumes,
        )

        return container

    def get_environment(self) -> dict[str, str]:
        return {"RESTIC_PASSWORD": os.environ[self.config.password_env]}

    def build_volumes(self) -> dict[str, dict[str, str]]:
        return {}

    def get_repo_path(self) -> str:
        return ""
