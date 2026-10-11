# solo-ops

Operations tooling for Solo Empire: the Python scripts behind the daily workflows, the owner CLI
(`infra/cli.ts`), platform registries, integration clients and their offline tests.

This repository was split out of the `solo-empire` registry repository on 2026-10-11. **The files were
copied, not moved with `git subtree`: everything before the split keeps its history in
`bookchaowalit/solo-empire`** (`infra/`, `Makefile`, `empire`, `data/indexes/task-command-ownership.json`,
the former `.github/workflows`). The registry repository now only holds `repository-catalog/` and the
agent-harness hooks; it is **not** a dependency of this repository.

## Run the checks

```bash
cd solo-ops
task test              # every infra/tests/**/*_test.py script, offline (EMPIRE_CLIENT_CONFIG=/nonexistent)
task docs:check        # contract guards that read the sibling architecture/knowledge checkouts
task check             # docs:check + test
task install           # npm ci for the owner CLI (tsx, typescript)
task typecheck         # tsc --noEmit for infra/cli.ts (after task install)
task --list            # all other tasks (platform, integrations, learning, portfolio, ...)
```

`task test` passes in a fresh clone: tests that need a sibling repository skip with a clear `SKIP`
line when it is not checked out. No CI workflow is configured (Actions quota); run the commands locally.

## Where things are

| Path | What |
|---|---|
| `infra/scripts/` | Python scripts (`utils`, `platform`, `sync`, `workflows`, `learning`, `quality`, `security`, ...) |
| `infra/tests/` | offline tests; `infra/tests/run_all.py` runs them all |
| `infra/tasks/*.yml` | Task adapters (flattened into the root `Taskfile.yml`) |
| `infra/platform/` | cloud-provider, domain and building-block registries owned by ops |
| `infra/cli.ts` | owner CLI (`task tasks`, `task status`); loads shared modules from the `solo-mcp` sibling |
| `empire`, `Makefile` | thin facades over Task |

## Sibling repositories (no path back to the hub)

Siblings are looked up as `../<repo>` next to this checkout, or through `SOLO_<NAME>_ROOT`
(name without the `solo-` prefix, upper-cased, dashes to underscores).

| Variable | Default | Used for |
|---|---|---|
| `SOLO_KNOWLEDGE_ROOT` | `../solo-knowledge` | diary entries (`diary/entries`), roles, goals, learning, quiz |
| `SOLO_ARCHITECTURE_ROOT` | `../solo-architecture` | docs, systems, operations, templates read by the contract guards |
| `SOLO_SKILLS_ROOT` | `../solo-skills` | skill packages |
| `SOLO_MCP_ROOT` | `../solo-mcp` | `infra/cli.ts` imports `src/*.ts` from here (same module instances as the MCP server) |
| `SOLO_REGISTRY_DIR` | *(none)* | the registry repository's `repository-catalog` folder; tools that read `online-systems.yaml`, `runtime-boundaries.yaml`, `docpack-coverage.yaml` need it and skip clearly when unset |
| `SOLO_BOOK_DEV_GITHUB_ROOT` | derived from `.../github/<owner>/private/solo-ops` | the folder that holds the repository clones |
| `SOLO_ACCOUNTS_ROOT`, `SOLO_MEDIA_ROOT`, `SOLO_INTEGRATIONS_ROOT`, ... | `../<repo>` | per-repository lookups |

`infra/scripts/utils/_env.py` is the single resolver (`PROJECT_ROOT` = this checkout, `repo_file()`,
`sibling_repo_root()`); the legacy repository-relative names `docs/`, `systems/`, `projects/`, `skills/`,
`repository-catalog/` and `projects/product/engineering/book-dev/github/...` are translated to the
owning checkout.

## Diary

`task diary -- --record-conversation --topic ... --question-summary ... --outcome-summary ... --functions ...`
writes the private daily diary under `$SOLO_KNOWLEDGE_ROOT/diary/entries/YYYY/MM/DD/` (git-ignored in
solo-knowledge, never committed).

## Secrets

Credentials come from `infra/.env` (git-ignored; template: `infra/.env.example`) or the configured secret
provider. Tests never need them: they use placeholders and the SQLite store doubles under `infra/tests/support/`.
