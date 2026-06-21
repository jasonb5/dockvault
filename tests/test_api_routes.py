from types import SimpleNamespace

from dockvault.api import _readiness_payload, health


def test_health_endpoint_returns_ok() -> None:
    assert health() == {"status": "ok"}


def test_readiness_payload_returns_ok_when_scheduler_and_docker_are_ready(monkeypatch) -> None:
    app = SimpleNamespace(state=SimpleNamespace(scheduler=SimpleNamespace(running=True)))

    monkeypatch.setattr("dockvault.api.create_docker_client", lambda: object())
    monkeypatch.setattr("dockvault.api.get_jobs", lambda client: [])

    assert _readiness_payload(app) == ({"status": "ok"}, 200)


def test_readiness_payload_fails_when_scheduler_is_missing() -> None:
    app = SimpleNamespace(state=SimpleNamespace())

    assert _readiness_payload(app) == (
        {"status": "error", "reason": "scheduler_unavailable"},
        503,
    )


def test_readiness_payload_fails_when_scheduler_is_stopped() -> None:
    app = SimpleNamespace(state=SimpleNamespace(scheduler=SimpleNamespace(running=False)))

    assert _readiness_payload(app) == (
        {"status": "error", "reason": "scheduler_stopped"},
        503,
    )


def test_readiness_payload_fails_when_docker_client_creation_fails(monkeypatch) -> None:
    app = SimpleNamespace(state=SimpleNamespace(scheduler=SimpleNamespace(running=True)))

    def _raise():
        raise RuntimeError("docker down")

    monkeypatch.setattr("dockvault.api.create_docker_client", _raise)

    assert _readiness_payload(app) == (
        {"status": "error", "reason": "docker_unavailable"},
        503,
    )


def test_readiness_payload_fails_when_job_discovery_fails(monkeypatch) -> None:
    from dockvault.docker import JobDiscoveryError

    app = SimpleNamespace(state=SimpleNamespace(scheduler=SimpleNamespace(running=True)))

    monkeypatch.setattr("dockvault.api.create_docker_client", lambda: object())

    def _raise(client):
        raise JobDiscoveryError("failed")

    monkeypatch.setattr("dockvault.api.get_jobs", _raise)

    assert _readiness_payload(app) == (
        {"status": "error", "reason": "job_discovery_failed"},
        503,
    )
