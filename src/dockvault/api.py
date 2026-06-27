from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from dockvault.commands.backup import list_snapshots_for_job
from dockvault.docker import JobDiscoveryError, create_docker_client, get_jobs
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


def _isoformat_utc(value: datetime | None) -> str | None:
    if value is None:
        return None

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


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
    }


def _discover_jobs() -> list[BackupJobConfig]:
    try:
        client = create_docker_client()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="docker_unavailable") from exc

    try:
        return list(get_jobs(client))
    except JobDiscoveryError as exc:
        raise HTTPException(status_code=503, detail="job_discovery_failed") from exc


def _get_job_by_name(name: str) -> BackupJobConfig:
    for job in _discover_jobs():
        if job.name == name:
            return job

    raise HTTPException(status_code=404, detail="job_not_found")


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
        raise HTTPException(status_code=502, detail="snapshot_lookup_failed") from exc

    return {"snapshots": snapshots}
