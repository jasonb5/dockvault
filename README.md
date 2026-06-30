# dockvault

Dockvault discovers backup jobs from Docker volume labels and optional
server-side config, then runs `restic` backups for them on a schedule.

Today the project supports:
- Source type: `files`
- Repository type: `local`
- API health endpoints for container orchestration
- A container image published to GitHub Container Registry

## How It Works

Dockvault watches Docker volumes for labels prefixed with `dockvault.` and can
also load retrofit job config from a server-side YAML file.

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

The backup hostname is configured on the server side. If no override is
configured, Dockvault uses the machine hostname.

## Job Configuration

Jobs can be configured in two ways:

1. Docker volume labels
2. A server-side YAML file referenced by `DOCKVAULT_CONFIG_PATH`

When both are present for the same volume, external config wins for overlapping
fields.

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

### External Config For Existing Volumes

Use `DOCKVAULT_CONFIG_PATH` when you need to onboard existing volumes without
recreating or relabeling them.

- the config file is read by the Dockvault server process
- each job must point at an existing Docker volume with `source.volume_name`
- the real Docker volume remains the backup source identity
- top-level `defaults` can hold shared policy to keep the file DRY

Example:

```yaml
defaults:
  repository:
    type: local
    password_env: RESTIC_PASSWORD
  retention:
    keep_last: 7
    keep_daily: 14

jobs:
  media:
    source:
      type: files
      volume_name: media_data
    schedule: "0 1 * * *"
    repository:
      path: /srv/restic/media

  photos:
    source:
      type: files
      volume_name: photos_data
    schedule: "0 2 * * *"
    repository:
      path: /srv/restic/photos
```

Recommended precedence per volume:

1. External config entry for the volume
2. Volume labels
3. Server-side env defaults
4. Ignore the volume if neither defines a valid job

Compose example:

```yaml
services:
  dockvault:
    environment:
      DOCKVAULT_CONFIG_PATH: /etc/dockvault/config.yaml
    volumes:
      - ./dockvault/config.yaml:/etc/dockvault/config.yaml:ro
```

Sample file:

- `dockvault.config.example.yaml`

## Environment Variables

Required for backups:
- `RESTIC_PASSWORD`
  Default password variable consumed by the restic container.

Optional:
- `DOCKVAULT_SERVER_URL`
  Default Dockvault API base URL for remote CLI commands such as `jobs`, `job`,
  `snapshots`, and `history`.
- `DOCKVAULT_CONFIG_PATH`
  Absolute path inside the Dockvault server container/process for the optional
  retrofit YAML job config file.
- `DOCKVAULT_API_TOKEN`
  Shared bearer token for mutating API requests. When set on the server,
  `POST` backup, check, and restore endpoints require
  `Authorization: Bearer <token>`. Set the same value for remote CLI usage.
- `DOCKVAULT_DEFAULT_SOURCE_TYPE`
  Optional default `source.type` for discovered jobs. Useful for reducing
  repeated `dockvault.source.type=files` labels.
- `DOCKVAULT_DEFAULT_REPOSITORY_TYPE`
  Optional default `repository.type` for discovered jobs. Useful for reducing
  repeated `dockvault.repository.type=local` labels.
- `DOCKVAULT_DEFAULT_REPOSITORY_PASSWORD_ENV`
  Optional default `repository.password_env` for discovered jobs.
- `DOCKVAULT_DEFAULT_RETENTION_KEEP_LAST`
- `DOCKVAULT_DEFAULT_RETENTION_KEEP_DAILY`
- `DOCKVAULT_DEFAULT_RETENTION_KEEP_WEEKLY`
- `DOCKVAULT_DEFAULT_RETENTION_KEEP_MONTHLY`
- `DOCKVAULT_DEFAULT_RETENTION_KEEP_YEARLY`
  Optional default retention policy for discovered jobs. These values are
  overridden by volume labels and external config.
- `DOCKVAULT_HOSTNAME`
  Hostname to attach to backups. If unset, Dockvault uses the current host
  name.
- `DOCKVAULT_HISTORY_DB_PATH`
  SQLite path used for persistent backup run history. Defaults to
  `dockvault-history.sqlite3` in the current working directory.
- `DOCKVAULT_MAX_CONCURRENT_BACKUPS`
  Global limit for concurrently running scheduled backup and retention jobs.
  Defaults to `1`.
- `DOCKVAULT_RETENTION_SCHEDULE`
  Cron expression in `UTC` for native restic retention runs. If unset,
  retention scheduling is disabled.
- `DOCKVAULT_RETENTION_ARGS`
  Default arguments passed to `restic forget` for repositories that do not
  declare a per-repository retention policy. Example:
  `--keep-last 7 --keep-daily 14`. Dockvault automatically adds `--prune` if
  you do not include it.

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
uv run dockvault doctor
uv run dockvault config scaffold > dockvault.config.yaml
uv run dockvault config scaffold --schedule "0 2 * * *" --repository-root /srv/restic > dockvault.config.yaml
uv run dockvault config scaffold --repository-password-env RESTIC_PASSWORD_MEDIA --retention-keep-weekly 8 > dockvault.config.yaml
uv run dockvault config scaffold --server http://dockvault:8000 > dockvault.config.yaml
uv run dockvault jobs --server http://dockvault:8000
uv run dockvault job media-nightly --server http://dockvault:8000
uv run dockvault snapshots media-nightly --server http://dockvault:8000
uv run dockvault history media-nightly --server http://dockvault:8000
uv run dockvault backup create media-nightly --server http://dockvault:8000
uv run dockvault backup check media-nightly --server http://dockvault:8000
uv run dockvault restore media-nightly latest --server http://dockvault:8000
uv run dockvault restore media-nightly latest restore-target --path /photos/2024/image.jpg --server http://dockvault:8000
uv run dockvault restore media-nightly latest --dry-run
uv run dockvault restore media-nightly latest --in-place
uv run dockvault backup list-jobs
uv run dockvault backup create media-nightly
uv run dockvault backup snapshots media-nightly
uv run dockvault backup check media-nightly
uv run dockvault restore media-nightly latest
uv run dockvault restore media-nightly latest restore-target
uv run dockvault restore media-nightly latest restore-target --path /photos/2024/image.jpg
```

Command behavior:
- `dockvault server` starts the API and scheduler on `0.0.0.0:8000`
- `dockvault doctor` verifies Docker access, discovered jobs, required password
  environment variables, and repository path mounts inside the container
- `dockvault config scaffold` prints a starter YAML config for all current
  Docker volumes using the selected schedule and repository root
- scaffold generation also accepts override flags for generated defaults such
  as `--source-type`, `--repository-type`, `--repository-password-env`, and
  `--retention-keep-*`
- `dockvault config scaffold --server <url>` asks a remote Dockvault server to
  generate the same starter YAML from that server's Docker volume inventory
- `dockvault jobs --server <url>` prints discovered jobs from a remote
  Dockvault server
- `dockvault job <name> --server <url>` prints one discovered job from a remote
  Dockvault server
- `dockvault snapshots <name> --server <url>` prints remote snapshot data for a
  discovered job
- `dockvault history <name> --server <url>` prints remote in-memory run history
  for a discovered job
- `dockvault restore <name> <snapshot> [target-volume] --server <url>` triggers
  a restore through a remote Dockvault server
- `dockvault restore <name> <snapshot> [target-volume] --dry-run` previews the
  restore and prints the restic output without writing data
- without `--server`, `dockvault jobs`, `dockvault job`, `dockvault snapshots`,
  `dockvault history`, and `dockvault restore` use local Docker-backed behavior
  by default; if `DOCKVAULT_SERVER_URL` is set they switch to remote mode
  automatically
- `dockvault backup list-jobs` prints discovered job names
- `dockvault backup create <name>` runs matching jobs immediately
- `dockvault backup create <name> --server <url>` triggers a backup
  through a remote Dockvault server
- `dockvault backup snapshots <name>` prints matching job snapshots as JSON
- `dockvault backup check <name>` runs `restic check` for matching job repositories
- `dockvault backup check <name> --server <url>` triggers a repository check
  through a remote Dockvault server
- `dockvault restore <name> <snapshot> [target-volume]` restores a
  snapshot into an override volume when `target-volume` is provided
- `dockvault restore <name> <snapshot> [target-volume] --path <path>` restores
  only the matching path from the snapshot into the selected target volume
- restoring into the original source volume now requires explicit
  `--in-place` confirmation for local CLI usage or `allow_in_place=true` in the
  API payload
- `--dry-run` can be used without `--in-place` to preview an in-place restore
  safely

## API

Endpoints:
- `GET /health`
  Returns `200` with `{"status":"ok"}` when the process is alive.
- `GET /ready`
  Returns readiness based on scheduler state and Docker job discovery.
- `GET /jobs`
  Returns discovered backup jobs with source, repository, schedule, and next
  scheduled run time when the job is currently present in the scheduler. When
  available, responses also include the latest in-memory backup run record for
  the current server process.
- `GET /jobs/{name}`
  Returns one discovered backup job by name.
- `GET /jobs/{name}/snapshots`
  Returns snapshots from the job's restic repository filtered by the job's
  source volume tag.
- `GET /jobs/{name}/history`
  Returns recent persisted backup run records from the local history database.
- `POST /jobs/{name}/backup`
  Triggers an immediate backup run. Requires bearer auth when
  `DOCKVAULT_API_TOKEN` is configured.
- `POST /jobs/{name}/check`
  Triggers an immediate `restic check`. Requires bearer auth when
  `DOCKVAULT_API_TOKEN` is configured.
- `POST /jobs/{name}/restore`
  Triggers a restore. Requires bearer auth when `DOCKVAULT_API_TOKEN` is
  configured. Restoring into the source volume requires `allow_in_place=true`.
  Set `dry_run=true` to preview the restore and receive restic output lines in
  the response.

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
curl http://127.0.0.1:8000/jobs
curl http://127.0.0.1:8000/jobs/media-nightly
curl http://127.0.0.1:8000/jobs/media-nightly/snapshots
curl http://127.0.0.1:8000/jobs/media-nightly/history
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

The checked-in Compose template is `compose.example.yaml`.
Create a local `compose.yaml` only when you want Docker Compose defaults or
host-specific overrides that should stay out of git.

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
6. The history database path is writable if you want persistent run history.

Required runtime inputs:

- Docker socket mount: `/var/run/docker.sock:/var/run/docker.sock`
- Repository path mounts for every `dockvault.repository.path` in use
- `RESTIC_PASSWORD`, or whichever variable name is configured through
  `dockvault.repository.password_env`
- a writable location for `DOCKVAULT_HISTORY_DB_PATH` if you override it

When using the checked-in Compose template, set
`DOCKVAULT_REPOSITORY_ROOT` in `.env` so the host bind mount and the in-container
repository path stay identical. For example, if job labels use
`dockvault.repository.path=/backup/restic/media`, then
`DOCKVAULT_REPOSITORY_ROOT=/backup/restic` and the compose bind mount will become
`/backup/restic:/backup/restic`.

Optional runtime inputs:

- `DOCKVAULT_HOSTNAME` to override the host name recorded in restic snapshots
- `DOCKVAULT_API_TOKEN` to protect mutating API endpoints
- `DOCKVAULT_HISTORY_DB_PATH` to move the SQLite history database onto a
  persistent mount

Operational assumptions:

- schedules are interpreted in `UTC`
- Dockvault reconciles Docker jobs every `60` seconds
- backup jobs run in transient `restic/restic:0.19.0` containers
- the service must be allowed to create and remove those transient containers

Build locally:

```bash
docker build -t dockvault:test .
```

Run the checked-in Compose template:

```bash
cp .env.example .env
docker compose --env-file .env -f compose.example.yaml up -d --build
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

## Native Retention

Dockvault can schedule native restic retention runs from the server process.

Behavior:
- retention uses `DOCKVAULT_RETENTION_ARGS` as the default policy
- one retention job is scheduled per unique repository path
- repositories shared by multiple backup jobs are deduplicated
- per-repository labels can override or disable the global policy
- retention jobs share the same global concurrency limit as scheduled backups

Example configuration:

```bash
export DOCKVAULT_RETENTION_SCHEDULE="30 3 * * *"
export DOCKVAULT_RETENTION_ARGS="--keep-last 7 --keep-daily 14 --keep-weekly 8"
```

That makes Dockvault run a command equivalent to:

```bash
restic -r /repo forget --json --keep-last 7 --keep-daily 14 --keep-weekly 8 --prune
```

Per-repository overrides:

- `dockvault.retention.enabled=false`
  disables scheduled retention for that repository even when global retention is
  configured.
- `dockvault.retention.keep_last=<n>`
- `dockvault.retention.keep_daily=<n>`
- `dockvault.retention.keep_weekly=<n>`
- `dockvault.retention.keep_monthly=<n>`
- `dockvault.retention.keep_yearly=<n>`

Example labels on one backup volume:

```text
dockvault.retention.keep_last=7
dockvault.retention.keep_daily=14
dockvault.retention.keep_weekly=8
```

Precedence:

- explicit per-repository retention labels override the global retention args
- `dockvault.retention.enabled=false` opts the repository out of global retention
- repositories without retention labels inherit `DOCKVAULT_RETENTION_ARGS`

If the same repository is discovered with conflicting explicit retention
policies, Dockvault logs a warning and skips retention scheduling for that
repository.

## Failure Behavior

Dockvault is designed to keep running when individual backups, retention runs,
or Docker discovery operations fail.

Current behavior:
- Docker unavailable during reconcile:
  the reconcile loop logs a warning and leaves existing scheduled jobs in place.
- Job discovery failure:
  Dockvault retries discovery up to `3` times with a `1` second delay, then
  logs the failure path and preserves existing scheduled jobs.
- Invalid volume labels or invalid cron on one job:
  that job is skipped and other jobs continue to be scheduled.
- Backup failure:
  the failing backup logs an error or warning with job context and does not stop
  the scheduler or other jobs.
- Retention failure:
  the failing retention run logs an error or warning with repository context and
  does not stop the scheduler or other jobs.
- Missing `dockvault.repository.password_env` variable:
  the affected backup or retention run fails when its transient restic container
  is being prepared; other jobs are unaffected.
- Retention misconfiguration:
  if `DOCKVAULT_RETENTION_SCHEDULE` is set but `DOCKVAULT_RETENTION_ARGS` is
  empty, Dockvault logs a warning and only schedules repositories with explicit
  per-repository retention labels.
- Readiness endpoint:
  `/ready` returns `503` when the scheduler is unavailable/stopped, Docker is
  unavailable, or job discovery fails.

Operationally, this means Dockvault prefers partial progress over global
failure: one bad job should not take down the server or unschedule unrelated
work.

## Observability

Important runtime logs include:
- scheduler configuration at startup
- reconcile summaries showing discovered, scheduled, failed, and removed jobs
- backup start/completion/failure messages with job and repository context
- retention start/completion/failure messages with repository context
- concurrency wait messages when backups or retention runs are blocked by the
  global concurrency limit

## Docker Security

Dockvault currently requires access to the Docker socket:

```text
/var/run/docker.sock
```

That gives the process broad control over the local Docker daemon. In practice,
Dockvault can:
- list Docker volumes and read their labels
- create transient `restic/restic` containers
- mount host paths and Docker volumes into those containers
- start and remove those transient containers

Operationally, you should treat Dockvault as a privileged infrastructure
service, not as an untrusted application workload.

Recommendations:
- deploy Dockvault only on hosts where Docker-level control is acceptable
- do not expose the Docker socket mount to unrelated containers
- keep the host limited to trusted operators and workloads
- prefer a dedicated backup host or a clearly trusted single-tenant Docker
  environment
- review which host paths are mounted into Dockvault, since those same paths can
  then be mounted into transient restic containers

Current limitation:
- Dockvault does not support a reduced-permission discovery mode; Docker socket
  access is required for job discovery and for launching backup and retention
  containers

## Secret Handling

Dockvault currently passes restic credentials to transient backup and retention
containers through environment variables.

Default behavior:
- each repository uses `RESTIC_PASSWORD` unless overridden with
  `dockvault.repository.password_env`
- the configured password variable must exist in the Dockvault server process
  environment
- Dockvault forwards that variable into the transient `restic` container it
  launches for the job

Operational recommendations:
- inject secrets at deploy time instead of hardcoding them in Compose files
- use your runtime's secret injection mechanism if available
- avoid committing real password values into git
- use different password variables only when you intentionally need separate
  repository credentials
- treat access to the Dockvault container environment as secret access

Example with an environment file:

```bash
cp .env.example .env
docker compose --env-file .env -f compose.example.yaml up -d
```

If you prefer Docker Compose's default file discovery, keep the checked-in
template unchanged and put your machine-specific deployment in a local
`compose.yaml`.

If you use a custom repository password variable label such as:

```text
dockvault.repository.password_env=MEDIA_RESTIC_PASSWORD
```

then `MEDIA_RESTIC_PASSWORD` must be present in the Dockvault server
environment before that job can run.

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

Release procedure:
- see `RELEASING.md`

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
