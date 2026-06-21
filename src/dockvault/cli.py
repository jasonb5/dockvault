from importlib.metadata import version as package_version

import typer
import uvicorn

from dockvault.commands.backup import app as backup_app
from dockvault.logging import LOGGING_CONFIG, setup_logging

app = typer.Typer(help="dockvault")


@app.command()
def version() -> None:
    print(package_version("dockvault"))


app.add_typer(backup_app, name="backup")


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
