from typing import override

from dockvault.repository.base import BaseContainerRepositoryHandler


class LocalRepositoryHandler(BaseContainerRepositoryHandler):
    @override
    def build_volumes(self) -> dict[str, dict[str, str]]:
        return {self.config.path: {"bind": "/repo", "mode": "rw"}}

    @override
    def get_repo_path(self) -> str:
        return "/repo"
