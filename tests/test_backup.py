from types import SimpleNamespace

import logging
import pytest

from dockvault.commands import backup
from dockvault.models.job import BackupJobConfig
from dockvault.models.restic import ResticExitError, ResticStatus, ResticSummary
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
    assert handler.get_restore_volumes() == {
        "media-volume": {"bind": "/restore", "mode": "rw"},
    }
    assert handler.get_restore_volumes("restore-volume") == {
        "restore-volume": {"bind": "/restore", "mode": "rw"},
    }
    assert (
        handler.build_restore_command("/repo", "latest")
        == "restic -r /repo restore latest --target /restore"
    )
    assert (
        handler.build_restore_command("/repo", "latest", "/photos/2024/image.jpg")
        == "restic -r /repo restore latest --target /restore --include /photos/2024/image.jpg"
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
    container = SimpleNamespace(
        wait=lambda: {"StatusCode": 0},
        logs=lambda stdout, stderr: summary.model_dump_json().encode("utf-8"),
    )

    class FakeRepository:
        def launch(self, volumes, command, hostname=None):
            self.volumes = volumes
            self.command = command
            self.hostname = hostname

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
    repo = FakeRepository()
    recorded = []

    monkeypatch.setattr(backup, "_create_docker_client", lambda: object())
    monkeypatch.setattr(backup, "create_source_handler", lambda config: source)
    monkeypatch.setattr(backup, "create_repository_handler", lambda config, client: repo)
    monkeypatch.setattr(
        backup,
        "record_backup_run",
        lambda *args, **kwargs: recorded.append((args, kwargs)),
    )

    caplog.set_level("INFO")

    backup.run_backup(job, hostname="charon")

    assert "Starting backup" in caplog.text
    assert "job=media" in caplog.text
    assert "repo=/repo" in caplog.text
    assert "Backup completed" in caplog.text
    assert "snapshot=abc123" in caplog.text
    assert "added=2.0 KiB" in caplog.text
    assert repo.command == ["-c", "timeout 21600s backup to /repo host=charon"]
    assert repo.hostname == "charon"
    assert recorded == [
        (("media", "succeeded"), {"started_at": recorded[0][1]["started_at"], "snapshot_id": "abc123"})
    ]


def test_run_backup_wraps_restic_command_with_timeout(monkeypatch, caplog) -> None:
    job = BackupJobConfig.model_validate(
        {
            "name": "media",
            "schedule": "0 1 * * *",
            "source": {"type": "files", "volume_name": "media-volume"},
            "repository": {"type": "local", "path": "/repo"},
        }
    )
    seen = {}
    container = SimpleNamespace(
        wait=lambda: {"StatusCode": 0},
        logs=lambda stdout, stderr: _summary_bytes(),
    )

    class FakeRepository:
        def launch(self, volumes, command, hostname=None):
            seen.update({"cmd": command})

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
        build_backup_command=lambda repository, hostname: "restic backup --json /data",
    )

    monkeypatch.setattr(backup, "_create_docker_client", lambda: object())
    monkeypatch.setattr(backup, "create_source_handler", lambda config: source)
    monkeypatch.setattr(backup, "create_repository_handler", lambda config, client: FakeRepository())

    caplog.set_level("INFO")

    backup.run_backup(job)

    assert seen["cmd"] == ["-c", "timeout 21600s restic backup --json /data"]


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
    container = SimpleNamespace(
        wait=lambda: {"StatusCode": 1},
        logs=lambda stdout, stderr: error.model_dump_json().encode("utf-8"),
    )

    class FakeRepository:
        def launch(self, volumes, command, hostname=None):
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
    recorded = []

    monkeypatch.setattr(backup, "_create_docker_client", lambda: object())
    monkeypatch.setattr(backup, "create_source_handler", lambda config: source)
    monkeypatch.setattr(backup, "create_repository_handler", lambda config, client: FakeRepository())
    monkeypatch.setattr(
        backup,
        "record_backup_run",
        lambda *args, **kwargs: recorded.append((args, kwargs)),
    )

    caplog.set_level("DEBUG")

    backup.run_backup(job)

    assert "Backup failed" in caplog.text
    assert "job=media" in caplog.text
    assert "repo=/repo" in caplog.text
    assert "repository does not exist" in caplog.text
    # A backup failure must be visible at default log level (WARNING+);
    # it must not be buried alongside ordinary INFO chatter.
    failure_records = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING and "Backup failed" in r.getMessage()
    ]
    assert failure_records, (
        f"'Backup failed' must be logged at WARNING+, got:\n{caplog.text}"
    )
    assert recorded == [
        (("media", "failed"), {"started_at": recorded[0][1]["started_at"], "error": "repository does not exist"})
    ]


def test_run_restore_logs_completion_and_uses_target_volume(monkeypatch, caplog) -> None:
    job = BackupJobConfig.model_validate(
        {
            "name": "media",
            "schedule": "0 1 * * *",
            "source": {"type": "files", "volume_name": "media-volume"},
            "repository": {"type": "local", "path": "/repo"},
        }
    )
    seen = {}
    container = SimpleNamespace(
        wait=lambda: {"StatusCode": 0},
        logs=lambda stdout, stderr: b"restored successfully\n",
    )

    class FakeRepository:
        def launch(self, volumes, command, hostname=None):
            seen.update({"volumes": volumes, "command": command, "hostname": hostname})

            class Context:
                def __enter__(self_inner):
                    return container

                def __exit__(self_inner, exc_type, exc, tb):
                    return False

            return Context()

        def get_repo_path(self) -> str:
            return "/repo"

    source = SimpleNamespace(
        get_restore_volumes=lambda target=None: {target or "media-volume": {"bind": "/restore", "mode": "rw"}},
        build_restore_command=lambda repository, snapshot, restore_path=None: (
            f"restore {snapshot} to {repository} path={restore_path}"
        ),
    )

    monkeypatch.setattr(backup, "_create_docker_client", lambda: object())
    monkeypatch.setattr(backup, "create_source_handler", lambda config: source)
    monkeypatch.setattr(backup, "create_repository_handler", lambda config, client: FakeRepository())

    caplog.set_level("INFO")

    backup.run_restore(job, "latest", "restore-volume")

    assert "Starting restore job=media snapshot=latest target_volume=restore-volume repo=/repo" in caplog.text
    assert "Restore completed job=media snapshot=latest target_volume=restore-volume repo=/repo" in caplog.text
    assert seen["volumes"] == {"restore-volume": {"bind": "/restore", "mode": "rw"}}
    assert seen["command"] == ["-c", "timeout 21600s restore latest to /repo path=None"]
    assert seen["hostname"] is None


def test_run_restore_passes_restore_path(monkeypatch, caplog) -> None:
    job = BackupJobConfig.model_validate(
        {
            "name": "media",
            "schedule": "0 1 * * *",
            "source": {"type": "files", "volume_name": "media-volume"},
            "repository": {"type": "local", "path": "/repo"},
        }
    )
    seen = {}
    container = SimpleNamespace(
        wait=lambda: {"StatusCode": 0},
        logs=lambda stdout, stderr: b"restored successfully\n",
    )

    class FakeRepository:
        def launch(self, volumes, command, hostname=None):
            seen.update({"volumes": volumes, "command": command, "hostname": hostname})

            class Context:
                def __enter__(self_inner):
                    return container

                def __exit__(self_inner, exc_type, exc, tb):
                    return False

            return Context()

        def get_repo_path(self) -> str:
            return "/repo"

    source = SimpleNamespace(
        get_restore_volumes=lambda target=None: {target or "media-volume": {"bind": "/restore", "mode": "rw"}},
        build_restore_command=lambda repository, snapshot, restore_path=None: (
            f"restore {snapshot} to {repository} path={restore_path}"
        ),
    )

    monkeypatch.setattr(backup, "_create_docker_client", lambda: object())
    monkeypatch.setattr(backup, "create_source_handler", lambda config: source)
    monkeypatch.setattr(backup, "create_repository_handler", lambda config, client: FakeRepository())

    caplog.set_level("INFO")

    backup.run_restore(job, "latest", "restore-volume", "/photos/2024/image.jpg")

    assert "path=/photos/2024/image.jpg" in caplog.text
    assert seen["command"] == [
        "-c",
        "timeout 21600s restore latest to /repo path=/photos/2024/image.jpg",
    ]


def test_run_restore_logs_failure_with_last_output_line(monkeypatch, caplog) -> None:
    job = BackupJobConfig.model_validate(
        {
            "name": "media",
            "schedule": "0 1 * * *",
            "source": {"type": "files", "volume_name": "media-volume"},
            "repository": {"type": "local", "path": "/repo"},
        }
    )
    container = SimpleNamespace(
        wait=lambda: {"StatusCode": 3},
        logs=lambda stdout, stderr: b"first line\nFatal: restore failed\n",
    )

    class FakeRepository:
        def launch(self, volumes, command, hostname=None):
            class Context:
                def __enter__(self_inner):
                    return container

                def __exit__(self_inner, exc_type, exc, tb):
                    return False

            return Context()

        def get_repo_path(self) -> str:
            return "/repo"

    source = SimpleNamespace(
        get_restore_volumes=lambda target=None: {"media-volume": {"bind": "/restore", "mode": "rw"}},
        build_restore_command=lambda repository, snapshot, restore_path=None: (
            f"restore {snapshot} to {repository} path={restore_path}"
        ),
    )

    monkeypatch.setattr(backup, "_create_docker_client", lambda: object())
    monkeypatch.setattr(backup, "create_source_handler", lambda config: source)
    monkeypatch.setattr(backup, "create_repository_handler", lambda config, client: FakeRepository())

    caplog.set_level("WARNING")

    backup.run_restore(job, "abc123")

    assert "Restore failed job=media snapshot=abc123 target_volume=media-volume repo=/repo exit_code=3 error=Fatal: restore failed" in caplog.text


def test_list_snapshots_for_job_runs_tagged_restic_snapshots(monkeypatch) -> None:
    job = BackupJobConfig.model_validate(
        {
            "name": "media",
            "schedule": "0 1 * * *",
            "source": {"type": "files", "volume_name": "media-volume"},
            "repository": {"type": "local", "path": "/repo"},
        }
    )
    seen = {}
    container = SimpleNamespace(
        wait=lambda: {"StatusCode": 0},
        logs=lambda stdout, stderr: b'[{"id":"old","time":"2026-06-26T01:00:00Z"},{"id":"new","time":"2026-06-27T01:00:00Z"}]',
    )

    class FakeRepository:
        def launch(self, volumes, command, hostname=None):
            seen.update({"volumes": volumes, "command": command, "hostname": hostname})

            class Context:
                def __enter__(self_inner):
                    return container

                def __exit__(self_inner, exc_type, exc, tb):
                    return False

            return Context()

        def get_repo_path(self) -> str:
            return "/repo"

    monkeypatch.setattr(backup, "_create_docker_client", lambda: object())
    monkeypatch.setattr(backup, "create_repository_handler", lambda config, client: FakeRepository())

    snapshots = backup.list_snapshots_for_job(job)

    assert snapshots == [
        {"id": "new", "time": "2026-06-27T01:00:00Z"},
        {"id": "old", "time": "2026-06-26T01:00:00Z"},
    ]
    assert seen["volumes"] is None
    assert seen["hostname"] is None
    assert seen["command"] == [
        "-c",
        "timeout 21600s restic -r /repo snapshots --json --tag media-volume",
    ]


def test_list_snapshots_for_job_raises_on_restic_failure(monkeypatch, caplog) -> None:
    job = BackupJobConfig.model_validate(
        {
            "name": "media",
            "schedule": "0 1 * * *",
            "source": {"type": "files", "volume_name": "media-volume"},
            "repository": {"type": "local", "path": "/repo"},
        }
    )
    error = ResticExitError(message_type="exit_error", code=1, message="repository is locked")
    container = SimpleNamespace(
        wait=lambda: {"StatusCode": 1},
        logs=lambda stdout, stderr: error.model_dump_json().encode("utf-8"),
    )

    class FakeRepository:
        def launch(self, volumes, command, hostname=None):
            class Context:
                def __enter__(self_inner):
                    return container

                def __exit__(self_inner, exc_type, exc, tb):
                    return False

            return Context()

        def get_repo_path(self) -> str:
            return "/repo"

    monkeypatch.setattr(backup, "_create_docker_client", lambda: object())
    monkeypatch.setattr(backup, "create_repository_handler", lambda config, client: FakeRepository())

    caplog.set_level("WARNING")

    with pytest.raises(RuntimeError, match="repository is locked"):
        backup.list_snapshots_for_job(job)

    assert "Snapshot lookup failed" in caplog.text


def test_run_check_runs_restic_check(monkeypatch, caplog) -> None:
    job = BackupJobConfig.model_validate(
        {
            "name": "media",
            "schedule": "0 1 * * *",
            "source": {"type": "files", "volume_name": "media-volume"},
            "repository": {"type": "local", "path": "/repo"},
        }
    )
    seen = {}
    container = SimpleNamespace(
        wait=lambda: {"StatusCode": 0},
        logs=lambda stdout, stderr: b"check completed\n",
    )

    class FakeRepository:
        def launch(self, volumes, command, hostname=None):
            seen.update({"volumes": volumes, "command": command, "hostname": hostname})

            class Context:
                def __enter__(self_inner):
                    return container

                def __exit__(self_inner, exc_type, exc, tb):
                    return False

            return Context()

        def get_repo_path(self) -> str:
            return "/repo"

    monkeypatch.setattr(backup, "_create_docker_client", lambda: object())
    monkeypatch.setattr(backup, "create_repository_handler", lambda config, client: FakeRepository())

    caplog.set_level("INFO")

    backup.run_check(job)

    assert seen["volumes"] is None
    assert seen["hostname"] is None
    assert seen["command"] == ["-c", "timeout 21600s restic -r /repo check"]
    assert "Repository check completed" in caplog.text


def test_run_check_raises_on_failure(monkeypatch, caplog) -> None:
    job = BackupJobConfig.model_validate(
        {
            "name": "media",
            "schedule": "0 1 * * *",
            "source": {"type": "files", "volume_name": "media-volume"},
            "repository": {"type": "local", "path": "/repo"},
        }
    )
    container = SimpleNamespace(
        wait=lambda: {"StatusCode": 3},
        logs=lambda stdout, stderr: b"first line\nFatal: repository is locked\n",
    )

    class FakeRepository:
        def launch(self, volumes, command, hostname=None):
            class Context:
                def __enter__(self_inner):
                    return container

                def __exit__(self_inner, exc_type, exc, tb):
                    return False

            return Context()

        def get_repo_path(self) -> str:
            return "/repo"

    monkeypatch.setattr(backup, "_create_docker_client", lambda: object())
    monkeypatch.setattr(backup, "create_repository_handler", lambda config, client: FakeRepository())

    caplog.set_level("WARNING")

    with pytest.raises(RuntimeError, match="Fatal: repository is locked"):
        backup.run_check(job)

    assert "Repository check failed" in caplog.text


# ---------------------------------------------------------------------------
# C2: run_backup must surface failures rather than swallow / crash on them.
#
# Helpers below construct a minimal run_backup environment with a fake
# repository / source / docker client. They let each test focus on what
# exec_run returns (output bytes + exit_code) and what the consequences
# should be.
# ---------------------------------------------------------------------------


_JOB_LABELS = {
    "name": "media",
    "schedule": "0 1 * * *",
    "source": {"type": "files", "volume_name": "media-volume"},
    "repository": {"type": "local", "path": "/repo"},
}


def _make_job() -> BackupJobConfig:
    return BackupJobConfig.model_validate(_JOB_LABELS)


class _FakeRepository:
    """Records context-manager lifecycle so tests can assert cleanup ran."""

    def __init__(self, container) -> None:
        self._container = container
        self.entered = False
        self.exited = False

    def launch(self, volumes, command, hostname=None):
        outer = self
        outer.command = command
        outer.hostname = hostname

        class _Context:
            def __enter__(self_inner):
                outer.entered = True
                return outer._container

            def __exit__(self_inner, exc_type, exc, tb):
                outer.exited = True
                return False

        return _Context()

    def get_repo_path(self) -> str:
        return "/repo"


def _install_run_backup_env(monkeypatch, *, output: bytes | None, exit_code: int = 0) -> _FakeRepository:
    """Wire up monkeypatches so run_backup uses fake source / repo / client.

    Returns the fake repository so tests can assert on lifecycle state.
    """
    container = SimpleNamespace(
        wait=lambda: {"StatusCode": exit_code},
        logs=lambda stdout, stderr: output,
    )
    repo = _FakeRepository(container)
    source = SimpleNamespace(
        get_volumes=lambda: {},
        build_backup_command=lambda repository, hostname: "backup",
    )
    monkeypatch.setattr(backup, "_create_docker_client", lambda: object())
    monkeypatch.setattr(backup, "create_source_handler", lambda config: source)
    monkeypatch.setattr(backup, "create_repository_handler", lambda config, client: repo)
    return repo


def _summary_bytes() -> bytes:
    return ResticSummary(
        message_type="summary",
        snapshot_id="abc123",
        files_changed=2,
        data_added=2048,
        total_duration=1.5,
    ).model_dump_json().encode("utf-8")


def _status_bytes(percent: float = 0.5) -> bytes:
    return ResticStatus(
        message_type="status",
        percent_done=percent,
    ).model_dump_json().encode("utf-8")


def _warning_records_for(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno >= logging.WARNING]


# --- non-zero OS exit codes ------------------------------------------------


def test_run_backup_logs_failure_when_exit_code_nonzero_without_summary(monkeypatch, caplog) -> None:
    """OS-level non-zero exit (restic crashed before producing JSON, OOM-kill,
    container died, ...) must be reported. The current code silently inspects
    output[-1] and returns without logging anything."""
    repo = _install_run_backup_env(monkeypatch, output=b"", exit_code=137)
    caplog.set_level("DEBUG")

    backup.run_backup(_make_job())

    failures = _warning_records_for(caplog)
    assert failures, f"expected a WARNING+ log for the failure, got:\n{caplog.text}"
    combined = "\n".join(r.getMessage() for r in failures)
    assert "media" in combined
    assert "137" in combined
    assert repo.exited, "container cleanup must run even when the backup failed"


def test_run_backup_does_not_log_success_when_exit_code_is_nonzero_even_with_summary(
    monkeypatch, caplog,
) -> None:
    """If restic exits non-zero we cannot trust 'Backup completed' even when a
    summary message is present in the stream. Restic exit code 1 = hard
    failure; exit code 3 = partial failure (some files unreadable). In both
    cases the operator must see a warning, not a success line."""
    _install_run_backup_env(monkeypatch, output=_summary_bytes(), exit_code=3)
    caplog.set_level("DEBUG")

    backup.run_backup(_make_job())

    assert "Backup completed" not in caplog.text, (
        "must not log unconditional success when restic exit_code != 0"
    )
    assert _warning_records_for(caplog), (
        f"expected a WARNING+ log when exit_code != 0, got:\n{caplog.text}"
    )


# --- empty / None output ----------------------------------------------------


def test_run_backup_logs_failure_when_output_is_empty(monkeypatch, caplog) -> None:
    """`output[-1]` on an empty list raises IndexError today. Empty output is
    realistic (exec failed to start, restic killed before first line)."""
    repo = _install_run_backup_env(monkeypatch, output=b"", exit_code=0)
    caplog.set_level("DEBUG")

    backup.run_backup(_make_job())  # must not raise

    assert _warning_records_for(caplog), (
        f"expected a WARNING+ log when restic produced no output, got:\n{caplog.text}"
    )
    assert repo.exited


def test_run_backup_logs_failure_when_output_is_none(monkeypatch, caplog) -> None:
    """`result.output` can be None when the exec fails to start. Today this
    blows up with `AttributeError: 'NoneType' object has no attribute 'decode'`."""
    repo = _install_run_backup_env(monkeypatch, output=None, exit_code=1)
    caplog.set_level("DEBUG")

    backup.run_backup(_make_job())  # must not raise

    assert _warning_records_for(caplog), (
        f"expected a WARNING+ log when restic output was None, got:\n{caplog.text}"
    )
    assert repo.exited


# --- weird shapes of valid output ------------------------------------------


def test_run_backup_finds_summary_even_when_status_messages_follow_it(
    monkeypatch, caplog,
) -> None:
    """Today's code only looks at output[-1]. If restic emits a status update
    after the summary (which it can, depending on flush timing), the summary
    is invisible and the run is treated as 'unknown' / silent."""
    output = b"\n".join([_status_bytes(0.3), _summary_bytes(), _status_bytes(1.0)])
    _install_run_backup_env(monkeypatch, output=output, exit_code=0)
    caplog.set_level("INFO")

    backup.run_backup(_make_job())

    assert "Backup completed" in caplog.text
    assert "snapshot=abc123" in caplog.text


def test_run_backup_logs_failure_when_only_status_messages_present(
    monkeypatch, caplog,
) -> None:
    """If the stream contains only progress lines (e.g. restic was killed
    mid-run), today neither the summary nor the exit-error branch matches
    and run_backup returns silently. That's a false positive."""
    output = b"\n".join([_status_bytes(0.1), _status_bytes(0.5), _status_bytes(0.9)])
    _install_run_backup_env(monkeypatch, output=output, exit_code=0)
    caplog.set_level("DEBUG")

    backup.run_backup(_make_job())

    assert "Backup completed" not in caplog.text
    assert _warning_records_for(caplog), (
        f"expected a WARNING+ log when no summary or exit error was produced, got:\n{caplog.text}"
    )


# --- non-JSON lines mixed with valid JSON ----------------------------------


def test_run_backup_ignores_non_json_lines_and_still_finds_summary(
    monkeypatch, caplog,
) -> None:
    """exec_run defaults to demux=False, so stderr is interleaved into the
    same byte stream. Restic prints warnings ('unable to read xattrs', lock
    messages, ...) to stderr. Today the first non-JSON line raises
    ValidationError and propagates out of run_backup, killing the run AND
    leaving the caller (APScheduler) with an uncontextualised traceback."""
    output = b"\n".join(
        [
            b"Fatal: warning printed before --json took effect",
            _summary_bytes(),
            b"unable to read xattrs on /data/something",
        ]
    )
    _install_run_backup_env(monkeypatch, output=output, exit_code=0)
    caplog.set_level("INFO")

    backup.run_backup(_make_job())  # must not raise

    assert "Backup completed" in caplog.text


def test_run_backup_does_not_raise_when_all_output_lines_are_unparseable(
    monkeypatch, caplog,
) -> None:
    """If *every* line is garbage (restic dumped a traceback, container
    printed shell errors), we should treat the run as failed and log it,
    not propagate a ValidationError."""
    output = b"Traceback (most recent call last):\n  File ...\nValueError: boom"
    repo = _install_run_backup_env(monkeypatch, output=output, exit_code=1)
    caplog.set_level("DEBUG")

    backup.run_backup(_make_job())  # must not raise

    assert _warning_records_for(caplog), (
        f"expected a WARNING+ log when output was entirely unparseable, got:\n{caplog.text}"
    )
    assert repo.exited
