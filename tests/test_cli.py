from importlib.metadata import version as package_version

from typer.testing import CliRunner

from dockvault.cli import app


def test_version_prints_project_version() -> None:
    result = CliRunner().invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout == f"{package_version('dockvault')}\n"
