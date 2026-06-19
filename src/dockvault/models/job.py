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
    """Unflatten ``dockvault.*`` Docker labels into a nested config dict.

    Values are kept as raw strings; any type coercion (bool, int, list, ...)
    is delegated to the Pydantic model that ultimately validates the result.
    This avoids the historical bug where any value containing a comma was
    silently turned into a list regardless of the target field's type.

    Raises ``ValueError`` when two labels disagree about whether a key is a
    scalar value or a nested object (e.g. both ``dockvault.source`` and
    ``dockvault.source.type`` are set).
    """
    config: dict[str, Any] = {}

    for key, value in labels.items():
        if not key.startswith(PREFIX):
            continue

        parts = key.removeprefix(PREFIX).split(".")
        current: dict[str, Any] = config

        for depth, part in enumerate(parts[:-1]):
            existing = current.get(part)
            if existing is None:
                current[part] = {}
            elif not isinstance(existing, dict):
                conflict = PREFIX + ".".join(parts[: depth + 1])
                raise ValueError(
                    f"label {key!r} conflicts with {conflict!r}: "
                    f"{conflict!r} was set as a scalar but {key!r} requires "
                    f"a nested object at that path",
                )
            current = current[part]

        leaf = parts[-1]
        if isinstance(current.get(leaf), dict):
            raise ValueError(
                f"label {key!r} conflicts with nested labels already set "
                f"under {key + '.'!r}: cannot overwrite a nested object with "
                f"a scalar value",
            )
        current[leaf] = value

    return config
