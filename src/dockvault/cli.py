from importlib.metadata import version as package_version
import json
import os
from datetime import datetime, timezone

import typer
import uvicorn

from dockvault.client import (
    DockvaultClientError,
    get_history as get_remote_history,
    get_job as get_remote_job,
    get_jobs as get_remote_jobs,
    restore as restore_remote,
    get_snapshots as get_remote_snapshots,
)
from dockvault.commands.backup import app as backup_app
from dockvault.commands.backup import _get_jobs_by_name, list_snapshots_for_job, run_restore
from dockvault.docker import JobDiscoveryError, create_docker_client, get_jobs
from dockvault.history import get_backup_history, get_last_backup_run
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


def _server_is_configured(server: str | None) -> bool:
    return bool(server or os.getenv("DOCKVAULT_SERVER_URL"))


def _isoformat_utc(value: datetime | None) -> str | None:
    if value is None:
        return None

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _history_payload(record: dict | None) -> dict | None:
    if record is None:
        return None

    return {
        "status": record["status"],
        "started_at": _isoformat_utc(record["started_at"]),
        "finished_at": _isoformat_utc(record["finished_at"]),
        "snapshot_id": record["snapshot_id"],
        "error": record["error"],
    }


def _local_job_payload(job) -> dict:
    return {
        "name": job.name,
        "schedule": job.schedule,
        "source": job.source.model_dump(mode="json"),
        "repository": job.repository.model_dump(mode="json"),
        "next_run_time": None,
        "last_run": _history_payload(get_last_backup_run(job.name)),
    }


def _print_payload(payload: dict) -> None:
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


def _get_local_jobs() -> list:
    client = create_docker_client()
    return sorted(list(get_jobs(client)), key=lambda job: job.name or "")


app.add_typer(backup_app, name="backup")


@app.command()
def jobs(server: str | None = typer.Option(None, "--server")) -> None:
    if _server_is_configured(server):
        _print_remote_payload(get_remote_jobs, server)
        return

    _print_payload({"jobs": [_local_job_payload(job) for job in _get_local_jobs()]})


@app.command()
def job(name: str, server: str | None = typer.Option(None, "--server")) -> None:
    if _server_is_configured(server):
        _print_remote_payload(get_remote_job, server, name)
        return

    _print_payload(_local_job_payload(_get_jobs_by_name(name)[0]))


@app.command()
def snapshots(name: str, server: str | None = typer.Option(None, "--server")) -> None:
    if _server_is_configured(server):
        _print_remote_payload(get_remote_snapshots, server, name)
        return

    _print_payload({"snapshots": list_snapshots_for_job(_get_jobs_by_name(name)[0])})


@app.command()
def history(name: str, server: str | None = typer.Option(None, "--server")) -> None:
    if _server_is_configured(server):
        _print_remote_payload(get_remote_history, server, name)
        return

    job_config = _get_jobs_by_name(name)[0]
    _print_payload({"runs": [_history_payload(record) for record in get_backup_history(job_config.name)]})


@app.command()
def restore(
    name: str,
    snapshot: str,
    target_volume: str | None = typer.Argument(None),
    path: str | None = typer.Option(None, "--path"),
    in_place: bool = typer.Option(False, "--in-place", help="Allow restore into the source volume"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview restore without writing data"),
    server: str | None = typer.Option(None, "--server"),
) -> None:
    if _server_is_configured(server):
        _print_remote_payload(
            restore_remote,
            server,
            name,
            snapshot,
            target_volume,
            path,
            in_place,
            dry_run,
        )
        return

    try:
        for job in _get_jobs_by_name(name):
            result = run_restore(
                job,
                snapshot,
                target_volume,
                path,
                allow_in_place=in_place,
                dry_run=dry_run,
            )
            if dry_run:
                _print_payload(result)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


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
