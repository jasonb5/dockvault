from typing import Protocol

from dockvault.models.source import BackupSource


class BackupSourceHandler(Protocol):
    config: BackupSource

    def get_volumes(self) -> dict[str, dict[str, str]]: ...
    def build_backup_command(self, repository: str, hostname: str | None = None) -> str: ...


class BaseBackupSourceHandler:
    config: BackupSource

    def __init__(self, config: BackupSource):
        self.config = config

    def get_volumes(self) -> dict[str, dict[str, str]]:
        return {}

    def build_backup_command(self, repository: str, hostname: str | None = None) -> str:
        return ""
