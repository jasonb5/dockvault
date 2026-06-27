from importlib.metadata import version as package_version
import json
import os

import typer
import uvicorn

from dockvault.client import (
    DockvaultClientError,
    get_history as get_remote_history,
    get_job as get_remote_job,
    get_jobs as get_remote_jobs,
    get_snapshots as get_remote_snapshots,
)
from dockvault.commands.backup import app as backup_app
from dockvault.commands.backup import _get_jobs_by_name, run_restore
from dockvault.docker import JobDiscoveryError, create_docker_client, get_jobs
from dockvault.logging import LOGGING_CONFIG, setup_logging

app = typer.Typer(help="dockvault")


@app.command()
def version() -> None:
    print(package_version("dockvault"))


def _print_remote_payload(fetcher, server: str | None, *args: str) -> None:
    try:
        payload = fetcher(server, *args)
    except DockvaultClientError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


app.add_typer(backup_app, name="backup")


@app.command()
def jobs(server: str | None = typer.Option(None, "--server")) -> None:
    _print_remote_payload(get_remote_jobs, server)


@app.command()
def job(name: str, server: str | None = typer.Option(None, "--server")) -> None:
    _print_remote_payload(get_remote_job, server, name)


@app.command()
def snapshots(name: str, server: str | None = typer.Option(None, "--server")) -> None:
    _print_remote_payload(get_remote_snapshots, server, name)


@app.command()
def history(name: str, server: str | None = typer.Option(None, "--server")) -> None:
    _print_remote_payload(get_remote_history, server, name)


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
def doctor() -> None:
    failures: list[str] = []

    try:
        client = create_docker_client()
        typer.echo("[ok] docker reachable")
    except Exception as exc:
        typer.echo(f"[fail] docker unreachable: {exc}")
        raise typer.Exit(code=1) from exc

    try:
        jobs = list(get_jobs(client))
        typer.echo(f"[ok] discovered jobs: {len(jobs)}")
    except JobDiscoveryError as exc:
        typer.echo(f"[fail] job discovery failed: {exc}")
        raise typer.Exit(code=1) from exc

    for job in jobs:
        password_env = job.repository.password_env
        if os.getenv(password_env):
            typer.echo(f"[ok] job={job.name} password env present: {password_env}")
        else:
            failures.append(f"job={job.name} missing password env: {password_env}")

        repository_path = job.repository.path
        if os.path.exists(repository_path):
            typer.echo(f"[ok] job={job.name} repository path exists: {repository_path}")
        else:
            failures.append(
                f"job={job.name} repository path missing in container: {repository_path}"
            )

    if failures:
        for failure in failures:
            typer.echo(f"[fail] {failure}")
        raise typer.Exit(code=1)

    typer.echo("Doctor checks passed")


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
