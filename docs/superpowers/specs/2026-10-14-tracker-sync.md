# Guarded tracker synchronization pilot

User authorization: continue the existing pilot and implement the remaining automatic synchronization. Work task: #180. Scope task: #177; these are separate tasks.

## Contract

Use the Tasks API as the execution authority for the one existing task. Trello owns its source title. The worker may update the task title with If-Match, Jira summary/status, Trello execution list, and a narrowly marked status block in Trello and Linear descriptions. Preserve other text, labels, dates, assignees and fields. BOO-27 is an evidence parent: never change its title, global status or acceptance criteria.

| Tasks status / explicit notes.tracker_sync.phase | Jira target | Trello target |
| --- | --- | --- |
| todo / absent or Backlog | To Do | Backlog |
| in_progress / absent or WIP | In Progress | WIP |
| in_progress / Review | In Review | Review |
| done / absent or Done | Done | Done |
| blocked, cancelled, incompatible phase | hold and report | hold and report |

Do not infer completion from Closed, card completion flags, a parent issue, repository tests or timestamps. Do not import new cards, create issues, delete/archive objects, use native Jira sync in parallel, send comments/messages, or expand the allowlist.

Exact identities and list/status IDs are in `infra/platform/tracker-sync-pilot.json`. Resolve existing objects and verify source markers and workspace/board/project identities before writing. Never match by title or display number alone. Runtime must reject configuration outside this pilot.

## Concurrency and recovery

One nonblocking local lock per configuration; one bounded tick. Tasks title writes use the returned version with If-Match. Before every provider mutation reread the relevant object and compare its managed fields to the previous verified baseline or the intended output. Unexpected manual mirror edits cause a conflict and hold. Preserve fresh unrelated description text. Journal intended managed output hashes before a mutation, verify readback, then advance that provider's checkpoint atomically. Recover a lost acknowledgement only when current output matches the persisted intent; partial failure stays visible. Checkpoints contain identities, hashes and bounded status codes, not a shadow task database or provider content.

Jira and Trello do not offer an atomic cross-provider transaction. A reread reduces the race window; it cannot eliminate concurrent provider edits after that read. The report must retain this limitation. Do not replay uncertain transitions blindly. Jira transition IDs are discovered from currently valid transitions and matched by target status ID.

## Runtime

Python standard library, existing `_env.get_env`/secret resolver and Tasks API client. Fixed HTTPS provider origins, authorization headers, no redirect forwarding or token-bearing URLs/logs. Tests never use real credentials or hosted services. Bounded HTTP timeouts, limited retry of reads for transient errors; writes with uncertain outcomes are resolved by readback on a later tick. Honor Retry-After by deferring the next tick rather than long sleeps.

Default CLI is dry-run. Explicit `--apply` enables writes only within the allowlist; `--preflight` reports missing credential names without values. Credential names: LINEAR_API_KEY, TRELLO_API_KEY, TRELLO_API_TOKEN, JIRA_EMAIL, JIRA_API_TOKEN. Supported credentials must be configured securely before activation; chat connectors are not worker credentials. Use the existing task-sync delegated Tasks API client. No new secret creation or production service deployment.

Schedule template: a local user systemd timer every15minutes, one execution at a time with a hard deadline; use a stable installed checkout path supplied at installation. Do not enable while preflight is blocked. Quiet unchanged ticks; JSON operational report only on changes/conflicts/failure, with an owner-readable health/status command and nonzero failure exit. No external notifications unless separately authorized. A stopped/asleep machine cannot execute; document the local runtime boundary. Native Codex automation_update is unavailable in this session; do not forge its configuration or use unrelated Page/Site automations.

## Verification and delivery

Test actual fake-provider HTTP requests, identity rejection, bootstrap safety, no-op idempotence, source title changes, WIP/Review/Done mapping, unsupported state hold, stale Task version, manual mirror conflict, concurrent reread drift, lost ack, partial provider failure, rate-limit deferral, redacted errors, locks and journal recovery. Verify current live objects through connected tools and dry-run only; do not move the real UI card as a test. Run owning ops suite, independent review, scoped commit/push. Runtime activation is a separate fact and must not be claimed from unit tests.
