---
title: Task Tracker Linkage Pilot — 2026-10-14
created: 2026-10-14
updated: 2026-10-14
status: verified-linkage
lifecycle: historical
description: หลักฐานการผูก task หนึ่งรายการระหว่าง Trello, Tasks API, Jira และ Linear evidence parent
---

# Task Tracker Linkage Pilot — 14 ตุลาคม 2026

ทดลองกับ Trello card ที่มีอยู่จริง สร้างตัวแทนใน Tasks API และ Jira แล้วผูกกับ
issue เดิมใน Linear ตามคำขอเจ้าของ อ่านกลับครบทุกระบบและตรวจการส่ง key เดิมซ้ำ.
**ผลที่ตรวจรับได้คือการผูก ID และลิงก์ของ pilot; งาน UI ยังไม่ได้เริ่ม และไม่มี continuous sync.**

## ตัวตนและบทบาท

Source key: `trello:69b6b2394811358126a19b4d`

| ระบบ | ตัวตน | บทบาท / สถานะที่อ่านกลับ |
|---|---|---|
| Trello | [Create rudimentary UI for the website](https://trello.com/c/aj3gvoZe/8-create-rudimentary-ui-for-the-website) | ต้นทางขอบเขตงาน; booknbook / Backlog; complete=false |
| Tasks API | #177; `01a13b0d-976a-7a17-94f8-2b95bb175612` | task หลักของเรา; todo; phase Backlog; version 3 |
| Jira | [KAN-1](https://bookchaowalit.atlassian.net/browse/KAN-1) | ตัวแทนงาน UI สำหรับ pilot; To Do; labels tracker-pilot/phase-backlog/needs-repository |
| Linear | [BOO-27](https://linear.app/bookchaowalit/issue/BOO-27/หลกฐานความสามารถ-product-engineering) | evidence parent ของ pilot; Backlog; เกณฑ์ Product Engineering เดิมยังอยู่ |
| Repository | bookchaowalit/platform-ops-cli | เก็บหลักฐานการทดลองนี้; product repository ของ card ยัง unresolved |

Linear ปฏิเสธสร้าง issue ใหม่เพราะ workspace เต็ม free issue quota.
เจ้าของเลือกใช้ issue เดิม จึงเพิ่มหลักฐานใน BOO-27 ที่มีขอบเขต per-product
API/test/release evidence. BOO-27 เป็น parent ที่ติดตามหลักฐาน ไม่ใช่ mirror
ของงาน UI; ห้ามนำสถานะของ parent ไปปิดงาน UI หรือใช้ legacy task #136
ใน description เดิมแทน task #177.

Trello card ไม่มี requirement description ก่อนทดลอง และยังไม่ได้ยืนยัน repo
ที่รับผิดชอบ UI. ชื่อบอร์ด booknbook ไม่เพียงพอสำหรับจับคู่กับ repo ในประวัติ.
ไม่มีการสร้าง product checkout เปล่าหรืออ้าง build/test ของ UI.
เฉพาะหลักฐาน integration pilot อยู่ใน owning repository เครื่องมือ.

## การดำเนินงาน

1. อ่าน card ต้นทางและตรวจ external key ใน Tasks API ก่อนสร้าง; ไม่พบ task เดิม.
2. ใช้ supported Tasks API external upsert ด้วย source=trello และ card object ID.
   สร้าง task #177 แล้วอ่านกลับได้ UUID ตรงกัน.
3. สร้าง Jira Task KAN-1 ในโครงการ KAN ซึ่งมีอยู่แล้ว พร้อม source key และ task UUID.
4. เพิ่มส่วน pilot ใน BOO-27 โดยคง title, status และ description เดิมทุกส่วน.
5. เพิ่มลิงก์กลับใน Trello description; คง title, list และ completion flag.
6. เก็บ Linear/Jira IDs, role, evidence URL และ phase ใน Tasks API notes.
   เขียนด้วย If-Match จาก version ที่อ่านล่าสุด.
7. อ่านกลับและตรวจ source key, task UUID และลิงก์ของแต่ละระบบ.
8. ทวน external upsert และ stale-version rejection; ผลอยู่ใน snapshot ด้านล่าง.

API client ใช้ delegation `task-sync` ที่มี tasks.task:read/write ผ่าน client
ที่ตั้งค่าไว้แล้ว. ไม่บันทึก token, credential file, request headers หรือ
raw private transcript ในหลักฐาน.

## ผล verification

| การตรวจ | ผลจริง |
|---|---|
| Tasks API external lookup | trello card ID → task #177 / UUID ตรงกัน |
| External-key replay | outcome=unchanged; task number, UUID และ version เดิม |
| Stale If-Match | HTTP 412 / version_mismatch; state ไม่เปลี่ยน |
| Tasks API metadata | Linear BOO-27 role=evidence-parent และ Jira KAN-1 ตรงกัน |
| Trello readback | source key, task UUID, BOO-27 และ KAN-1 อยู่ใน description; ยัง Backlog/complete=false |
| Linear readback | source key, task UUID และ KAN-1 อยู่ใน pilot section; title/status/description เดิมคงอยู่ |
| Jira readback | source key, task UUID และ BOO-27 อยู่ใน description; status To Do |

ผลตรวจแบบ machine-readable อยู่ใน
[dated evidence snapshot](task-tracker-pilot-2026-10-14.json).
ไฟล์นี้เป็นผลสังเกต ณ วันที่บันทึก ไม่ใช่ task database หรือ live status view.

Repository checks: snapshot identity/roles/limits, preservation และคำสั่ง
read-only ที่ระบุด้านล่างผ่าน. Full `task test` ใน test environment ที่มีอยู่
ได้ 118/120 scripts passed, 1 skipped, 1 partial, 0 failed; status=partial.
ส่วนที่ยังตรวจไม่ครบคือ art_pipeline_test.py (ขาด ImageMagick) และ
portfolio_l3_test.py (ไม่มี actual portfolio manifest). ไม่อ้าง full release gate.

## ทวนตัวตนในระบบเรา

จาก root ของ checkout platform-ops-cli ใช้ client ที่ตั้งค่าไว้แล้ว:

```bash
python3 - <<'PY'
import json, sys
sys.path.insert(0, 'infra/scripts/utils')
from task_store import open_task_store
store = open_task_store(None, 'task-sync', channel='cli')
task = store.find_external('trello', '69b6b2394811358126a19b4d')
assert task and task['id'] == 177
assert task['external_source'] == 'trello'
assert task['external_id'] == '69b6b2394811358126a19b4d'
notes = json.loads(task['notes'])
assert notes['source_key'] == 'trello:69b6b2394811358126a19b4d'
assert notes['linear']['id'] == 'BOO-27'
assert notes['linear']['role'] == 'evidence-parent'
assert notes['jira']['key'] == 'KAN-1'
print(json.dumps({'number': task['id'], 'status': task['status'],
                  'identity_links_verified': True}))
PY
```

อ่าน Trello/Jira/Linear ด้วย ID ที่ระบุในตารางเพื่อตรวจส่วนที่เหลือ.
หาก retry native create หลังผลไม่แน่นอน ให้ค้น source key และอ่าน ID ที่คืนมา
ก่อนสร้างอีกครั้ง; title ที่คล้ายกันไม่ใช่หลักฐานว่าเป็น task เดียวกัน.
การตรวจ replay ที่รันวันนี้ใช้ existing external key และ If-Match โดยไม่สร้าง
task เพิ่ม. หากพบ version conflict ต้องอ่านและทบทวนการเปลี่ยนแปลงก่อนเขียน.

## สถานะและงานต่อไป

Tasks API ปัจจุบันรองรับ todo, in_progress, done, blocked, cancelled.
Backlog อยู่ใน phase/readiness metadata. Review เป็นข้อเสนอให้เก็บ phase
แยกจาก in_progress; ยังไม่ได้ทดสอบ live Review transition หรือสร้าง worker
ที่ทำ mapping นี้อัตโนมัติ. Closed ต้องแยก cancellation/archive/completion ก่อน.

- Verified: identity linkage, native readbacks, external-key replay,
  stale-version rejection และ preservation checks ของ pilot หนึ่งรายการ.
- Observed: Linear issue quota และ card ที่ยังไม่มี scope/repo ยืนยัน.
- Planned: กำหนด requirement/product repo, conflict ownership matrix,
  background sync worker, event deduplication, Review/Closed mapping และ
  reconciliation ข้าม trackers.

ก่อนขยาย ให้แยก execution mirror ออกจาก evidence parent ใน mapping registry
และเลือกงานที่มี repo/acceptance ชัดเจน. การปิดงานต้องอิงเกณฑ์ product;
ผลเชื่อมลิงก์ผ่านไม่เปลี่ยน task #177, KAN-1, card ต้นทาง หรือ BOO-27 เป็น Done.

deployment_skipped_no_target: งานนี้เปลี่ยนเฉพาะ task metadata และ evidence documents;
ไม่มี application code หรือ deployment target ใน scope pilot นี้.
