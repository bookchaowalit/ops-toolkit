# Book Dev: ส่งงาน Linear ให้ AI อัตโนมัติ

สถานะการรันจริงให้ดูจาก timer และ journal บนเครื่องที่ติดตั้ง การผ่าน tests ไม่ได้แปลว่างานถูกส่งแล้ว

## ทำงานอย่างไร

`infra/scripts/workflows/book_dev_linear_cycle.py` รันหนึ่งรอบดังนี้:

1. อ่าน issue ใน Linear ที่มี label `ai-dispatch` และอยู่ในสถานะ backlog/unstarted (อ่านอย่างเดียว ไม่ย้าย ไม่ comment ไม่สร้าง issue)
2. ข้าม issue ที่มี label `รอเจ้าของ` หรือ `ติดเงื่อนไข`, issue ที่เคยส่งแล้ว และ issue ที่ชื่อไม่ขึ้นต้นด้วย `[ชื่อ-repo]` ที่ตรงกับ checkout ในเครื่องได้หนึ่งเดียว
3. เขียน handoff และ execution ticket ลง `data/runtime/agent-loop/book-dev/`
4. ส่ง ticket แรกตามลำดับ priority ให้ provider (ค่าเริ่มต้น `claude`) โดยรันภายใน repo เป้าหมาย

ส่งได้ไม่เกินหนึ่ง issue ต่อรอบ ประวัติอยู่ใน `data/runtime/agent-loop/book-dev/linear/state.json`
ถ้าหา CLI ไม่เจอ issue จะไม่ถูกนับว่าส่งแล้ว ถ้ารันแล้วล้มจะลองใหม่ได้ 3 ครั้ง ลบรายการใน `dispatched` เพื่อให้ส่ง issue นั้นใหม่

Prompt ของ dispatcher ทำตาม `book-dev-engineering-loop` skill: หลังตรวจผ่านจะ commit, push และ deploy ตามเป้าหมายที่ repo ระบุไว้
แต่ไม่ merge, ไม่ force-push และหยุดก่อนงาน production หรืองานที่ลบข้อมูล ติด label `ai-dispatch` เฉพาะ issue ที่พร้อมให้ AI ทำเองได้

## สิ่งที่ต้องเตรียม

- `LINEAR_API_KEY` ใน Infisical path `/linear` (หรือ secret provider ที่ตั้งไว้) สิทธิ์อ่านอย่างเดียวก็พอ
- Claude Code ที่ login แบบใช้งานนอกแอปได้ CLI ของ Claude desktop (`~/.claude/remote/ccd-cli/`) ใช้ token ของแอปเองและตอบ `Not logged in` เมื่อรันจาก timer
  ให้ติดตั้ง Claude Code CLI แล้ว login หรือใส่ `CLAUDE_CODE_OAUTH_TOKEN` ใน env file
- ลำดับการหา CLI: `BOOK_DEV_CLAUDE_CLI`, `PATH`, `~/.local/bin/claude`, `~/.claude/local/claude` แล้วจึงเป็น build ใหม่สุดใน `~/.claude/remote/ccd-cli/`

ตั้งค่าเพิ่มได้ใน `~/.config/book-dev-dispatch.env`:

```sh
BOOK_DEV_LINEAR_LABEL=ai-dispatch
BOOK_DEV_LINEAR_HOLD_LABELS=รอเจ้าของ,ติดเงื่อนไข
BOOK_DEV_DISPATCH_PROVIDERS=claude
BOOK_DEV_DISPATCH_MODE=implement
```

## คำสั่ง

```sh
task book-dev:linear:cycle -- --dry-run --json
task book-dev:linear:timer
task book-dev:linear:timer -- --activate
systemctl --user list-timers book-dev-dispatch.timer
journalctl --user -u book-dev-dispatch.service -n 40
systemctl --user disable --now book-dev-dispatch.timer
```

Timer รันทุก 2 ชั่วโมงตั้งแต่ 08:00 ถึง 22:00 (Asia/Bangkok) และไม่เริ่มรอบใหม่ระหว่างที่รอบก่อนยังทำงานอยู่
Timer ทำงานเฉพาะตอนที่เครื่องและ user service เปิดอยู่
