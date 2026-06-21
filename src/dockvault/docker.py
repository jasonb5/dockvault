import json
import logging
from collections.abc import Iterator

from docker import DockerClient
from docker.errors import APIError
from pydantic import ValidationError

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


def get_jobs(client: DockerClient, labels: list[str] | None = None) -> Iterator[BackupJobConfig]:
    if labels is None:
        labels = []

    labels.append("dockvault.enabled")

    filters = {
        "label": labels,
    }

    try:
        raw_volumes = client.volumes.list(filters=filters)
    except APIError as e:
        logger.warning("Failed to list docker volumes %s", e)
        raise JobDiscoveryError("failed to list docker volumes") from e

    for volume in raw_volumes:
        try:
            config = labels_to_config(volume.attrs["Labels"])
        except Exception:
            logger.warning("Failed to convert labels for volume %s to config", volume.name)

            continue

        try:
            config["source"]["volume_name"] = volume.name
        except KeyError:
            config["source"] = {"volume_name": volume.name}

        if not config.get("name"):
            config["name"] = volume.name

        try:
            yield BackupJobConfig.model_validate(config)
        except ValidationError:
            logger.warning("Failed to parse labels %s", json.dumps(config))
