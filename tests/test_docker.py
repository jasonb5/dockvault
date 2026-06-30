from types import SimpleNamespace

import pytest

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

        def get(self, name):
            raise AssertionError(f"unexpected get({name})")

    labels = ["dockvault.name=nightly"]
    jobs = list(get_jobs(FakeClient(), labels))

    assert captured_filters == {"label": ["dockvault.enabled"]}
    assert labels == ["dockvault.name=nightly"]

    assert [job.name for job in jobs] == ["nightly"]
    assert jobs[0].source.volume_name == "volume-a"
    assert jobs[0].repository.path == "/repo-a"


def test_get_jobs_uses_external_config_for_existing_unlabeled_volume(
    tmp_path, monkeypatch
) -> None:
    config_path = tmp_path / "dockvault.yaml"
    config_path.write_text(
        """
jobs:
  media:
    source:
      type: files
      volume_name: media_data
    schedule: "0 1 * * *"
    repository:
      type: local
      path: /srv/restic/media
""".strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DOCKVAULT_CONFIG_PATH", str(config_path))

    volume = SimpleNamespace(name="media_data", attrs={"Labels": {}})

    class FakeClient:
        def __init__(self) -> None:
            self.volumes = self

        def list(self, filters):
            return []

        def get(self, name):
            assert name == "media_data"
            return volume

    jobs = list(get_jobs(FakeClient()))

    assert len(jobs) == 1
    assert jobs[0].name == "media"
    assert jobs[0].source.volume_name == "media_data"
    assert jobs[0].repository.path == "/srv/restic/media"


def test_get_jobs_applies_server_env_defaults_to_label_discovered_volume(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DOCKVAULT_DEFAULT_SOURCE_TYPE", "files")
    monkeypatch.setenv("DOCKVAULT_DEFAULT_REPOSITORY_TYPE", "local")
    monkeypatch.setenv("DOCKVAULT_DEFAULT_REPOSITORY_PASSWORD_ENV", "SERVER_PASSWORD")
    monkeypatch.setenv("DOCKVAULT_DEFAULT_RETENTION_KEEP_WEEKLY", "8")

    volume = SimpleNamespace(
        name="media_data",
        attrs={
            "Labels": {
                "dockvault.enabled": "true",
                "dockvault.name": "media",
                "dockvault.schedule": "0 1 * * *",
                "dockvault.repository.path": "/srv/restic/media",
            }
        },
    )

    class FakeClient:
        def __init__(self) -> None:
            self.volumes = self

        def list(self, filters):
            return [volume]

        def get(self, name):
            raise AssertionError(f"unexpected get({name})")

    jobs = list(get_jobs(FakeClient()))

    assert len(jobs) == 1
    assert jobs[0].source.type == "files"
    assert jobs[0].repository.type == "local"
    assert jobs[0].repository.password_env == "SERVER_PASSWORD"
    assert jobs[0].retention is not None
    assert jobs[0].retention.keep_weekly == 8


def test_external_config_overrides_server_env_defaults(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "dockvault.yaml"
    config_path.write_text(
        """
jobs:
  media:
    source:
      type: files
      volume_name: media_data
    schedule: "0 1 * * *"
    repository:
      type: local
      path: /srv/restic/media
      password_env: CONFIG_PASSWORD
""".strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DOCKVAULT_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("DOCKVAULT_DEFAULT_REPOSITORY_PASSWORD_ENV", "SERVER_PASSWORD")

    volume = SimpleNamespace(name="media_data", attrs={"Labels": {}})

    class FakeClient:
        def __init__(self) -> None:
            self.volumes = self

        def list(self, filters):
            return []

        def get(self, name):
            return volume

    jobs = list(get_jobs(FakeClient()))

    assert len(jobs) == 1
    assert jobs[0].repository.password_env == "CONFIG_PASSWORD"


def test_get_jobs_external_config_overrides_labels(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "dockvault.yaml"
    config_path.write_text(
        """
defaults:
  repository:
    type: local
    password_env: SHARED_PASSWORD
jobs:
  media-override:
    source:
      type: files
      volume_name: media_data
    schedule: "0 3 * * *"
    repository:
      path: /srv/restic/override
""".strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DOCKVAULT_CONFIG_PATH", str(config_path))

    volume = SimpleNamespace(
        name="media_data",
        attrs={
            "Labels": {
                "dockvault.enabled": "true",
                "dockvault.name": "media-label",
                "dockvault.schedule": "0 1 * * *",
                "dockvault.source.type": "files",
                "dockvault.repository.type": "local",
                "dockvault.repository.path": "/srv/restic/label",
                "dockvault.repository.password_env": "LABEL_PASSWORD",
            }
        },
    )

    class FakeClient:
        def __init__(self) -> None:
            self.volumes = self

        def list(self, filters):
            return [volume]

        def get(self, name):
            raise AssertionError(f"unexpected get({name})")

    jobs = list(get_jobs(FakeClient()))

    assert len(jobs) == 1
    assert jobs[0].name == "media-override"
    assert jobs[0].schedule == "0 3 * * *"
    assert jobs[0].repository.path == "/srv/restic/override"
    assert jobs[0].repository.password_env == "SHARED_PASSWORD"


def test_get_jobs_skips_external_job_when_volume_is_missing(
    tmp_path, monkeypatch, caplog
) -> None:
    config_path = tmp_path / "dockvault.yaml"
    config_path.write_text(
        """
jobs:
  media:
    source:
      type: files
      volume_name: missing_volume
    schedule: "0 1 * * *"
    repository:
      type: local
      path: /srv/restic/media
""".strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DOCKVAULT_CONFIG_PATH", str(config_path))
    caplog.set_level("WARNING")

    class NotFound(Exception):
        pass

    class FakeClient:
        def __init__(self) -> None:
            self.volumes = self

        def list(self, filters):
            return []

        def get(self, name):
            raise NotFound(name)

    monkeypatch.setattr("dockvault.docker.NotFound", NotFound)

    jobs = list(get_jobs(FakeClient()))

    assert jobs == []
    assert "Configured external job volume not found missing_volume" in caplog.text


def test_create_docker_client_sets_timeout(monkeypatch) -> None:
    class FakeAPI:
        timeout = 0

    class FakeClient:
        def __init__(self) -> None:
            self.api = FakeAPI()

    monkeypatch.setattr("dockvault.docker.DockerClient.from_env", lambda: FakeClient())

    client = create_docker_client()

    assert client.api.timeout == 60
