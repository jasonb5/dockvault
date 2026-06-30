import json
import logging
from collections.abc import Iterator

from docker import DockerClient
from docker.errors import APIError, NotFound
from pydantic import ValidationError

from dockvault.config import (
    ExternalConfigError,
    load_external_job_configs,
    load_server_default_job_config,
    matches_label_filters,
    merge_job_config,
)
from dockvault.models.job import BackupJobConfig, labels_to_config

logger = logging.getLogger(__name__)
DOCKER_TIMEOUT_SECONDS = 60


class JobDiscoveryError(RuntimeError):
    pass


def create_docker_client() -> DockerClient:
    client = DockerClient.from_env()

    if hasattr(client, "api") and hasattr(client.api, "timeout"):
        client.api.timeout = DOCKER_TIMEOUT_SECONDS

    return client


def list_volumes(client: DockerClient, filters: dict | None = None) -> list:
    try:
        return list(client.volumes.list(filters=filters))
    except APIError as e:
        logger.warning("Failed to list docker volumes %s", e)
        raise JobDiscoveryError("failed to list docker volumes") from e


def get_jobs(client: DockerClient, labels: list[str] | None = None) -> Iterator[BackupJobConfig]:
    requested_labels = list(labels or [])
    default_config = load_server_default_job_config()

    try:
        external_configs = load_external_job_configs()
    except ExternalConfigError as exc:
        logger.warning("Failed to load external job config %s", exc)
        raise JobDiscoveryError("failed to load external job config") from exc

    try:
        raw_volumes = list_volumes(client, filters={"label": ["dockvault.enabled"]})
    except JobDiscoveryError:
        raise

    volumes_by_name = {volume.name: volume for volume in raw_volumes}

    for volume_name in external_configs:
        if volume_name in volumes_by_name:
            continue

        try:
            volumes_by_name[volume_name] = client.volumes.get(volume_name)
        except NotFound:
            logger.warning("Configured external job volume not found %s", volume_name)
        except APIError as exc:
            logger.warning("Failed to inspect configured external job volume %s: %s", volume_name, exc)
            raise JobDiscoveryError("failed to inspect configured external job volume") from exc

    for volume in volumes_by_name.values():
        volume_labels = volume.attrs.get("Labels") or {}
        has_external_config = volume.name in external_configs

        try:
            config = labels_to_config(volume_labels)
        except Exception:
            logger.warning("Failed to convert labels for volume %s to config", volume.name)

            continue

        config = merge_job_config(default_config, config)

        if has_external_config:
            config = merge_job_config(config, external_configs[volume.name])

        try:
            config["source"]["volume_name"] = volume.name
        except KeyError:
            config["source"] = {"volume_name": volume.name}

        if not config.get("name"):
            config["name"] = volume.name

        if not matches_label_filters(
            config,
            requested_labels,
            volume_labels=volume_labels,
            has_external_config=has_external_config,
        ):
            continue

        try:
            yield BackupJobConfig.model_validate(config)
        except ValidationError:
            logger.warning("Failed to parse labels %s", json.dumps(config))
