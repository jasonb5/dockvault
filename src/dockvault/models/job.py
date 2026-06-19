from typing import Any

from pydantic import BaseModel

from dockvault.models.repository import BackupRepository
from dockvault.models.source import BackupSource

PREFIX = "dockvault."


class BackupJobConfig(BaseModel):
    name: str | None
    source: BackupSource
    repository: BackupRepository
    schedule: str


def labels_to_config(labels: dict[str, str]) -> dict[str, Any]:
    config: dict[str, Any] = {}

    for key, value in labels.items():
        if not key.startswith(PREFIX):
            continue

        parts = key.removeprefix(PREFIX).split(".")
        current = config

        for part in parts[:-1]:
            current = current.setdefault(part, {})

        current[parts[-1]] = parse_label_value(value)

    return config


def parse_label_value(value: str) -> Any:
    if "," in value:
        return [item.strip() for item in value.split(",") if item.strip()]

    if value.lower() in {"true", "false"}:
        return value.lower() == "true"

    return value
