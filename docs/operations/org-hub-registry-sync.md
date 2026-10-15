# Organization hub registry sync

ชุดคำสั่งนี้อัปเดต metadata จาก GitHub ให้ hub ของแต่ละ Org และตรวจลิงก์ของ
umbrella กับ personal hub โดยเก็บทะเบียนไว้ใน repo เจ้าของแต่ละแห่ง

## การเรียกใช้งาน

รันจาก checkout ของ `bookchaowalit/platform-ops-cli` และระบุที่อยู่ของทะเบียน
กับโฟลเดอร์ที่เก็บ hub โดยตรง ใช้ Python standard library, Git และ GitHub CLI
ที่ล็อกอินด้วยบัญชีเจ้าของอยู่แล้ว

```bash
task hubs:sync -- --index /home/bookchaowalit/bookchaowalit-empire/registry/hubs.json --checkouts-root /home/bookchaowalit
task hubs:sync -- --index /home/bookchaowalit/bookchaowalit-empire/registry/hubs.json --checkouts-root /home/bookchaowalit --check
task hubs:sync -- --index /home/bookchaowalit/bookchaowalit-empire/registry/hubs.json --checkouts-root /home/bookchaowalit --apply --publish
```

- ค่าเริ่มต้นแสดง preview; `--check` ออกด้วย code 1 เมื่อมีข้อมูลต่างจาก GitHub
- `--apply` อัปเดตไฟล์และ commit เฉพาะไฟล์ที่กำหนด; `--publish` จึงจะ push ด้วย
- Code 2 หมายถึงมีสิ่งที่ต้องแก้ เช่น checkout มีงานค้าง, branch/remote ไม่ตรง,
  สิทธิ์ Org เปลี่ยน, GitHub อ่านไม่ได้ หรือมี Org ใหม่ที่ยังไม่ลงทะเบียน
- ถ้าข้อมูลเหมือนเดิม จะคง snapshot date และไม่สร้าง commit

## ขอบเขตข้อมูล

Umbrella เก็บเฉพาะ hub metadata และตำแหน่งไฟล์ projection ในแต่ละ hub ผ่าน
`organizations[].sync.inventory_path` และ `organizations[].sync.readme_path`
personal `solo-empire` เป็น reference ที่ตรวจการเข้าถึงและ metadata ของ hub
แต่ทะเบียนเดิมของ personal hub ยังคง schema และเจ้าของเดิม

Org hubs ใช้ทะเบียน `repository-catalog/registries/repositories.json` และส่วน
`ORG-HUB INVENTORY` ใน README ส่วน Work Hub ใช้
`repository-catalog/registries/github-repositories.json` กับ
`repository-catalog/GITHUB-REPOSITORIES.md` แยกจาก client registry เดิม
ทะเบียน GitHub นี้แสดง repo ที่อยู่ใน Org ตาม provider; client registry อาจอ้างถึง
repo ของลูกค้าที่มี owner อื่นด้วย จึงมีขอบเขตต่างกัน

Worker อ่านเฉพาะ GitHub repository metadata และไฟล์ projection ที่ตั้งค่าไว้
เก็บ archived/private repos และ default branch ตาม GitHub โดยไม่รวม hub เอง
ตรวจ owner, URL, path, symlink และ marker ก่อนเขียน ไม่อ่านเนื้อหา product หรือ
เอกสารลูกค้า และไม่รวบรวม product records มาเก็บใน umbrella

## ตัวตั้งเวลาในเครื่อง

```bash
task hubs:timer -- --index /home/bookchaowalit/bookchaowalit-empire/registry/hubs.json --checkouts-root /home/bookchaowalit --activate
systemctl --user list-timers bookchaowalit-org-hub-sync.timer
systemctl --user start bookchaowalit-org-hub-sync.service
systemctl --user status bookchaowalit-org-hub-sync.service
journalctl --user -u bookchaowalit-org-hub-sync.service -n 20 --no-pager
```

Installer จะตรวจ live `--check` ว่าครบทุก hub และไม่มี drift ก่อนเปิด timer
ชื่อ `bookchaowalit-org-hub-sync.timer` เวลา **09:00 ทุกวัน Asia/Bangkok**
เป็น user systemd service บนเครื่องที่ติดตั้ง ใช้ GitHub session ของ user นั้น
เครื่องต้องเปิดและ user service manager ต้องทำงาน หากพลาดเวลา `Persistent=true`
จะรันชดเชยเมื่อ timer กลับมาทำงาน Run ที่ข้อมูลไม่เปลี่ยนจะไม่ส่งการแจ้งเตือน
หรือสร้าง Git commit ใช้ local journal และ runtime report สำหรับตรวจสถานะ

สถานะครั้งล่าสุดอยู่ที่ `.runtime/org-hub-sync.json` ใน umbrella ซึ่งถูก Git ignore
ไม่มี token หรือเนื้อหาเอกสารอยู่ในรายงาน Lock ในที่เดียวกันป้องกัน writer ซ้อนกัน

## เมื่อมีงานค้างหรือ publish ไม่ผ่าน

Worker จะข้าม hub ที่มี working tree dirty หรือ branch/remote ไม่ตรง และรายงาน
ความสำเร็จบางส่วนให้เห็นชัดเจน Checkout ที่สะอาดและตามหลังอย่างเดียวสามารถ
fast-forward ได้ใน apply mode ส่วน unpushed/diverged commits ต้องให้เจ้าของจัดการ
ถ้า push ถูกปฏิเสธ worker จะเก็บ local commit ไว้และรายงาน `git_push_failed`
ตรวจสถานะและประสานงานที่ repo เจ้าของก่อนรันใหม่ ห้าม force-push หรือทิ้งงานค้าง

เมื่อต้องหยุดงานอัตโนมัติ:

```bash
systemctl --user disable --now bookchaowalit-org-hub-sync.timer
```
