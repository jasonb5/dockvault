from docker import DockerClient

from dockvault.models.repository import BackupRepository
from dockvault.repository.base import BackupRepositoryHandler
from dockvault.repository.local import LocalRepositoryHandler

REPOSITORY_HANDLERS = {
    "local": LocalRepositoryHandler,
}


def create_repository_handler(
    config: BackupRepository, client: DockerClient
) -> BackupRepositoryHandler:
    handler_cls = REPOSITORY_HANDLERS[config.type]

    return handler_cls(config, client)
