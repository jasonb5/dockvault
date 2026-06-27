from types import SimpleNamespace

from importlib.metadata import version as package_version

from typer.testing import CliRunner

import dockvault.cli as cli_module
from dockvault.cli import app


def test_version_prints_project_version() -> None:
    result = CliRunner().invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout == f"{package_version('dockvault')}\n"


def test_doctor_reports_success(monkeypatch) -> None:
    jobs = [
        SimpleNamespace(
            name="alpha",
            repository=SimpleNamespace(path="/repo-alpha", password_env="RESTIC_PASSWORD"),
        )
    ]

    monkeypatch.setattr(cli_module, "create_docker_client", lambda: object())
    monkeypatch.setattr(cli_module, "get_jobs", lambda client: jobs)
    monkeypatch.setattr(cli_module.os, "getenv", lambda name: "secret")
    monkeypatch.setattr(cli_module.os.path, "exists", lambda path: True)

    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "[ok] docker reachable" in result.stdout
    assert "[ok] discovered jobs: 1" in result.stdout
    assert "[ok] job=alpha password env present: RESTIC_PASSWORD" in result.stdout
    assert "[ok] job=alpha repository path exists: /repo-alpha" in result.stdout
    assert "Doctor checks passed" in result.stdout


def test_doctor_fails_when_docker_is_unreachable(monkeypatch) -> None:
    def _raise():
        raise RuntimeError("docker down")

    monkeypatch.setattr(cli_module, "create_docker_client", _raise)

    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert result.stdout == "[fail] docker unreachable: docker down\n"


def test_doctor_fails_when_job_configuration_is_incomplete(monkeypatch) -> None:
    jobs = [
        SimpleNamespace(
            name="alpha",
            repository=SimpleNamespace(path="/repo-alpha", password_env="RESTIC_PASSWORD"),
        )
    ]

    monkeypatch.setattr(cli_module, "create_docker_client", lambda: object())
    monkeypatch.setattr(cli_module, "get_jobs", lambda client: jobs)
    monkeypatch.setattr(cli_module.os, "getenv", lambda name: None)
    monkeypatch.setattr(cli_module.os.path, "exists", lambda path: False)

    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "[ok] docker reachable" in result.stdout
    assert "[ok] discovered jobs: 1" in result.stdout
    assert "[fail] job=alpha missing password env: RESTIC_PASSWORD" in result.stdout
    assert "[fail] job=alpha repository path missing in container: /repo-alpha" in result.stdout


def test_jobs_fetches_remote_payload(monkeypatch) -> None:
    monkeypatch.setattr(cli_module, "get_remote_jobs", lambda server: {"jobs": [{"name": "alpha"}]})

    result = CliRunner().invoke(app, ["jobs", "--server", "http://dockvault:8000"])

    assert result.exit_code == 0
    assert result.stdout == '{\n  "jobs": [\n    {\n      "name": "alpha"\n    }\n  ]\n}\n'


def test_job_fetches_remote_payload(monkeypatch) -> None:
    monkeypatch.setattr(cli_module, "get_remote_job", lambda server, name: {"name": name})

    result = CliRunner().invoke(app, ["job", "alpha", "--server", "http://dockvault:8000"])

    assert result.exit_code == 0
    assert result.stdout == '{\n  "name": "alpha"\n}\n'


def test_snapshots_fetches_remote_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        cli_module,
        "get_remote_snapshots",
        lambda server, name: {"snapshots": [{"id": "abc123"}]},
    )

    result = CliRunner().invoke(app, ["snapshots", "alpha", "--server", "http://dockvault:8000"])

    assert result.exit_code == 0
    assert result.stdout == '{\n  "snapshots": [\n    {\n      "id": "abc123"\n    }\n  ]\n}\n'


def test_history_fetches_remote_payload(monkeypatch) -> None:
    monkeypatch.setattr(cli_module, "get_remote_history", lambda server, name: {"runs": []})

    result = CliRunner().invoke(app, ["history", "alpha", "--server", "http://dockvault:8000"])

    assert result.exit_code == 0
    assert result.stdout == '{\n  "runs": []\n}\n'


def test_jobs_reports_remote_client_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        cli_module,
        "get_remote_jobs",
        lambda server: (_ for _ in ()).throw(cli_module.DockvaultClientError("server down")),
    )

    result = CliRunner().invoke(app, ["jobs", "--server", "http://dockvault:8000"])

    assert result.exit_code == 1
    assert result.stderr == "server down\n"
