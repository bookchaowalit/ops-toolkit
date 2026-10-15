# Organization Hub Sync Implementation Plan

> Use superpowers:executing-plans and test-driven-development; obtain an independent review before publishing.

**Goal:** Automatically maintain organization hub inventories and validate the umbrella graph.

**Architecture:** platform-ops-cli owns the worker and user-timer installer. An explicit umbrella index provides metadata-only targets. Each owning hub stores its projection independently.

**Tech Stack:** Python standard library, Git, GitHub CLI, user systemd.

**Spec:** ../specs/2026-10-15-org-hub-sync.md

## Global constraints

- Daily schedule: `*-*-* 09:00:00 Asia/Bangkok`, persistent local timer.
- No credentials copied, no repository creation/transfers, no client document ingestion.
- Explicit paths; default preview; publishing requires `--apply --publish`.
- Refuse dirty or diverged checkouts and never force-push.
- No-change runs preserve dates, revisions and documents outside generated regions.

## Review focus

- API pagination, failed reads and loss of admin membership must not silently truncate inventories.
- Generated Markdown must preserve archive status, unusual branch text and surrounding human prose.
- Configuration/symlinks cannot redirect writes outside a hub or into credential/client files.
- Dirty/ahead/diverged repositories and rejected pushes retain user work.
- Timer activation requires a verified live unchanged report and a real next trigger.

## Task 1: Worker and behavioral coverage

- [x] Add failing tests for inventory validation/rendering, paths, real Git publishing and failure boundaries.
- [x] Implement `infra/scripts/sync/org_hub_registry.py` with explicit index/checkout arguments, preview/check/apply/publish, scoped projections and safe reports.
- [x] Run focused tests and the owning offline suite; record failures/skips explicitly.

## Task 2: Timer and adoption

- [x] Add failing timer readiness/quoting tests and implement `infra/scripts/sync/install_org_hub_timer.py`.
- [x] Add sync metadata and generated regions to the umbrella and 11 new hubs; give Work Hub its separate GitHub inventory projection.
- [x] Document commands, schedule, local-machine lifetime, failure handling and owning tooling references.
- [ ] Run live preview/apply/check and obtain an independent review; fix blocking findings.

## Task 3: Publish and activate

- [ ] Commit/push only the intended owning-repository paths after verification.
- [ ] Verify remote revisions and live no-change behavior.
- [ ] Install/validate user units, run the actual oneshot, enable the timer and inspect the next trigger.
- [ ] Report the operating schedule, results and any unresolved external/environmental limitations.

## Verification evidence before publication

- Focused contracts: 19 worker tests and 5 timer tests passed.
- Independent review: fixed both Important findings with failing-then-passing regressions for path revalidation after fast-forward and failure-report persistence. Literal Markdown branch rendering also has a regression.
- Actual user-unit syntax validation passed after supplying the user's existing runtime directory when the shell omits XDG_RUNTIME_DIR.
- Owning offline suite: 119/120 test scripts passed. `infra/tests/rnd/rnd_pipeline_test.py` fails because the external `bookchaowalit/ai-doc-assistant` checkout is absent. Its test, guard and environment resolver are unchanged; standalone reproduction confirms the same missing-evidence failure. This is an environmental limitation, not a full-suite pass.
