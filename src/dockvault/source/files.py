import shlex

from typing import override

from dockvault.source.base import BaseBackupSourceHandler


class FilesBackupHandler(BaseBackupSourceHandler):
    @override
    def get_volumes(self) -> dict[str, dict[str, str]]:
        return {self.config.volume_name: {"bind": "/data", "mode": "ro"}}

    @override
    def get_restore_volumes(self, target: str | None = None) -> dict[str, dict[str, str]]:
        volume_name = target or self.config.volume_name

        return {volume_name: {"bind": "/restore", "mode": "rw"}}

    @override
    def build_backup_command(self, repository: str, hostname: str | None = None) -> str:
        host_arg = f" --host {hostname}" if hostname else ""

        return (
            f"restic -r {repository} backup{host_arg} --tag {self.config.volume_name} --json /data"
        )

    @override
    def build_restore_command(
        self,
        repository: str,
        snapshot: str,
        restore_path: str | None = None,
        dry_run: bool = False,
    ) -> str:
        include_arg = f" --include {shlex.quote(restore_path)}" if restore_path else ""
        dry_run_arg = " --dry-run" if dry_run else ""

        return f"restic -r {repository} restore {snapshot} --target /restore{include_arg}{dry_run_arg}"
