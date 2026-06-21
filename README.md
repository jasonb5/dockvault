# dockvault

Dockvault discovers backup jobs from Docker volume labels and runs `restic`
backups for them on a schedule.

Today the project supports:
- Source type: `files`
- Repository type: `local`
- API health endpoints for container orchestration
- A container image published to GitHub Container Registry

## How It Works

Dockvault watches Docker volumes for labels prefixed with `dockvault.`.
Every matching volume becomes a backup job.

The server process:
- starts a FastAPI app on port `8000`
- starts an APScheduler instance in the same process
- reconciles backup jobs from Docker every `60` seconds
- schedules each job with its `dockvault.schedule` cron expression in `UTC`

When a job runs, Dockvault launches a short-lived `restic/restic:0.19.0`
container, mounts:
- the source volume at `/data` read-only
- the local repository path at `/repo` read-write

The backup command it builds for `files` sources is:

```sh
restic -r /repo backup --host <hostname> --tag <volume-name> --json /data
```

If no hostname override is configured, Dockvault uses the machine hostname.

## Job Configuration

Jobs are configured entirely with Docker volume labels.

Required labels:
- `dockvault.enabled`
- `dockvault.schedule`
- `dockvault.source.type=files`
- `dockvault.repository.type=local`
- `dockvault.repository.path=/absolute/path/to/repo`

Optional labels:
- `dockvault.name=<job-name>`
  If omitted, the Docker volume name is used.
- `dockvault.source.volume_name=<volume-name>`
  Usually not needed because Dockvault fills this from the Docker volume.
- `dockvault.repository.password_env=<ENV_VAR_NAME>`
  Defaults to `RESTIC_PASSWORD`.

Example:

```sh
docker volume create \
  --label dockvault.enabled=true \
  --label dockvault.name=media-nightly \
  --label dockvault.schedule="0 1 * * *" \
  --label dockvault.source.type=files \
  --label dockvault.repository.type=local \
  --label dockvault.repository.path=/srv/restic/media \
  media
```

## Environment Variables

Required for backups:
- `RESTIC_PASSWORD`
  Default password variable consumed by the restic container.

Optional:
- `DOCKVAULT_HOSTNAME`
  Hostname to attach to backups. If unset, Dockvault uses the current host
  name.

If you set `dockvault.repository.password_env`, that variable must also be set
in the Dockvault process environment.

## CLI

Install locally:

```bash
uv sync --dev
```

Show available commands:

```bash
uv run dockvault --help
uv run dockvault backup --help
```

Common commands:

```bash
uv run dockvault version
uv run dockvault server
uv run dockvault backup list-jobs
uv run dockvault backup create media-nightly
uv run dockvault backup create media-nightly custom-hostname
```

Command behavior:
- `dockvault server` starts the API and scheduler on `0.0.0.0:8000`
- `dockvault backup list-jobs` prints discovered job names
- `dockvault backup create <name> [hostname]` runs matching jobs immediately

## API

Endpoints:
- `GET /health`
  Returns `200` with `{"status":"ok"}` when the process is alive.
- `GET /ready`
  Returns readiness based on scheduler state and Docker job discovery.

`/ready` failure reasons currently include:
- `scheduler_unavailable`
- `scheduler_stopped`
- `docker_unavailable`
- `job_discovery_failed`

Run locally:

```bash
uv run dockvault server
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
```

## Local Development

Set up a development environment:

```bash
uv sync --dev
```

Run tests:

```bash
uv run pytest
```

Build the local container image:

```bash
docker build -t dockvault:test .
```

## Running As A Container

The repository contains a production Dockerfile for the `dockvault server`
process.

An example Compose deployment is included at `compose.example.yaml`.

## Deployment Requirements

Dockvault assumes the following at runtime:

1. Docker API access is available inside the Dockvault process.
2. The process can read Docker volumes and create short-lived backup
   containers.
3. Every local restic repository path referenced by job labels exists inside
   the Dockvault container at the same absolute path used in the label.
4. The restic password environment variable required by each job is present in
   the Dockvault process environment.
5. Port `8000` is reachable if you want health or readiness probing.

Required runtime inputs:

- Docker socket mount: `/var/run/docker.sock:/var/run/docker.sock`
- Repository path mounts for every `dockvault.repository.path` in use
- `RESTIC_PASSWORD`, or whichever variable name is configured through
  `dockvault.repository.password_env`

Optional runtime inputs:

- `DOCKVAULT_HOSTNAME` to override the host name recorded in restic snapshots

Operational assumptions:

- schedules are interpreted in `UTC`
- Dockvault reconciles Docker jobs every `60` seconds
- backup jobs run in transient `restic/restic:0.19.0` containers
- the service must be allowed to create and remove those transient containers

Build locally:

```bash
docker build -t dockvault:test .
```

Run the example deployment:

```bash
docker compose -f compose.example.yaml up -d --build
```

Run locally:

```bash
docker run --rm \
  -p 8000:8000 \
  -e RESTIC_PASSWORD=secret \
  -e DOCKVAULT_HOSTNAME=dockvault \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /srv/restic:/srv/restic \
  dockvault:test
```

Notes:
- The container must be able to reach the Docker daemon.
- Any local repository path referenced by `dockvault.repository.path` must be
  mounted into the Dockvault container at the same absolute path.
- The image exposes port `8000` and includes a Docker `HEALTHCHECK` against
  `/health`.
- The checked-in Compose example only deploys Dockvault itself. Backup jobs are
  created separately by labeling Docker volumes.

## CI And Publishing

GitHub Actions workflows are included for:
- tests
- container build validation
- container publishing to GitHub Container Registry

Published image name:

```text
ghcr.io/<owner>/dockvault
```

Workflow behavior:
- Pull requests build the image but do not push it.
- Pushes to `main` publish branch and sha tags.
- Pushes of Git tags matching `v*` publish versioned image tags.

Examples:
- Git ref `main` publishes branch and sha tags.
- Git tag `v0.1.0` publishes image tags including `0.1.0` and `0.1`.

## Limitations

Current scope is intentionally small:
- only Docker volumes are discovered as sources
- only `files` sources are supported
- only local restic repositories are supported
- schedules are interpreted as UTC cron expressions

## Versioning

The Python package version is derived from git metadata via `hatch-vcs`.

That means:
- local development builds can produce development-style versions
- release tags are the source of truth for published image versions
- the CLI `dockvault version` prints the installed package version
