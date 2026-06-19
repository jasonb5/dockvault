from dockvault.models.job import labels_to_config, parse_label_value


def test_parse_label_value_handles_lists_booleans_and_strings() -> None:
    assert parse_label_value("one, two,three") == ["one", "two", "three"]
    assert parse_label_value("true") is True
    assert parse_label_value("FALSE") is False
    assert parse_label_value("/backups") == "/backups"


def test_labels_to_config_builds_nested_config_from_dockvault_labels() -> None:
    labels = {
        "dockvault.name": "media",
        "dockvault.schedule": "0 1 * * *",
        "dockvault.source.type": "files",
        "dockvault.source.tags": "daily, weekly",
        "dockvault.repository.type": "local",
        "dockvault.repository.path": "/srv/backups",
        "dockvault.repository.enabled": "true",
        "unrelated.label": "ignored",
    }

    assert labels_to_config(labels) == {
        "name": "media",
        "schedule": "0 1 * * *",
        "source": {"type": "files", "tags": ["daily", "weekly"]},
        "repository": {
            "type": "local",
            "path": "/srv/backups",
            "enabled": True,
        },
    }
