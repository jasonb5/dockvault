import copy
import logging
import os
from pathlib import Path
from typing import Any

import yaml

from dockvault.models.job import PREFIX

logger = logging.getLogger(__name__)

CONFIG_PATH_ENV = "DOCKVAULT_CONFIG_PATH"


class ExternalConfigError(RuntimeError):
    pass


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
