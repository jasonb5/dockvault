# Releasing Dockvault

Dockvault releases are tag-driven.

The version is not manually edited in source files before release:
- `pyproject.toml` uses dynamic versioning through `hatch-vcs`
- container image version tags are derived from the git tag
- `dockvault version` reads the installed package version from package metadata

## Release Steps

1. Make sure the target commits are on `main`.
2. Make sure tests and container build changes are passing.
3. Create a semantic version tag in the form `vX.Y.Z`.
4. Push the tag.
5. Verify the GitHub Actions workflow published the image.

Example:

```bash
git checkout main
git pull
git tag v0.2.0
git push origin v0.2.0
```

## What The Tag Triggers

Pushing a tag like `v0.2.0` triggers the container workflow and publishes
versioned images to:

```text
ghcr.io/<owner>/dockvault
```

Expected image tags include:
- `0.2.0`
- `0.2`

Branch pushes to `main` still publish branch and sha tags, but the release tag
is the source of truth for versioned images.

## Verification Checklist

After pushing the tag, verify:
- the `Container` GitHub Actions workflow ran successfully
- the workflow created the expected GHCR image tags
- the image can be pulled successfully

Example pull check:

```bash
docker pull ghcr.io/<owner>/dockvault:0.2.0
```

## Notes

- Non-tag image builds embed package version `0.0.0` inside the image by
  default because they build without git metadata.
- Tagged releases pass the release version into the Docker build so the
  installed package metadata inside the image matches the release tag.
