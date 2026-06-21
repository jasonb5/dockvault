from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from dockvault.docker import JobDiscoveryError, create_docker_client, get_jobs
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
