from typing import override

from dockvault.source.base import BaseBackupSourceHandler


class FilesBackupHandler(BaseBackupSourceHandler):
    @override
    def get_volumes(self) -> dict[str, dict[str, str]]:
        return {self.config.volume_name: {"bind": "/data", "mode": "ro"}}

    @override
    def build_backup_command(self, repository: str, hostname: str | None = None) -> str:
        host_arg = f" --host {hostname}" if hostname else ""

        return (
            f"restic -r {repository} backup{host_arg} --tag {self.config.volume_name} --json /data"
        )
