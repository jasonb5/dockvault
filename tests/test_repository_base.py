from types import SimpleNamespace

import pytest

from dockvault.models.repository import LocalRepository
from dockvault.repository.base import BaseContainerRepositoryHandler
from dockvault.repository.local import LocalRepositoryHandler


def test_get_environment_uses_password_env(monkeypatch) -> None:
    monkeypatch.setenv("RESTIC_PASSWORD", "secret")
    handler = LocalRepositoryHandler(LocalRepository(type="local", path="/repo"), SimpleNamespace())

    assert handler.get_environment() == {"RESTIC_PASSWORD": "secret"}


def test_get_environment_raises_helpful_error_when_password_is_missing(monkeypatch) -> None:
    monkeypatch.delenv("RESTIC_PASSWORD", raising=False)
    handler = LocalRepositoryHandler(LocalRepository(type="local", path="/repo"), SimpleNamespace())

    with pytest.raises(RuntimeError, match="Missing restic password environment variable RESTIC_PASSWORD"):
        handler.get_environment()


def test_launch_merges_volumes_and_removes_container(monkeypatch) -> None:
    events = []

    class FakeContainer:
        def start(self) -> None:
            events.append("start")

        def remove(self, force: bool) -> None:
            events.append(("remove", force))

    class FakeClient:
        def __init__(self) -> None:
            self.images = self
            self.containers = self
            self.available_images = set()

        def get(self, image):
            if image not in self.available_images:
                from docker.errors import ImageNotFound

                raise ImageNotFound("missing")
            return object()

        def pull(self, image) -> None:
            events.append(("pull", image))
            self.available_images.add(image)

        def create(self, image, command, environment, entrypoint, hostname, volumes):
            events.append((image, command, environment, entrypoint, hostname, volumes))
            return FakeContainer()

    class CustomHandler(BaseContainerRepositoryHandler):
        def build_volumes(self):
            return {"/repo": {"bind": "/repo", "mode": "rw"}}

    monkeypatch.setenv("RESTIC_PASSWORD", "secret")
    handler = CustomHandler(LocalRepository(type="local", path="/repo"), FakeClient())

    with handler.launch({"/data": {"bind": "/data", "mode": "ro"}}, ["-c", "restic backup"], "backup-host"):
        assert events[0] == ("pull", "restic/restic:0.19.0")
        assert events[1][0] == "restic/restic:0.19.0"
        assert events[1][1] == ["-c", "restic backup"]
        assert events[1][3] == ["/bin/sh"]
        assert events[1][4] == "backup-host"
        assert events[1][5] == {
            "/data": {"bind": "/data", "mode": "ro"},
            "/repo": {"bind": "/repo", "mode": "rw"},
        }

    assert events[2] == "start"
    assert events[-1] == ("remove", True)


def test_launch_uses_cached_restic_image_when_available(monkeypatch) -> None:
    events = []

    class FakeContainer:
        def start(self) -> None:
            events.append("start")

        def remove(self, force: bool) -> None:
            events.append(("remove", force))

    class FakeClient:
        def __init__(self) -> None:
            self.images = self
            self.containers = self
            self.available_images = {"restic/restic:0.19.0"}

        def get(self, image):
            return object()

        def pull(self, image) -> None:
            events.append(("pull", image))

        def create(self, image, command, environment, entrypoint, hostname, volumes):
            events.append((image, command, environment, entrypoint, hostname, volumes))
            return FakeContainer()

    class CustomHandler(BaseContainerRepositoryHandler):
        def build_volumes(self):
            return {"/repo": {"bind": "/repo", "mode": "rw"}}

    monkeypatch.setenv("RESTIC_PASSWORD", "secret")
    handler = CustomHandler(LocalRepository(type="local", path="/repo"), FakeClient())

    with handler.launch({"/data": {"bind": "/data", "mode": "ro"}}, ["-c", "restic backup"], "backup-host"):
        assert events[0][0] == "restic/restic:0.19.0"

    assert all(event != ("pull", "restic/restic:0.19.0") for event in events)
