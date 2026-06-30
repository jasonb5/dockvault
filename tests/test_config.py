import textwrap

import pytest

from dockvault.config import ExternalConfigError, load_external_job_configs, matches_label_filters


def test_load_external_job_configs_applies_defaults(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "dockvault.yaml"
    config_path.write_text(
        textwrap.dedent(
            """
            defaults:
              repository:
                type: local
                password_env: SHARED_PASSWORD
              retention:
                keep_daily: 14
            jobs:
              media:
                source:
                  type: files
                  volume_name: media_data
                schedule: "0 1 * * *"
                repository:
                  path: /srv/restic/media
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DOCKVAULT_CONFIG_PATH", str(config_path))

    configs = load_external_job_configs()

    assert configs == {
        "media_data": {
            "name": "media",
            "source": {"type": "files", "volume_name": "media_data"},
            "schedule": "0 1 * * *",
            "repository": {
                "type": "local",
                "path": "/srv/restic/media",
                "password_env": "SHARED_PASSWORD",
            },
            "retention": {"keep_daily": 14},
        }
    }


def test_load_external_job_configs_rejects_non_mapping_defaults(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "dockvault.yaml"
    config_path.write_text("defaults: []\n", encoding="utf-8")
    monkeypatch.setenv("DOCKVAULT_CONFIG_PATH", str(config_path))

    with pytest.raises(ExternalConfigError, match="'defaults' must be a mapping"):
        load_external_job_configs()


def test_matches_label_filters_treats_external_job_as_enabled() -> None:
    config = {
        "name": "media",
        "source": {"type": "files", "volume_name": "media_data"},
        "schedule": "0 1 * * *",
        "repository": {"type": "local", "path": "/repo"},
    }

    assert matches_label_filters(
        config,
        ["dockvault.enabled", "dockvault.name=media"],
        volume_labels={},
        has_external_config=True,
    )
