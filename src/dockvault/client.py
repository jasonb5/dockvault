import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
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


def resolve_api_token() -> str | None:
    value = os.getenv("DOCKVAULT_API_TOKEN")

    if value is None or not value.strip():
        return None

    return value.strip()


def get_json(server: str | None, path: str) -> dict:
    return _request_json(server, path, None)


def post_json(server: str | None, path: str, payload: dict) -> dict:
    return _request_json(server, path, payload)


def _request_json(server: str | None, path: str, payload: dict | None) -> dict:
    base_url = resolve_server_url(server)
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    api_token = resolve_api_token()

    if api_token is not None:
        headers["Authorization"] = f"Bearer {api_token}"

    request = Request(
        f"{base_url}{path}",
        data=data,
        headers=headers,
    )

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


def get_config_scaffold(
    server: str | None,
    schedule: str,
    repository_root: str,
    source_type: str | None = None,
    repository_type: str | None = None,
    repository_password_env: str | None = None,
    retention_keep_last: int | None = None,
    retention_keep_daily: int | None = None,
    retention_keep_weekly: int | None = None,
    retention_keep_monthly: int | None = None,
    retention_keep_yearly: int | None = None,
) -> dict:
    query_params = {
        "schedule": schedule,
        "repository_root": repository_root,
        "source_type": source_type,
        "repository_type": repository_type,
        "repository_password_env": repository_password_env,
        "retention_keep_last": retention_keep_last,
        "retention_keep_daily": retention_keep_daily,
        "retention_keep_weekly": retention_keep_weekly,
        "retention_keep_monthly": retention_keep_monthly,
        "retention_keep_yearly": retention_keep_yearly,
    }
    query = urlencode({key: value for key, value in query_params.items() if value is not None})
    return get_json(server, f"/config/scaffold?{query}")


def get_job(server: str | None, name: str) -> dict:
    return get_json(server, f"/jobs/{quote(name, safe='')}")


def get_snapshots(server: str | None, name: str) -> dict:
    return get_json(server, f"/jobs/{quote(name, safe='')}/snapshots")


def get_history(server: str | None, name: str) -> dict:
    return get_json(server, f"/jobs/{quote(name, safe='')}/history")


def backup(server: str | None, name: str) -> dict:
    return post_json(server, f"/jobs/{quote(name, safe='')}/backup", {})


def check(server: str | None, name: str) -> dict:
    return post_json(server, f"/jobs/{quote(name, safe='')}/check", {})


def restore(
    server: str | None,
    name: str,
    snapshot: str,
    target_volume: str | None,
    path: str | None,
    allow_in_place: bool,
    dry_run: bool,
) -> dict:
    return post_json(
        server,
        f"/jobs/{quote(name, safe='')}/restore",
        {
            "snapshot": snapshot,
            "target_volume": target_volume,
            "path": path,
            "allow_in_place": allow_in_place,
            "dry_run": dry_run,
        },
    )
