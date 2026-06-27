import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class DockvaultClientError(RuntimeError):
    pass


def resolve_server_url(server: str | None) -> str:
    value = server or os.getenv("DOCKVAULT_SERVER_URL")

    if value is None or not value.strip():
        raise DockvaultClientError(
            "Missing Dockvault server URL. Use --server or set DOCKVAULT_SERVER_URL.",
        )

    return value.rstrip("/")


def get_json(server: str | None, path: str) -> dict:
    base_url = resolve_server_url(server)
    request = Request(f"{base_url}{path}", headers={"Accept": "application/json"})

    try:
        with urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace")
        raise DockvaultClientError(f"Server returned HTTP {exc.code}: {message}") from exc
    except URLError as exc:
        raise DockvaultClientError(f"Failed to reach Dockvault server: {exc.reason}") from exc


def get_jobs(server: str | None) -> dict:
    return get_json(server, "/jobs")


def get_job(server: str | None, name: str) -> dict:
    return get_json(server, f"/jobs/{quote(name, safe='')}")


def get_snapshots(server: str | None, name: str) -> dict:
    return get_json(server, f"/jobs/{quote(name, safe='')}/snapshots")


def get_history(server: str | None, name: str) -> dict:
    return get_json(server, f"/jobs/{quote(name, safe='')}/history")
