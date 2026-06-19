from types import SimpleNamespace

from dockvault.commands import backup
from dockvault.models.job import BackupJobConfig
from dockvault.models.restic import ResticExitError, ResticSummary
from dockvault.source.files import FilesBackupHandler


def test_format_bytes_uses_binary_units() -> None:
    assert backup._format_bytes(512) == "512.0 B"
    assert backup._format_bytes(1024) == "1.0 KiB"
    assert backup._format_bytes(1024 * 1024) == "1.0 MiB"


def test_files_backup_handler_builds_expected_command_and_mounts() -> None:
    handler = FilesBackupHandler(
        BackupJobConfig.model_validate(
            {
                "name": "media",
                "schedule": "0 1 * * *",
                "source": {"type": "files", "volume_name": "media-volume"},
                "repository": {"type": "local", "path": "/repo"},
            }
        ).source
    )

    assert handler.get_volumes() == {
        "media-volume": {"bind": "/data", "mode": "ro"},
    }
    assert handler.build_backup_command("/repo") == "restic -r /repo backup --tag media-volume --json /data"
    assert (
        handler.build_backup_command("/repo", hostname="charon")
        == "restic -r /repo backup --host charon --tag media-volume --json /data"
    )


def test_run_backup_logs_summary(monkeypatch, caplog) -> None:
    job = BackupJobConfig.model_validate(
        {
            "name": "media",
            "schedule": "0 1 * * *",
            "source": {"type": "files", "volume_name": "media-volume"},
            "repository": {"type": "local", "path": "/repo"},
        }
    )
    summary = ResticSummary(
        message_type="summary",
        snapshot_id="abc123",
        files_changed=2,
        data_added=2048,
        total_duration=1.5,
    )
    container = SimpleNamespace(exec_run=lambda cmd: SimpleNamespace(output=summary.model_dump_json().encode("utf-8")))

    class FakeRepository:
        def launch(self, volumes):
            self.volumes = volumes

            class Context:
                def __enter__(self_inner):
                    return container

                def __exit__(self_inner, exc_type, exc, tb):
                    return False

            return Context()

        def get_repo_path(self) -> str:
            return "/repo"

    source = SimpleNamespace(
        get_volumes=lambda: {"media-volume": {"bind": "/data", "mode": "ro"}},
        build_backup_command=lambda repository, hostname: f"backup to {repository} host={hostname}",
    )

    monkeypatch.setattr(backup.DockerClient, "from_env", staticmethod(lambda: object()))
    monkeypatch.setattr(backup, "create_source_handler", lambda config: source)
    monkeypatch.setattr(backup, "create_repository_handler", lambda config, client: FakeRepository())

    caplog.set_level("INFO")

    backup.run_backup(job, hostname="charon")

    assert "Backup completed" in caplog.text
    assert "snapshot=abc123" in caplog.text
    assert "added=2.0 KiB" in caplog.text


def test_run_backup_logs_exit_error(monkeypatch, caplog) -> None:
    job = BackupJobConfig.model_validate(
        {
            "name": "media",
            "schedule": "0 1 * * *",
            "source": {"type": "files", "volume_name": "media-volume"},
            "repository": {"type": "local", "path": "/repo"},
        }
    )
    error = ResticExitError(message_type="exit_error", code=1, message="repository does not exist")
    container = SimpleNamespace(exec_run=lambda cmd: SimpleNamespace(output=error.model_dump_json().encode("utf-8")))

    class FakeRepository:
        def launch(self, volumes):
            class Context:
                def __enter__(self_inner):
                    return container

                def __exit__(self_inner, exc_type, exc, tb):
                    return False

            return Context()

        def get_repo_path(self) -> str:
            return "/repo"

    source = SimpleNamespace(
        get_volumes=lambda: {},
        build_backup_command=lambda repository, hostname: "backup",
    )

    monkeypatch.setattr(backup.DockerClient, "from_env", staticmethod(lambda: object()))
    monkeypatch.setattr(backup, "create_source_handler", lambda config: source)
    monkeypatch.setattr(backup, "create_repository_handler", lambda config, client: FakeRepository())

    caplog.set_level("INFO")

    backup.run_backup(job)

    assert "Backup failed" in caplog.text
    assert "repository does not exist" in caplog.text
