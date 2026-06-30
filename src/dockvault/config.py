import copy
import logging
import os
from pathlib import Path
from typing import Any

import yaml

from dockvault.models.job import PREFIX, labels_to_config

logger = logging.getLogger(__name__)

CONFIG_PATH_ENV = "DOCKVAULT_CONFIG_PATH"
DEFAULT_SOURCE_TYPE_ENV = "DOCKVAULT_DEFAULT_SOURCE_TYPE"
DEFAULT_REPOSITORY_TYPE_ENV = "DOCKVAULT_DEFAULT_REPOSITORY_TYPE"
DEFAULT_REPOSITORY_PASSWORD_ENV = "DOCKVAULT_DEFAULT_REPOSITORY_PASSWORD_ENV"
DEFAULT_RETENTION_ENVS = {
    "keep_last": "DOCKVAULT_DEFAULT_RETENTION_KEEP_LAST",
    "keep_daily": "DOCKVAULT_DEFAULT_RETENTION_KEEP_DAILY",
    "keep_weekly": "DOCKVAULT_DEFAULT_RETENTION_KEEP_WEEKLY",
    "keep_monthly": "DOCKVAULT_DEFAULT_RETENTION_KEEP_MONTHLY",
    "keep_yearly": "DOCKVAULT_DEFAULT_RETENTION_KEEP_YEARLY",
}


class ExternalConfigError(RuntimeError):
    pass


def load_server_default_job_config() -> dict[str, Any]:
    config: dict[str, Any] = {}

    source_type = _get_env_value(DEFAULT_SOURCE_TYPE_ENV)
    if source_type is not None:
        config["source"] = {"type": source_type}

    repository: dict[str, Any] = {}

    repository_type = _get_env_value(DEFAULT_REPOSITORY_TYPE_ENV)
    if repository_type is not None:
        repository["type"] = repository_type

    repository_password_env = _get_env_value(DEFAULT_REPOSITORY_PASSWORD_ENV)
    if repository_password_env is not None:
        repository["password_env"] = repository_password_env

    if repository:
        config["repository"] = repository

    retention: dict[str, Any] = {}
    for key, env_name in DEFAULT_RETENTION_ENVS.items():
        value = _get_env_value(env_name)
        if value is not None:
            retention[key] = value

    if retention:
        config["retention"] = retention

    return config


def build_scaffold_config(
    volumes: list[Any],
    *,
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
) -> dict[str, Any]:
    server_defaults = load_server_default_job_config()
    source_defaults = server_defaults.get("source", {})
    repository_defaults = server_defaults.get("repository", {})
    retention_defaults = copy.deepcopy(server_defaults.get("retention", {}))

    if source_type is not None:
        source_defaults = merge_job_config(source_defaults, {"type": source_type})

    repository_override: dict[str, Any] = {}
    if repository_type is not None:
        repository_override["type"] = repository_type
    if repository_password_env is not None:
        repository_override["password_env"] = repository_password_env
    if repository_override:
        repository_defaults = merge_job_config(repository_defaults, repository_override)

    for key, value in (
        ("keep_last", retention_keep_last),
        ("keep_daily", retention_keep_daily),
        ("keep_weekly", retention_keep_weekly),
        ("keep_monthly", retention_keep_monthly),
        ("keep_yearly", retention_keep_yearly),
    ):
        if value is not None:
            retention_defaults[key] = value

    defaults: dict[str, Any] = {
        "source": {
            "type": str(source_defaults.get("type", "files")),
        },
        "repository": {
            "type": str(repository_defaults.get("type", "local")),
            "path": repository_root,
            "password_env": str(repository_defaults.get("password_env", "RESTIC_PASSWORD")),
        },
    }
    if retention_defaults:
        defaults["retention"] = copy.deepcopy(retention_defaults)

    jobs: dict[str, Any] = {}

    for volume in sorted(volumes, key=lambda item: item.name):
        labels = volume.attrs.get("Labels") or {}
        label_config = labels_to_config(labels)
        repository_config = label_config.get("repository", {})
        source_config = label_config.get("source", {})
        retention_config = label_config.get("retention")

        job_name = str(label_config.get("name") or volume.name)
        job: dict[str, Any] = {
            "source": {
                "volume_name": volume.name,
            },
            "schedule": str(label_config.get("schedule") or schedule),
        }

        job_repository: dict[str, Any] = {}
        repository_path = repository_config.get("path")
        if repository_path not in (None, defaults["repository"]["path"]):
            job_repository["path"] = str(repository_path)

        if source_config.get("type") not in (None, defaults["source"]["type"]):
            job["source"]["type"] = str(source_config["type"])

        for key in ("type", "password_env"):
            value = repository_config.get(key)
            if value not in (None, defaults["repository"][key]):
                job_repository[key] = str(value)

        if job_repository:
            job["repository"] = job_repository

        if retention_config:
            job["retention"] = copy.deepcopy(retention_config)

        jobs[job_name] = job

    return {
        "defaults": defaults,
        "jobs": jobs,
    }


def render_scaffold_config(
    volumes: list[Any],
    *,
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
) -> str:
    return yaml.safe_dump(
        build_scaffold_config(
            volumes,
            schedule=schedule,
            repository_root=repository_root,
            source_type=source_type,
            repository_type=repository_type,
            repository_password_env=repository_password_env,
            retention_keep_last=retention_keep_last,
            retention_keep_daily=retention_keep_daily,
            retention_keep_weekly=retention_keep_weekly,
            retention_keep_monthly=retention_keep_monthly,
            retention_keep_yearly=retention_keep_yearly,
        ),
        sort_keys=False,
    )


def load_external_job_configs() -> dict[str, dict[str, Any]]:
    path = os.getenv(CONFIG_PATH_ENV)

    if path is None or not path.strip():
        return {}

    config_path = Path(path).expanduser()

    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ExternalConfigError(f"external config file not found: {config_path}") from exc
    except OSError as exc:
        raise ExternalConfigError(f"failed to read external config file: {config_path}") from exc
    except yaml.YAMLError as exc:
        raise ExternalConfigError(f"failed to parse external config file: {config_path}") from exc

    if payload is None:
        return {}

    if not isinstance(payload, dict):
        raise ExternalConfigError("external config root must be a mapping")

    defaults = payload.get("defaults", {})
    if defaults is None:
        defaults = {}

    if not isinstance(defaults, dict):
        raise ExternalConfigError("external config 'defaults' must be a mapping")

    jobs = payload.get("jobs", {})
    if jobs is None:
        return {}

    if not isinstance(jobs, dict):
        raise ExternalConfigError("external config 'jobs' must be a mapping")

    configs_by_volume: dict[str, dict[str, Any]] = {}

    for job_key, raw_job in jobs.items():
        if not isinstance(raw_job, dict):
            logger.warning("Skipping external job config %r: job config must be a mapping", job_key)
            continue

        source = raw_job.get("source")
        if not isinstance(source, dict):
            logger.warning("Skipping external job config %r: source must be a mapping", job_key)
            continue

        volume_name = source.get("volume_name")
        if not isinstance(volume_name, str) or not volume_name.strip():
            logger.warning(
                "Skipping external job config %r: source.volume_name must be a non-empty string",
                job_key,
            )
            continue

        normalized_volume_name = volume_name.strip()
        if normalized_volume_name in configs_by_volume:
            logger.warning(
                "Skipping external job config %r: duplicate source.volume_name=%r",
                job_key,
                normalized_volume_name,
            )
            continue

        config = merge_job_config(defaults, raw_job)
        config.setdefault("name", str(job_key))
        configs_by_volume[normalized_volume_name] = config

    return configs_by_volume


def merge_job_config(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)

    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_job_config(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)

    return merged


def _get_env_value(name: str) -> str | None:
    value = os.getenv(name)

    if value is None:
        return None

    normalized = value.strip()

    if not normalized:
        return None

    return normalized
def matches_label_filters(
    config: dict[str, Any],
    filters: list[str],
    *,
    volume_labels: dict[str, str],
    has_external_config: bool,
) -> bool:
    for raw_filter in filters:
        if "=" not in raw_filter:
            if raw_filter == f"{PREFIX}enabled":
                if has_external_config or raw_filter in volume_labels:
                    continue
                return False

            if raw_filter in volume_labels:
                continue

            return False

        key, expected = raw_filter.split("=", 1)
        if not key.startswith(PREFIX):
            return False

        current: Any = config
        matched = True

        for part in key.removeprefix(PREFIX).split("."):
            if not isinstance(current, dict) or part not in current:
                matched = False
                break
            current = current[part]

        if not matched or str(current) != expected:
            return False

    return True
