# Organization hub registry synchronization

Date: 2026-10-15

## Outcome

Continue the user's per-organization hub structure by refreshing its GitHub
metadata automatically. The umbrella remains an index of references; operations
code belongs in platform-ops-cli. Schedule a local daily run at 09:00 Asia/Bangkok.

## Ownership and records

- Take an explicit umbrella `registry/hubs.json` and explicit hub checkout parent.
- Validate all registered references, authenticated admin membership and coverage
  of owned `bookchaowalit-*` organizations. Report new/unregistered organizations
  for review; the scheduled worker does not create or transfer repositories.
- Refresh the 12 organization inventories from authenticated, paginated GitHub
  repository metadata; exclude each hub itself and preserve archived/private
  repositories and exact default branch values.
- Check the personal solo-empire reference, retaining its existing registry schema.
- New Org hubs use their existing JSON and generated README regions. Work Hub
  uses a separate `github-repositories.json` and `GITHUB-REPOSITORIES.md`, leaving
  client registries and documents under their existing ownership.
- The umbrella stores hub metadata and sync configuration, never product records.
- References are navigation/configuration, not runtime imports between hubs.

## Worker behavior

Use Python's standard library, Git and the existing authenticated GitHub CLI.
No new tokens, cloud services or Actions workflows are needed. Preview is the
default. `--check` reports drift with exit 1; `--apply` writes projections and
`--publish` explicitly enables scoped commits/pushes. Operational blockers exit 2.

Fetch and validate all remote metadata before projection writes. Validate owner,
URL, schema, path containment and generated markers. Never interpret provider
values as shell commands or unescaped Markdown table structure.

Modify only configured registry files and generated regions; preserve surrounding
documentation. Refuse dirty checkouts, wrong remotes/branches, unpushed or diverged
commits. In apply mode, a clean behind-only checkout may fast-forward before
rendering. Preserve a rejected push's local commit and report required action;
never force-push or rewrite history. Record errors by safe codes rather than raw
credential-bearing subprocess output. A lock prevents overlapping writers.

Preserve snapshot dates and make no commits when metadata/rendered output is
unchanged. Runtime reports and locks are ignored local files. One blocked target
does not authorize changing it; report partial completion accurately.

## Scheduling

Install a user systemd service and persistent daily timer only after a live
`--check` confirms all configured hubs are synchronized. Run with the same user's
GitHub session, noninteractive Git, a private umask and a bounded service timeout.
The local machine must be running; a persistent timer catches up on a missed run.
Unchanged runs produce no notifications and no Git commits. Failures are visible
in service status/journal and the local runtime report.

## Verification

Use controlled real Git repositories to verify scoped commits, idempotence,
dirty-work protection, rollback before commit, and push rejection preservation.
Test owner/URL/path validation, empty and archived inventories, unusual branches,
marker preservation, metadata failures and the timer's live-readiness gate.
Run the owning offline suite, report environmental failures honestly, then perform
live preview/apply/check, remote revision checks and an actual oneshot timer run.
