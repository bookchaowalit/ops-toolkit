# Infrastructure Test Suite

Repository-wide tests are grouped by the boundary they protect. Package-local
tests remain beside their package; these tests validate the shared Solo Empire
runtime and contracts.

```text
tests/
├── agent/        # agent routing, context, cache, telemetry, runtime
├── api/          # API and webhook boundaries
├── data_lake/    # only job_consumer_test + metadata_catalog_test remain; lake tests moved to solo-data-lake
├── database/     # schema, paths, backups, and idempotency
├── decisions/    # decision records, briefs, and context
├── engagements/  # engagement review and notification contracts
├── finance/      # finance control-plane and review flows
├── integrations/ # provider read-only and sync boundaries
├── (mlops/ moved to solo-mlops)
├── platform/     # deployment, workspace, mobile, and platform checks
├── quality/      # smoke, regression, automation, and coverage checks
├── security/     # secret and security checks
└── fixtures/     # sanitized test fixtures
```

Run repository-wide tests through the root `Taskfile.yml` so the Python
runtime and repository root are resolved consistently. The `infra/package.json`
names remain compatibility aliases for the same underlying test boundaries.

## Primary commands

| Command | What it runs |
|---|---|
| `task test` | Smoke tests only |
| `task test:matrix` | **All** discoverable modules under `infra/tests/` |
| `task test:matrix:list` | List modules (RUN vs SKIP) |
| `task test:flagships` | Nested **Tier A** unit/contract suites only |
| `task test:flagships:list` | List flagship targets |
| `task test:playwright` | Discover and run nested Playwright browser/E2E suites; aggregate HTML reports |
| `task test:playwright:list` | List discovered Playwright projects and readiness |
| `task test:playwright:ui -- --only <project-id>` | Open interactive Playwright UI Mode for one nested project |
| `task test:playwright:report` | Open the latest merged Playwright HTML report viewer on `127.0.0.1:9323` |
| `task test:lighthouse` | Portfolio LHCI **config** gate |
| `task test:lighthouse:run` | Full `lhci autorun` (slow; needs Chrome + Next start) |
| `task test:a11y` | Portfolio axe suite **config** gate |
| `task test:a11y:run` | Full Playwright axe run (starts Next; slower) |
| `task test:all` | preflight + workspace + matrix + flagships + lighthouse + python/mcp/go + docs |
| `task test:all:legacy` | Compatibility alias for the full Task quality gate |
| `task test:<domain>` | Targeted domain scripts (agent, finance, data-lake, …) |

### Matrix runner details

```bash
task test:matrix
task test:matrix -- --only database,security
task test:matrix -- --include-optional
task test:matrix -- --include-benchmarks
task test:matrix -- --json
```

Default matrix **skips**:

- `api/api_performance_benchmark.py` (use `--include-benchmarks` or `perf:api`)
- `data_lake/job_data_e2e_test.py` unless `BOOK_JOB_DATA_ROOT` is set or `--include-optional`

Implementation: `infra/scripts/quality/run_test_matrix.py`  
Lighthouse gate: `infra/scripts/quality/run_lighthouse_gate.py`  
Flagship gate: `infra/scripts/quality/run_flagship_test_gate.py` + `flagship_test_targets.yaml`

`test:lighthouse:run` auto-detects system Chrome or Playwright Chromium
(`~/.cache/ms-playwright/...`). It still requires the portfolio Next server to
serve `/` successfully after build.

### Flagship nested gate

```bash
task test:flagships
task test:flagships -- --only bookreading-frontend,booktrading-frontend
task test:flagships -- --include-optional
task test:flagships -- --strict-inventory
```

Targets live in `infra/scripts/quality/flagship_test_targets.yaml` (frozen Tier A
paths + offline-safe commands). Inventory-only packages are reported but do not
fail unless `--strict-inventory` is set.

### Scope note

`test:matrix` covers the **Solo Empire shared runtime** under `infra/tests/`.
`test:flagships` covers selected nested Book Dev flagships only — not all ~255
nested repositories.

### Portfolio Playwright orchestrator

The Playwright portfolio gate is a separate browser/UI layer. It searches the
roots declared in `infra/scripts/quality/playwright-projects.json` and includes
a package when it declares a Playwright config or one of the supported E2E
scripts. The nested repository remains the owner of its config, fixtures, and
test files; the parent only runs the command and collects evidence.

```bash
task test:playwright:list
task test:playwright
task test:playwright -- --only bookchaowalit-portfolio
task test:playwright -- --strict
task test:playwright:ui -- --only bookchaowalit-portfolio
task test:playwright:report
```

Each ready project emits a blob report and an individual HTML report. Blob
reports are merged into the latest portfolio report under the ignored
`infra/test-results/playwright/` boundary. `test:playwright:ui` starts
Playwright's interactive UI Mode for exactly one selected nested project; use
`test:playwright:report` for the merged HTML report viewer. Neither command
installs dependencies or modifies a nested repository. A project without local
dependencies is reported as `skip` by default and becomes a gate failure with
`--strict`.
