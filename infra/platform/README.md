---
title: Platform registries (repository-wide)
tags: [infra, platform, registry]
created: 2026-07-01
updated: 2026-10-11
description: Repository-wide platform registries that are owned by ops-toolkit (the platform kit lives in its own repository).
---

# `infra/platform/` — repository-wide registries

เมื่อ 2026-10-11 ชุด platform kit (IaC, GitOps, Helm, Kyverno policies, onboarding, observability, supply-chain, homelab bootstrap และเอกสาร PaaS) ย้ายไป repo ของตัวเองพร้อมประวัติ git:

- [`bookchaowalit/platform-infra-kit`](https://github.com/bookchaowalit/platform-infra-kit) — `infra/platform/**` เดิม, `platform_validation_test.py`, `git_file_mode.py`, `APP-INVENTORY.yaml` และ workflow `platform-validate.yml`
- [`bookchaowalit/platform-homelab-infra`](https://github.com/bookchaowalit/platform-homelab-infra) — `infra/self-hosted-stack/**` (compose, Traefik, Frappe)

ที่เหลือในโฟลเดอร์นี้เป็น registry ที่ทั้ง repo (scripts/platform/*, guards, MCP) อ่าน จึงอยู่ที่นี่:

| ไฟล์ | ผู้ใช้ |
|---|---|
| `domains.yaml` | `task domains:check`, `scripts/platform/{domains_registry,cloudflare_dns,estate_health,hub_site}.py`, `scripts/utils/tools_hub.py` |
| `cloud-providers.yaml` | `task cloud:providers:check`, `scripts/platform/{cloud_providers,monitor_health}.py` |
| `building-block-services.yaml` | `task platform:services:check`, `scripts/platform/building_block_services.py` |
| `gpu-worker-catalog.json` | `scripts/platform/gpu_worker_policy.py` |
| `llm-lab-models.json` | `scripts/platform/llm_lab_budget.py` |
| `paas-action-policy.json` | `scripts/platform/paas_action.py` |
| `media-transcription-routing.json` | routing contract ของ media worker (`bookchaowalit/content-media-worker` ถือสำเนา vendored) |
