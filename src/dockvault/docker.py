from docker import DockerClient

from dockvault.models.job import BackupJobConfig, labels_to_config


def get_jobs(client: DockerClient, labels: list[str] | None = None) -> list[BackupJobConfig]:
    jobs: list[BackupJobConfig] = []

    if labels is None:
        labels = []

    labels.append("dockvault.enabled")

    filters = {
        "label": labels,
    }
    raw_volumes = client.volumes.list(filters=filters)

    for volume in raw_volumes:
        config = labels_to_config(volume.attrs["Labels"])
        config["source"]["volume_name"] = volume.name

        if not config.get("name"):
            config["name"] = volume.name

        job = BackupJobConfig.model_validate(config)

        jobs.append(job)

    return jobs
