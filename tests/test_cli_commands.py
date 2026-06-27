from typer.testing import CliRunner

import dockvault.cli as cli_module
import dockvault.commands.backup as backup_module
from dockvault.cli import app


def test_backup_list_prints_job_names(monkeypatch) -> None:
    class FakeVolumeClient:
        def __init__(self) -> None:
            self.volumes = self

        def list(self, filters):
            return [
                type("Volume", (), {"name": "alpha", "attrs": {"Labels": {"dockvault.schedule": "0 1 * * *", "dockvault.source.type": "files", "dockvault.source.volume_name": "alpha", "dockvault.repository.type": "local", "dockvault.repository.path": "/repo", "dockvault.enabled": "true"}}})(),
            ]

    monkeypatch.setattr(backup_module, "_create_docker_client", lambda: FakeVolumeClient())

    # The python function is now `list_jobs` (to avoid shadowing the builtin);
    # typer derives the CLI subcommand name from it as `list-jobs`.
    result = CliRunner().invoke(app, ["backup", "list-jobs"])

    assert result.exit_code == 0
    assert result.stdout == "alpha\n"


def test_backup_create_runs_matching_jobs(monkeypatch) -> None:
    job = object()
    captured = {}

    monkeypatch.setattr(backup_module, "_create_docker_client", lambda: object())
    monkeypatch.setattr(backup_module, "get_jobs", lambda client, labels=None: [job])
    monkeypatch.setattr(backup_module, "run_backup", lambda selected, hostname=None: captured.update({"job": selected, "hostname": hostname}))

    result = CliRunner().invoke(app, ["backup", "create", "alpha", "charon"])

    assert result.exit_code == 0
    assert captured == {"job": job, "hostname": "charon"}


def test_restore_runs_matching_jobs(monkeypatch) -> None:
    job = object()
    captured = {}

    monkeypatch.setattr(cli_module, "_get_jobs_by_name", lambda name: [job])
    monkeypatch.setattr(
        cli_module,
        "run_restore",
        lambda selected, snapshot, target_volume=None, restore_path=None: captured.update(
            {
                "job": selected,
                "snapshot": snapshot,
                "target_volume": target_volume,
                "restore_path": restore_path,
            }
        ),
    )

    result = CliRunner().invoke(app, ["restore", "alpha", "latest", "restore-volume"])

    assert result.exit_code == 0
    assert captured == {
        "job": job,
        "snapshot": "latest",
        "target_volume": "restore-volume",
        "restore_path": None,
    }


def test_restore_passes_path_option(monkeypatch) -> None:
    job = object()
    captured = {}

    monkeypatch.setattr(cli_module, "_get_jobs_by_name", lambda name: [job])
    monkeypatch.setattr(
        cli_module,
        "run_restore",
        lambda selected, snapshot, target_volume=None, restore_path=None: captured.update(
            {
                "job": selected,
                "snapshot": snapshot,
                "target_volume": target_volume,
                "restore_path": restore_path,
            }
        ),
    )

    result = CliRunner().invoke(
        app,
        ["restore", "alpha", "latest", "restore-volume", "--path", "/photos/2024/image.jpg"],
    )

    assert result.exit_code == 0
    assert captured == {
        "job": job,
        "snapshot": "latest",
        "target_volume": "restore-volume",
        "restore_path": "/photos/2024/image.jpg",
    }


def test_backup_snapshots_prints_snapshot_json(monkeypatch) -> None:
    job = object()

    monkeypatch.setattr(backup_module, "_get_jobs_by_name", lambda name: [job])
    monkeypatch.setattr(
        backup_module,
        "list_snapshots_for_job",
        lambda selected: [{"id": "abc123", "time": "2026-06-27T01:00:00Z"}],
    )

    result = CliRunner().invoke(app, ["backup", "snapshots", "alpha"])

    assert result.exit_code == 0
    assert result.stdout == '[{"id": "abc123", "time": "2026-06-27T01:00:00Z"}]\n'


def test_backup_check_runs_matching_jobs(monkeypatch) -> None:
    job = object()
    captured = {}

    monkeypatch.setattr(backup_module, "_get_jobs_by_name", lambda name: [job])
    monkeypatch.setattr(backup_module, "run_check", lambda selected: captured.update({"job": selected}))

    result = CliRunner().invoke(app, ["backup", "check", "alpha"])

    assert result.exit_code == 0
    assert captured == {"job": job}


def test_server_command_configures_uvicorn(monkeypatch) -> None:
    captured = {}

    monkeypatch.setattr(cli_module.uvicorn, "run", lambda *args, **kwargs: captured.update({"args": args, "kwargs": kwargs}))

    result = CliRunner().invoke(app, ["server"])

    assert result.exit_code == 0
    assert captured["args"] == ("dockvault.api:app",)
    assert captured["kwargs"]["host"] == "0.0.0.0"
    assert captured["kwargs"]["port"] == 8000


def test_main_configures_logging_and_invokes_app(monkeypatch) -> None:
    events = []

    monkeypatch.setattr(cli_module, "setup_logging", lambda: events.append("logging"))
    monkeypatch.setattr(cli_module, "app", lambda: events.append("app"))

    cli_module.main()

    assert events == ["logging", "app"]
