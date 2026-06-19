from dockvault.models.source import BackupSource
from dockvault.source.base import BackupSourceHandler
from dockvault.source.files import FilesBackupHandler

SOURCE_HANDLERS = {"files": FilesBackupHandler}


def create_source_handler(config: BackupSource) -> BackupSourceHandler:
    handler_cls = SOURCE_HANDLERS[config.type]

    return handler_cls(config)
