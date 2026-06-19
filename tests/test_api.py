import asyncio
from types import SimpleNamespace

import pytest

from dockvault.api import lifespan


def test_lifespan_starts_scheduler_sets_state_and_shuts_down(monkeypatch) -> None:
    events = []

    class FakeScheduler:
        def start(self) -> None:
            events.append("start")

        def shutdown(self, wait: bool) -> None:
            events.append(("shutdown", wait))

    scheduler = FakeScheduler()
    app = SimpleNamespace(state=SimpleNamespace())

    monkeypatch.setattr("dockvault.api.create_scheduler", lambda: scheduler)

    async def run() -> None:
        async with lifespan(app):
            assert app.state.scheduler is scheduler
            assert events == ["start"]

    asyncio.run(run())

    assert events == ["start", ("shutdown", False)]


def test_lifespan_shuts_down_scheduler_when_context_errors(monkeypatch) -> None:
    events = []

    class FakeScheduler:
        def start(self) -> None:
            events.append("start")

        def shutdown(self, wait: bool) -> None:
            events.append(("shutdown", wait))

    monkeypatch.setattr("dockvault.api.create_scheduler", lambda: FakeScheduler())

    async def run() -> None:
        app = SimpleNamespace(state=SimpleNamespace())

        async with lifespan(app):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(run())

    assert events == ["start", ("shutdown", False)]
