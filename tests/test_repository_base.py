from types import SimpleNamespace

from dockvault.models.repository import LocalRepository
from dockvault.repository.base import BaseContainerRepositoryHandler
from dockvault.repository.local import LocalRepositoryHandler


def test_get_environment_uses_password_env(monkeypatch) -> None:
    monkeypatch.setenv("RESTIC_PASSWORD", "secret")
    handler = LocalRepositoryHandler(LocalRepository(type="local", path="/repo"), SimpleNamespace())

    assert handler.get_environment() == {"RESTIC_PASSWORD": "secret"}


def test_launch_merges_volumes_and_removes_container(monkeypatch) -> None:
    events = []

    class FakeContainer:
        def start(self) -> None:
            events.append("start")

        def remove(self, force: bool) -> None:
            events.append(("remove", force))

    class FakeClient:
        def __init__(self) -> None:
            self.containers = self

        def create(self, image, command, environment, volumes):
            events.append((image, command, environment, volumes))
            return FakeContainer()

    class CustomHandler(BaseContainerRepositoryHandler):
        def build_volumes(self):
            return {"/repo": {"bind": "/repo", "mode": "rw"}}

    monkeypatch.setenv("RESTIC_PASSWORD", "secret")
    handler = CustomHandler(LocalRepository(type="local", path="/repo"), FakeClient())

    with handler.launch({"/data": {"bind": "/data", "mode": "ro"}}):
        assert events[0][3] == {
            "/data": {"bind": "/data", "mode": "ro"},
            "/repo": {"bind": "/repo", "mode": "rw"},
        }

    assert events[1] == "start"
    assert events[-1] == ("remove", True)
