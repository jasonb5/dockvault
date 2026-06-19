from types import SimpleNamespace

import pytest

from dockvault.models.repository import LocalRepository
from dockvault.models.source import FileSource
from dockvault.repository.factory import create_repository_handler
from dockvault.repository.local import LocalRepositoryHandler
from dockvault.source.factory import create_source_handler
from dockvault.source.files import FilesBackupHandler


def test_create_source_handler_returns_files_handler() -> None:
    handler = create_source_handler(FileSource(type="files", volume_name="media"))

    assert isinstance(handler, FilesBackupHandler)


def test_create_repository_handler_returns_local_handler() -> None:
    client = SimpleNamespace()
    handler = create_repository_handler(LocalRepository(type="local", path="/repo"), client)

    assert isinstance(handler, LocalRepositoryHandler)


def test_create_source_handler_rejects_unknown_type() -> None:
    with pytest.raises(KeyError):
        create_source_handler(SimpleNamespace(type="unknown"))


def test_create_repository_handler_rejects_unknown_type() -> None:
    with pytest.raises(KeyError):
        create_repository_handler(SimpleNamespace(type="unknown"), SimpleNamespace())
