from types import SimpleNamespace

from dockvault.docker import create_docker_client, get_jobs


def test_get_jobs_adds_enabled_filter_and_builds_job_configs() -> None:
    captured_filters = {}

    volume_with_name = SimpleNamespace(
        name="volume-a",
        attrs={
            "Labels": {
                "dockvault.name": "nightly",
                "dockvault.schedule": "0 1 * * *",
                "dockvault.source.type": "files",
                "dockvault.repository.type": "local",
                "dockvault.repository.path": "/repo-a",
            }
        },
    )
    volume_without_name = SimpleNamespace(
        name="volume-b",
        attrs={
            "Labels": {
                "dockvault.schedule": "30 2 * * *",
                "dockvault.source.type": "files",
                "dockvault.repository.type": "local",
                "dockvault.repository.path": "/repo-b",
            }
        },
    )

    class FakeClient:
        def __init__(self) -> None:
            self.volumes = self

        def list(self, filters):
            captured_filters.update(filters)
            return [volume_with_name, volume_without_name]

    labels = ["dockvault.name=nightly"]
    jobs = list(get_jobs(FakeClient(), labels))

    assert captured_filters == {
        "label": ["dockvault.name=nightly", "dockvault.enabled"],
    }
    assert labels == ["dockvault.name=nightly", "dockvault.enabled"]

    assert [job.name for job in jobs] == ["nightly", "volume-b"]
    assert jobs[0].source.volume_name == "volume-a"
    assert jobs[1].source.volume_name == "volume-b"
    assert jobs[0].repository.path == "/repo-a"
    assert jobs[1].repository.path == "/repo-b"


def test_create_docker_client_sets_timeout(monkeypatch) -> None:
    class FakeAPI:
        timeout = 0

    class FakeClient:
        def __init__(self) -> None:
            self.api = FakeAPI()

    monkeypatch.setattr("dockvault.docker.DockerClient.from_env", lambda: FakeClient())

    client = create_docker_client()

    assert client.api.timeout == 60
