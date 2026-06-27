from contextlib import asynccontextmanager
from datetime import datetime, timezone
import os

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from dockvault.commands.backup import list_snapshots_for_job, run_backup, run_check, run_restore
from dockvault.docker import JobDiscoveryError, create_docker_client, get_jobs
from dockvault.history import get_backup_history, get_last_backup_run
from dockvault.models.job import BackupJobConfig
from dockvault.scheduler import create_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = create_scheduler()
    scheduler.start()

    app.state.scheduler = scheduler

    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(lifespan=lifespan)


class RestoreRequest(BaseModel):
    snapshot: str
    target_volume: str | None = None
    path: str | None = None
    allow_in_place: bool = False
    dry_run: bool = False


def _error_detail(code: str, message: str, **extra) -> dict:
    return {"code": code, "message": message, **extra}


def _get_configured_api_token() -> str | None:
    value = os.getenv("DOCKVAULT_API_TOKEN")

    if value is None or not value.strip():
        return None

    return value.strip()


def require_api_auth(authorization: str | None = Header(None)) -> None:
    configured_token = _get_configured_api_token()

    if configured_token is None:
        return

    if authorization != f"Bearer {configured_token}":
        raise HTTPException(
            status_code=401,
            detail=_error_detail(
                "unauthorized",
                "Missing or invalid API token",
            ),
        )


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


def _job_payload(app: FastAPI, job: BackupJobConfig) -> dict:
    scheduler = getattr(app.state, "scheduler", None)
    scheduled_job = None

    if scheduler is not None:
        scheduled_job = scheduler.get_job(f"backup:{job.name}")

    return {
        "name": job.name,
        "schedule": job.schedule,
        "source": job.source.model_dump(mode="json"),
        "repository": job.repository.model_dump(mode="json"),
        "next_run_time": _isoformat_utc(getattr(scheduled_job, "next_run_time", None)),
        "last_run": _history_payload(get_last_backup_run(job.name)),
    }


def _discover_jobs() -> list[BackupJobConfig]:
    try:
        client = create_docker_client()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=_error_detail(
                "docker_unavailable",
                "Docker is unavailable for job discovery",
                error=str(exc),
            ),
        ) from exc

    try:
        return list(get_jobs(client))
    except JobDiscoveryError as exc:
        raise HTTPException(
            status_code=503,
            detail=_error_detail(
                "job_discovery_failed",
                "Job discovery failed",
                error=str(exc),
            ),
        ) from exc


def _get_job_by_name(name: str) -> BackupJobConfig:
    for job in _discover_jobs():
        if job.name == name:
            return job

    raise HTTPException(
        status_code=404,
        detail=_error_detail(
            "job_not_found",
            f"No discovered job found with name '{name}'",
            name=name,
        ),
    )


def _readiness_payload(app: FastAPI) -> tuple[dict[str, str], int]:
    scheduler = getattr(app.state, "scheduler", None)

    if scheduler is None:
        return {"status": "error", "reason": "scheduler_unavailable"}, 503

    if not getattr(scheduler, "running", False):
        return {"status": "error", "reason": "scheduler_stopped"}, 503

    try:
        client = create_docker_client()
    except Exception:
        return {"status": "error", "reason": "docker_unavailable"}, 503

    try:
        _ = list(get_jobs(client))
    except JobDiscoveryError:
        return {"status": "error", "reason": "job_discovery_failed"}, 503

    return {"status": "ok"}, 200


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
def ready(request: Request) -> JSONResponse:
    payload, status_code = _readiness_payload(request.app)

    return JSONResponse(content=payload, status_code=status_code)


@app.get("/jobs")
def list_jobs(request: Request) -> dict[str, list[dict]]:
    jobs = sorted(_discover_jobs(), key=lambda job: job.name or "")

    return {"jobs": [_job_payload(request.app, job) for job in jobs]}


@app.get("/jobs/{name}")
def get_job(name: str, request: Request) -> dict:
    job = _get_job_by_name(name)

    return _job_payload(request.app, job)


@app.get("/jobs/{name}/snapshots")
def get_job_snapshots(name: str) -> dict[str, list[dict]]:
    job = _get_job_by_name(name)

    try:
        snapshots = list_snapshots_for_job(job)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=502,
            detail=_error_detail(
                "snapshot_lookup_failed",
                f"Snapshot lookup failed for job '{name}'",
                name=name,
                error=str(exc),
            ),
        ) from exc

    return {"snapshots": snapshots}


@app.get("/jobs/{name}/history")
def get_job_history(name: str) -> dict[str, list[dict]]:
    job = _get_job_by_name(name)

    return {"runs": [_history_payload(record) for record in get_backup_history(job.name)]}


@app.post("/jobs/{name}/restore")
def restore_job(name: str, payload: RestoreRequest, authorization: str | None = Header(None)) -> dict:
    require_api_auth(authorization)
    job = _get_job_by_name(name)

    try:
        restore_result = run_restore(
            job,
            payload.snapshot,
            payload.target_volume,
            payload.path,
            allow_in_place=payload.allow_in_place,
            dry_run=payload.dry_run,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=_error_detail(
                "invalid_restore_request",
                str(exc),
                name=name,
            ),
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=502,
            detail=_error_detail(
                "restore_failed",
                f"Restore failed for job '{name}'",
                name=name,
                error=str(exc),
            ),
        ) from exc

    response = {
        "status": "ok",
        "name": name,
        "snapshot": payload.snapshot,
        "target_volume": payload.target_volume or job.source.volume_name,
        "path": payload.path,
        "allow_in_place": payload.allow_in_place,
        "dry_run": payload.dry_run,
    }

    if payload.dry_run:
        response["output"] = restore_result["output"]

    return response


@app.post("/jobs/{name}/backup")
def backup_job(name: str, authorization: str | None = Header(None)) -> dict:
    require_api_auth(authorization)
    job = _get_job_by_name(name)

    try:
        run_backup(job, raise_on_failure=True)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=502,
            detail=_error_detail(
                "backup_failed",
                f"Backup failed for job '{name}'",
                name=name,
                error=str(exc),
            ),
        ) from exc

    return {
        "status": "ok",
        "name": name,
    }


@app.post("/jobs/{name}/check")
def check_job(name: str, authorization: str | None = Header(None)) -> dict:
    require_api_auth(authorization)
    job = _get_job_by_name(name)

    try:
        run_check(job)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=502,
            detail=_error_detail(
                "check_failed",
                f"Repository check failed for job '{name}'",
                name=name,
                error=str(exc),
            ),
        ) from exc

    return {
        "status": "ok",
        "name": name,
    }
