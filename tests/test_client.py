from urllib.error import URLError

import pytest

import dockvault.client as client


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_resolve_server_url_uses_explicit_value() -> None:
    assert client.resolve_server_url("http://dockvault:8000/") == "http://dockvault:8000"


def test_resolve_server_url_uses_environment(monkeypatch) -> None:
    monkeypatch.setenv("DOCKVAULT_SERVER_URL", "http://dockvault:8000/")

    assert client.resolve_server_url(None) == "http://dockvault:8000"


def test_resolve_server_url_raises_when_missing(monkeypatch) -> None:
    monkeypatch.delenv("DOCKVAULT_SERVER_URL", raising=False)

    with pytest.raises(client.DockvaultClientError, match="Missing Dockvault server URL"):
        client.resolve_server_url(None)


def test_get_json_fetches_remote_payload(monkeypatch) -> None:
    seen = {}

    def _urlopen(request):
        seen["url"] = request.full_url
        seen["authorization"] = request.get_header("Authorization")
        return _FakeResponse(b'{"jobs": []}')

    monkeypatch.setattr(client, "urlopen", _urlopen)

    assert client.get_json("http://dockvault:8000/", "/jobs") == {"jobs": []}
    assert seen["url"] == "http://dockvault:8000/jobs"
    assert seen["authorization"] is None


def test_get_json_sends_bearer_token_when_configured(monkeypatch) -> None:
    seen = {}

    def _urlopen(request):
        seen["authorization"] = request.get_header("Authorization")
        return _FakeResponse(b'{"jobs": []}')

    monkeypatch.setenv("DOCKVAULT_API_TOKEN", "secret-token")
    monkeypatch.setattr(client, "urlopen", _urlopen)

    assert client.get_json("http://dockvault:8000/", "/jobs") == {"jobs": []}
    assert seen["authorization"] == "Bearer secret-token"


def test_get_json_raises_clean_error_when_server_is_unreachable(monkeypatch) -> None:
    def _urlopen(request):
        raise URLError("connection refused")

    monkeypatch.setattr(client, "urlopen", _urlopen)

    with pytest.raises(client.DockvaultClientError, match="Failed to reach Dockvault server"):
        client.get_json("http://dockvault:8000", "/jobs")


def test_restore_posts_remote_payload(monkeypatch) -> None:
    seen = {}

    def _urlopen(request):
        seen["url"] = request.full_url
        seen["body"] = request.data.decode("utf-8")
        return _FakeResponse(b'{"status": "ok"}')

    monkeypatch.setattr(client, "urlopen", _urlopen)

    assert client.restore(
        "http://dockvault:8000",
        "alpha",
        "latest",
        "restore-target",
        "/photos/2024",
        False,
        True,
    ) == {"status": "ok"}
    assert seen["url"] == "http://dockvault:8000/jobs/alpha/restore"
    assert seen["body"] == (
        '{"snapshot": "latest", "target_volume": "restore-target", "path": "/photos/2024", "allow_in_place": false, "dry_run": true}'
    )


def test_backup_posts_remote_payload(monkeypatch) -> None:
    seen = {}

    def _urlopen(request):
        seen["url"] = request.full_url
        seen["body"] = request.data.decode("utf-8")
        return _FakeResponse(b'{"status": "ok"}')

    monkeypatch.setattr(client, "urlopen", _urlopen)

    assert client.backup("http://dockvault:8000", "alpha") == {"status": "ok"}
    assert seen["url"] == "http://dockvault:8000/jobs/alpha/backup"
    assert seen["body"] == '{}'


def test_check_posts_remote_payload(monkeypatch) -> None:
    seen = {}

    def _urlopen(request):
        seen["url"] = request.full_url
        seen["body"] = request.data.decode("utf-8")
        return _FakeResponse(b'{"status": "ok"}')

    monkeypatch.setattr(client, "urlopen", _urlopen)

    assert client.check("http://dockvault:8000", "alpha") == {"status": "ok"}
    assert seen["url"] == "http://dockvault:8000/jobs/alpha/check"
    assert seen["body"] == '{}'
