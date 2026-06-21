# dockvault

Minimal modern Python project scaffold.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
pytest
python -m dockvault
```

## Releases

Pushing a Git tag like `v0.1.0` publishes a versioned container image to
`ghcr.io/<owner>/dockvault` with semver tags such as `0.1.0` and `0.1`.
