# Tracker sync pilot

สถานะการรันจริงให้ดูจากผล preflight และ timer บนเครื่องที่ติดตั้งเสมอ การมีโค้ดหรือผ่าน tests ไม่ได้แปลว่า background sync เปิดแล้ว

Pilot นี้จำกัดเฉพาะ Tasks API #177 / Trello card `69b6b2394811358126a19b4d` / Jira KAN-1 / Linear BOO-27. งานพัฒนาระบบ sync อยู่ task #180; ไม่ใช่งาน UI #177.

## เจ้าของข้อมูล

| ข้อมูล | เจ้าของ | ปลายทาง |
| --- | --- | --- |
| ชื่องาน | Trello card | Tasks API title, Jira summary |
| สถานะทำงาน | Tasks API task #177 | Jira status, Trello list, status block ใน Linear |
| ขั้น Review | `notes.tracker_sync.phase` ใน Tasks API | Jira In Review, Trello Review |
| ขอบเขต UI / เนื้อหาที่คนเขียน | ต้นทางของแต่ละรายการ | คงข้อความเดิม |
| สถานะและเกณฑ์ Product Engineering | BOO-27 | คงงานแม่เดิม |

`todo` → Backlog / To Do, `in_progress` → WIP / In Progress, `in_progress` พร้อม phase `Review` → Review / In Review, `done` → Done. `blocked`, `cancelled` หรือ phase ที่ขัดกับ status ทำให้หยุดและรายงาน ไม่เดาความหมายจาก Closed. เมื่อยังไม่มี product repository/requirements งาน UI ยังคงไม่ได้เริ่ม.

ตัวรันนี้ใช้ Linux main thread และตัด blocking I/O ตามเส้นตาย60วินาที ด้วย systemd deadline90วินาทีเป็น backstop. ถ้าผู้เรียกมี active alarm อยู่แล้วหรือเรียกจาก thread อื่น ระบบจะหยุดก่อนอ่าน provider เพื่อรักษาสภาพของผู้เรียก. กรณีทำได้บางส่วนก่อน timeout รายงานและ pending journal จะเก็บหลักฐานการเปลี่ยนแปลงไว้.

## ตรวจและเปิดรัน

รันจาก checkout ของ ops-toolkit:

```sh
python3 infra/scripts/sync/tracker_sync.py --config infra/platform/tracker-sync-pilot.json --preflight --json
python3 infra/scripts/sync/tracker_sync.py --config infra/platform/tracker-sync-pilot.json --state-dir .runtime/tracker-sync-pilot --json
```

คำสั่งแรกตรวจข้อมูลเชื่อมต่อ คำสั่งที่สองเป็น dry-run. ให้ตรวจ planned changes ก่อน apply ครั้งแรก:

```sh
python3 infra/scripts/sync/tracker_sync.py --config infra/platform/tracker-sync-pilot.json --state-dir .runtime/tracker-sync-pilot --apply --json
python3 infra/scripts/sync/install_tracker_sync_timer.py --activate
systemctl --user list-timers tracker-sync-pilot.timer
systemctl --user status tracker-sync-pilot.timer tracker-sync-pilot.service
journalctl --user -u tracker-sync-pilot.service -n 20
```

Installer ไม่เปิด timer เมื่อ preflight หรือ live dry-run ไม่ผ่าน และไม่เขียนทับ unit เดิมที่ไม่ตรงกับ template. Preflight ตรวจ presence ของ provider credentials และการเปิด delegated Tasks client; ยังไม่รับรอง authentication จนกว่าจะอ่าน API จริงสำเร็จ. Timer รันทุก15นาทีพร้อม jitter สูงสุด30วินาทีและ catch-up หลังกลับมาเปิดเครื่อง; ต้องมีเครื่องและ user service ทำงาน. ไม่มีบริการ cloud ที่ติดตั้งหรือเสียเงินเพิ่ม. เมื่อไม่มีการเปลี่ยนแปลง tick จะเงียบ ส่วน conflict/failure/change อยู่ใน JSON log; ยังไม่มีการส่งข้อความหรือ notification ไปช่องทางภายนอก.

หยุดรันได้ด้วย `systemctl --user disable --now tracker-sync-pilot.timer`. หากมี execution กำลังทำงานให้รอจนจบก่อนแก้ checkpoint; ห้ามลบ state เพื่อบังคับข้าม conflict. คืนค่าข้อมูลผ่าน tracker เจ้าของ field แล้วอ่านกลับก่อนรันซ้ำ.

## ข้อมูลเชื่อมต่อ

ใช้ existing secret resolver `_env.get_env` และ delegated Tasks API client `task-sync`. ชื่อค่าที่ต้องเตรียม: `LINEAR_API_KEY`, `TRELLO_API_KEY`, `TRELLO_API_TOKEN`, `JIRA_EMAIL`, `JIRA_API_TOKEN`. เก็บใน secret manager หรือไฟล์ environment ส่วนตัวที่เครื่องนี้อ่านได้; ห้ามใส่ค่าใน Git, command arguments, URLs, logs หรือแชต. การเชื่อมต่อผ่าน plugin ในแชตไม่ได้ให้ credentials แก่ worker.

ใช้ Jira token สำหรับ site ที่ allowlist ระบุและมีสิทธิ์อ่าน/แก้ KAN-1/transition; Trello token สิทธิ์ read/write, Linear key อ่านและแก้ description ของ parent. Worker จำกัด objects เพิ่มอีกชั้น แม้ provider credential จะมีสิทธิ์กว้างกว่า. หากใช้ scoped Jira API token ที่ต้องผ่าน `api.atlassian.com/ex/jira/<cloudId>` transport นี้ยังไม่รองรับ: อย่าใช้กับ tenant-origin Basic adapter โดยไม่เพิ่ม/ตรวจ contract ก่อน.

## การเขียนชนและการกู้คืน

Tasks API title update ใช้ `If-Match` version. ก่อนเขียน tracker จะอ่านซ้ำ ตรวจ managed fields กับ baseline/intent และเก็บ intent hashes ก่อนส่ง. อ่านกลับสำเร็จจึงอัปเดต checkpoint. ถ้า status/list/managed block ถูกแก้ด้วยมือและไม่ตรงกับค่าที่ควรเป็น ระบบ hold ให้ตรวจ. ไม่สร้างรายการใหม่ ไม่ส่ง comments ไม่ลบหรือ archive งาน.

`.runtime/tracker-sync-pilot` เป็นข้อมูล lock, hashes, pending intents และรายงานแบบจำกัด ไม่ใช่สำเนาฐานงาน. อย่าใช้ JSON evidence เก่าจาก linkage pilot เป็นสถานะปัจจุบัน. การ sync หลาย provider ไม่มี transaction เดียว; Jira/Trello/Linear reread guard ยังมีช่วงเวลาที่แก้ชนหลังอ่านได้ ต้องประเมินรายงาน partial/conflict และตรวจปลายทางก่อนแก้เอง.

429/Retry-After ทำให้เลื่อน tick; read retry มีขอบเขตและ write ที่ไม่รู้ผลต้องตรวจ readback ไม่ส่งซ้ำแบบเดา. ไม่เปิด native Jira–Linear sync ซ้อนกับ worker นี้.

Provider references: [Trello authorization](https://developer.atlassian.com/cloud/trello/guides/rest-api/authorization/), [Trello cards API](https://developer.atlassian.com/cloud/trello/rest/api-group-cards/), [Jira issues API](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issues/), [Linear GraphQL](https://linear.app/developers/graphql).

## ขยาย scope

ก่อนเพิ่ม task ต้องยืนยัน product repository, provider object/workspace IDs, field ownership และ status/transition mapping. เปลี่ยน allowlist พร้อม tests และ review แยกครั้ง ไม่ scan/import ทั้งบอร์ดอัตโนมัติ. BOO-27 ยังคงเป็น evidence parent ของ pilot นี้เท่านั้น.
