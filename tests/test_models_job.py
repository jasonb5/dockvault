from typing import Any

import pytest
from pydantic import ValidationError

from dockvault.models.job import (
    PREFIX,
    BackupJobConfig,
    labels_to_config,
)


# ---------------------------------------------------------------------------
# labels_to_config: structural unflattening
# ---------------------------------------------------------------------------


def test_labels_to_config_unflattens_dot_keys_into_nested_dict() -> None:
    labels = {
        "dockvault.name": "media",
        "dockvault.schedule": "0 1 * * *",
        "dockvault.source.type": "files",
        "dockvault.repository.type": "local",
        "dockvault.repository.path": "/srv/backups",
        "unrelated.label": "ignored",
    }

    assert labels_to_config(labels) == {
        "name": "media",
        "schedule": "0 1 * * *",
        "source": {"type": "files"},
        "repository": {"type": "local", "path": "/srv/backups"},
    }


def test_labels_to_config_keeps_values_as_raw_strings() -> None:
    """Type coercion is Pydantic's job; this function must not guess types.

    Regression for the historical ``parse_label_value`` behaviour that
    silently converted any value containing a comma into a list and any
    ``true``/``false`` string into a bool.
    """
    labels = {
        "dockvault.schedule": "0 0 * * 1,3,5",
        "dockvault.repository.path": "/srv/odd,name",
        "dockvault.source.type": "files",
        "dockvault.source.volume_name": "vol,backup",
        "dockvault.repository.enabled": "true",
    }

    config = labels_to_config(labels)

    assert config["schedule"] == "0 0 * * 1,3,5"
    assert config["repository"]["path"] == "/srv/odd,name"
    assert config["source"]["volume_name"] == "vol,backup"
    assert config["repository"]["enabled"] == "true"


def test_labels_to_config_ignores_labels_outside_dockvault_prefix() -> None:
    labels = {
        "com.docker.compose.project": "demo",
        "maintainer": "me",
        "dockvault.schedule": "0 1 * * *",
    }

    assert labels_to_config(labels) == {"schedule": "0 1 * * *"}


def test_labels_to_config_handles_empty_input() -> None:
    assert labels_to_config({}) == {}


def test_labels_to_config_handles_deeply_nested_keys() -> None:
    labels = {"dockvault.a.b.c.d": "leaf"}

    assert labels_to_config(labels) == {"a": {"b": {"c": {"d": "leaf"}}}}


def test_labels_to_config_preserves_empty_string_values() -> None:
    labels = {"dockvault.repository.path": ""}

    assert labels_to_config(labels) == {"repository": {"path": ""}}


# ---------------------------------------------------------------------------
# labels_to_config: prefix / leaf collision detection (M3)
# ---------------------------------------------------------------------------


def test_labels_to_config_raises_when_scalar_then_nested() -> None:
    """A scalar at ``dockvault.source`` followed by ``dockvault.source.type``
    must raise, not silently TypeError deep inside ``setdefault``."""
    labels = {
        "dockvault.source": "files",
        "dockvault.source.type": "files",
    }

    with pytest.raises(ValueError) as exc_info:
        labels_to_config(labels)

    message = str(exc_info.value)
    assert "dockvault.source.type" in message
    assert "dockvault.source" in message


def test_labels_to_config_raises_when_nested_then_scalar() -> None:
    """A nested path set first followed by a scalar at the same prefix must
    raise rather than silently overwriting the nested dict."""
    labels = {
        "dockvault.source.type": "files",
        "dockvault.source": "files",
    }

    with pytest.raises(ValueError) as exc_info:
        labels_to_config(labels)

    assert "dockvault.source" in str(exc_info.value)


def test_labels_to_config_raises_when_intermediate_key_is_scalar() -> None:
    """Collision detection must fire on intermediate prefixes too, not only
    at the immediate parent."""
    labels = {
        "dockvault.repository": "local",
        "dockvault.repository.options.cache": "true",
    }

    with pytest.raises(ValueError) as exc_info:
        labels_to_config(labels)

    assert "dockvault.repository" in str(exc_info.value)


# ---------------------------------------------------------------------------
# End-to-end: labels -> BackupJobConfig.model_validate
# ---------------------------------------------------------------------------


def _validate(labels: dict[str, str]) -> BackupJobConfig:
    """Helper mirroring what ``get_jobs`` does in production."""
    config = labels_to_config(labels)
    config.setdefault("source", {}).setdefault("volume_name", "vol")
    config.setdefault("name", "vol")
    return BackupJobConfig.model_validate(config)


def test_backup_job_config_accepts_cron_with_comma_separated_minutes() -> None:
    """Regression: ``parse_label_value`` used to mangle this into a list."""
    job = _validate(
        {
            "dockvault.schedule": "0,30 * * * *",
            "dockvault.source.type": "files",
            "dockvault.repository.type": "local",
            "dockvault.repository.path": "/srv/backups",
        }
    )
    assert job.schedule == "0,30 * * * *"


def test_backup_job_config_accepts_cron_with_comma_separated_weekdays() -> None:
    job = _validate(
        {
            "dockvault.schedule": "0 0 * * 1,3,5",
            "dockvault.source.type": "files",
            "dockvault.repository.type": "local",
            "dockvault.repository.path": "/srv/backups",
        }
    )
    assert job.schedule == "0 0 * * 1,3,5"


def test_backup_job_config_accepts_scalar_string_fields_containing_commas() -> None:
    """A path that happens to contain a comma must survive end-to-end."""
    job = _validate(
        {
            "dockvault.schedule": "0 1 * * *",
            "dockvault.source.type": "files",
            "dockvault.repository.type": "local",
            "dockvault.repository.path": "/srv/odd,name",
        }
    )
    assert job.repository.path == "/srv/odd,name"


def test_backup_job_config_rejects_invalid_types() -> None:
    """Sanity check: the model still rejects truly bad input."""
    with pytest.raises(ValidationError):
        BackupJobConfig.model_validate(
            {
                "name": "x",
                "schedule": "0 1 * * *",
                "source": {"type": "files", "volume_name": "v"},
                "repository": {"type": "nonsense"},
            }
        )


def test_backup_job_config_round_trips_a_realistic_label_set() -> None:
    job = _validate(
        {
            "dockvault.name": "media",
            "dockvault.schedule": "0 1 * * *",
            "dockvault.source.type": "files",
            "dockvault.repository.type": "local",
            "dockvault.repository.path": "/srv/backups",
        }
    )
    assert job.name == "media"
    assert job.schedule == "0 1 * * *"
    assert job.source.type == "files"
    assert job.repository.type == "local"
    assert job.repository.path == "/srv/backups"


# ---------------------------------------------------------------------------
# Module surface: confirm ``parse_label_value`` is gone
# ---------------------------------------------------------------------------


def test_parse_label_value_has_been_removed() -> None:
    """The content-based type-guessing helper must not come back. If a future
    field needs list semantics, model it explicitly with a Pydantic validator
    rather than reintroducing implicit content sniffing."""
    import dockvault.models.job as job_module

    assert not hasattr(job_module, "parse_label_value")


def test_prefix_constant_is_exported() -> None:
    assert PREFIX == "dockvault."


# ---------------------------------------------------------------------------
# Defensive: labels_to_config does not mutate its input
# ---------------------------------------------------------------------------


def test_labels_to_config_does_not_mutate_input_labels() -> None:
    labels: dict[str, Any] = {
        "dockvault.schedule": "0 1 * * *",
        "dockvault.source.type": "files",
    }
    snapshot = dict(labels)

    labels_to_config(labels)

    assert labels == snapshot
