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
        return _FakeResponse(b'{"jobs": []}')

    monkeypatch.setattr(client, "urlopen", _urlopen)

    assert client.get_json("http://dockvault:8000/", "/jobs") == {"jobs": []}
    assert seen["url"] == "http://dockvault:8000/jobs"


def test_get_json_raises_clean_error_when_server_is_unreachable(monkeypatch) -> None:
    def _urlopen(request):
        raise URLError("connection refused")

    monkeypatch.setattr(client, "urlopen", _urlopen)

    with pytest.raises(client.DockvaultClientError, match="Failed to reach Dockvault server"):
        client.get_json("http://dockvault:8000", "/jobs")
