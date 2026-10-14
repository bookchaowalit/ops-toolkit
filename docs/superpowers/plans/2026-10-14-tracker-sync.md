# Tracker synchronization Implementation Plan

> **For agentic workers:** use superpowers:subagent-driven-development for the bounded implementation; the main agent owns integration, verification and activation.

**Goal:** provide a guarded, repeatable sync for task #177 and its existing linked trackers (work task #180).

**Architecture:** Python worker with fixed provider adapters, an explicit pilot allowlist and field ownership. A local checkpoint stores only synchronization hashes and pending intents; Tasks API retains live task data. Local scheduling remains disabled until preflight passes.

**Tech Stack:** Python standard library, task_store, `_env`, offline unittest fake HTTP services, user systemd.

- [ ] T1.1 implementer: write meaningful failing integration tests, implement `infra/scripts/sync/tracker_sync.py`, `tracker_sync_http.py`, and `infra/tests/integrations/tracker_sync_test.py`; run focused tests.
- [ ] T1.2 main: create `infra/platform/tracker-sync-pilot.json`, schedule templates under `infra/templates/tracker-sync/`, operating runbook, inspect live identities and perform dry-run validation. Do not touch shared dirty Taskfile.
- [ ] T1.3 main/reviewer: run `task test`, scope diff, commit; independent medium-risk review with gpt-6.1-sol high; resolve findings and rerun affected checks, push reviewed scope.
- [ ] T1.4 main: verify preflight, activate timer only if credentials are available; otherwise record exact runtime blocker and the single secure setup needed. Log private conversation diary and update work task without marking UI work done.

Root plan tree: G1 → T1 → T1.1 implementation, T1.2 integration/runtime, T1.3 review/delivery, T1.4 activation/handoff. T1.1 and T1.2 are independent until integration; T1.3 depends on both. No child agent may release or access external credentials.
