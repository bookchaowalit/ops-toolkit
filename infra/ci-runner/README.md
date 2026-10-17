# Private-repo CI runner (Railway)

Public repositories keep using GitHub-hosted runners. Private repositories run on this
self-hosted runner so they do not consume paid GitHub Actions minutes.

- Image: `myoung34/github-runner` (Ubuntu 24.04) plus PostgreSQL 17 for test databases.
- Host: Railway project `ci-runners`, one service per runner scope.
- Labels: `self-hosted, linux, x64, railway`. Workflows use
  `runs-on: [self-hosted, linux, x64, railway]`.
- Docker is not available inside Railway containers, so `services:` containers do not work.
  Start the database with a step: `run: start-postgres` and connect to
  `postgresql://postgres@127.0.0.1:5432/postgres`.

## Registration and credentials

No long-lived GitHub token is stored on Railway. Each service gets a one-hour registration
token (`RUNNER_TOKEN`) created with `gh api -X POST repos/<owner>/<repo>/actions/runners/registration-token`
(or `orgs/<org>/...` for an org-wide runner). The runner's configuration is kept on a Railway
volume mounted at `/runner-config` (`CONFIGURED_ACTIONS_RUNNER_FILES_DIR`), so restarts reuse
the registration. If the volume is lost, create a new registration token and redeploy.

Do not set `EPHEMERAL` at all: the image treats any non-empty value, including `false`, as
ephemeral, so the runner deregisters after one job. Set `DISABLE_AUTOMATIC_DEREGISTRATION=true`.
If GitHub deletes the registration (the runner was offline too long), the volume holds stale
config and the runner logs "registration has been deleted": replace the volume with an empty one,
set a fresh `RUNNER_TOKEN`, and redeploy.

Personal-account repositories (`bookchaowalit/*`) cannot share a runner; each needs its own
service with `RUNNER_SCOPE=repo` and `REPO_URL`. Organization repositories can share one
service with `RUNNER_SCOPE=org` and `ORG_NAME`.

## Services

| Service | Scope | Target |
| --- | --- | --- |
| `runner-sales-crm` | repo | `bookchaowalit/sales-crm` |
