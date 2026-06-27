from importlib.metadata import version as package_version

import typer
import uvicorn

from dockvault.commands.backup import app as backup_app
from dockvault.commands.backup import _get_jobs_by_name, run_restore
from dockvault.logging import LOGGING_CONFIG, setup_logging

app = typer.Typer(help="dockvault")


@app.command()
def version() -> None:
    print(package_version("dockvault"))


app.add_typer(backup_app, name="backup")


@app.command()
def restore(
    name: str,
    snapshot: str,
    target_volume: str | None = typer.Argument(None),
    path: str | None = typer.Option(None, "--path"),
) -> None:
    for job in _get_jobs_by_name(name):
        run_restore(job, snapshot, target_volume, path)


@app.command()
def server() -> None:
    uvicorn.run(
        "dockvault.api:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_config=LOGGING_CONFIG,
        access_log=True,
    )


def main() -> None:
    setup_logging()

    app()


if __name__ == "__main__":
    main()
