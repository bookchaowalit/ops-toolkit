# Document metadata check

ตัวตรวจ metadata ของเอกสารตามมาตรฐาน `bookchaowalit.document-metadata.v1`
(เฟส 2 ของ `bookchaowalit-empire/docs/operations/document-metadata.md`)
ตรวจทีละ repo และอ่านเฉพาะ front matter กับ sidecar ไม่อ่านเนื้อหาเอกสาร

## การเรียกใช้งาน

รันจาก checkout ของ `bookchaowalit/platform-ops-cli` ใช้ path แบบเต็ม หรือ path
ที่อิงจาก root ของ platform-ops-cli (Task รันคำสั่งจาก root ของ repo นี้)

```bash
task docs:meta:check -- /path/to/knowledge-base
task docs:meta:check -- /path/to/knowledge-base --json
task docs:meta:check -- /path/to/repo --visibility private
task docs:meta:next-id -- /path/to/knowledge-base
task test:docs-meta
```

- Exit 0 คือผ่าน, 1 คือมีเอกสารผิด (แสดงทีละไฟล์เป็น `path: code: message`),
  2 คือรันตรวจไม่ได้ เช่น หา registry ไม่เจอ (`registry_missing`), ไม่รู้ visibility
  ของ repo (`visibility_unknown`) หรืออ่าน GitHub ไม่ได้ (`github_read_failed`)
- `--json` พิมพ์ report schema `bookchaowalit.document-metadata-check.v1`
- `next-id` พิมพ์ `DOC-NNNN` ถัดจาก id สูงสุดที่ใช้แล้วใน repo
- ต้องมี `PyYAML` และ `jsonschema` (อยู่ใน `infra/requirements-dev.txt`)

## Schema และรายการหมวด

อ่านจาก checkout ของ `bookchaowalit-empire` ที่ `$BOOKCHAOWALIT_EMPIRE_ROOT`
ถ้าไม่ตั้งจะใช้ `~/bookchaowalit-empire` หรือระบุเองด้วย `--empire <path>`

- `registry/schemas/document-metadata.v1.schema.json`
- `registry/schemas/document-categories.v1.json` เป็นต้นฉบับของรายการหมวด

เทสต์ใช้ไฟล์จริงสองไฟล์นี้ ถ้าไม่มี checkout ของ empire เทสต์จะ skip

## สิ่งที่ตรวจ

| Code | ความหมาย |
|---|---|
| `schema` | ไม่ตรง JSON Schema เช่น ขาด field, วันที่ผิด, field ที่ไม่รู้จัก หรือมี `superseded_by` แต่ status ไม่ใช่ `deprecated` |
| `invalid_yaml`, `not_a_mapping` | อ่าน YAML ไม่ได้ หรือ sidecar ไม่ใช่ mapping |
| `unknown_category` | หมวดไม่อยู่ใน `document-categories.v1.json` |
| `duplicate_id` | `id` ซ้ำกับไฟล์อื่นใน repo (ไฟล์แรกตามลำดับ path ถือว่าเป็นเจ้าของ) |
| `classification_exceeds_visibility` | `confidential` หรือ `restricted` อยู่ใน repo public |
| `sidecar_file_missing` | `file` แบบ relative ชี้ไปไฟล์ที่ไม่มีอยู่ |
| `sidecar_file_unsafe` | `file` เป็น path แบบ absolute, ออกนอก repo หรือผ่าน symlink |

Visibility อ่านจาก `gh api repos/<owner>/<repo>` โดยหา `<owner>/<repo>` จาก remote `origin`
ใช้ `--visibility` เพื่อข้ามการเรียก GitHub เวลาทำงาน offline หรือในเทสต์
repo public เก็บได้ถึง `internal` ส่วน repo private และ internal เก็บได้ทุกระดับ
`file` ที่เป็น URI เช่น `s3://...` ไม่ถูกตรวจว่ามีอยู่จริง

## ไฟล์ที่ถูกข้าม

- ไฟล์ Markdown ที่ไม่มี front matter หรือมี front matter ของมาตรฐานอื่น เช่น front matter
  แบบ Obsidian ใน knowledge-base หรือ `schema: solo-empire.service.v1` จะไม่ถือว่า error
  ตัวตรวจจะตรวจเฉพาะ front matter ที่ `schema` ขึ้นต้นด้วย `bookchaowalit.document-metadata.`
  ส่วนไฟล์ `*.meta.yaml` จะถูกตรวจทุกไฟล์
- ไฟล์ที่เป็น symlink หรืออยู่ใต้ directory ที่เป็น symlink จะถูกข้าม ใช้กฎเดียวกับ hub sync
- ถ้าเป็น Git repo จะใช้ `git ls-files --cached --others --exclude-standard` ดังนั้น path ที่
  `.gitignore` ไว้จะไม่ถูกตรวจ ถ้าไม่ใช่ Git repo จะข้าม `.git`, `node_modules`, `.venv`, `venv`
  และ `__pycache__`
