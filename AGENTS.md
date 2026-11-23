# BC Call Center (`d:\yalecom`) — Project Rules

## Auto-Deploy, README & GitHub Push Standing Instruction
ทุกครั้งที่พัฒนา/แก้ไขโค้ดในโปรเจกต์นี้เสร็จและผ่านการตรวจสอบแล้ว ให้ดำเนินการอัตโนมัติครบทั้ง 4 ขั้นตอนโดยไม่ต้องรอถามซ้ำ:
1. **Build & Verify**: รัน `npm run typecheck`, `npm run build` (และ `py_compile` หากแก้ไฟล์ Python) ให้ผ่าน `0` errors
2. **Auto-Deploy**: รีสตาร์ท production server (`NODE_ENV=production node server.mjs`) และตรวจสอบว่า https://callcenter.bcsmethai.com/ ตอบ `HTTP 200 OK`
3. **Update README**: อัปเดต `README.md` (และภาพหน้าจอหาก UI เปลี่ยนชัดเจน โดยเบลอ IP/เบอร์มือถือส่วนตัวเสมอ) ให้ตรงกับฟีเจอร์ล่าสุด
4. **Secret Scan & Push GitHub**: สแกนไฟล์ที่จะ commit ไม่ให้มี secret/API key/รหัสผ่าน/Public IP/เบอร์ส่วนตัว/ไฟล์ `.env*`/`data/`/`jarvis/models/` แล้ว commit และ `git push origin main` ไปยัง https://github.com/jaturapornchai/bccallcenter

## Architecture Constraints
- **Strict Workspace Isolation (กฎเหล็ก)**: ทำทุกอย่างภายในโฟลเดอร์โปรเจกต์ `d:\yalecom` นี้ทั้งหมด ห้ามสร้าง แก้ไข ลบ สตาร์ท หรือออกไปยุ่งกับโฟลเดอร์อื่นข้างนอกเด็ดขาด
- **Inbound-Only**: ระบบรับสายเข้าอย่างเดียว ไม่มีฟีเจอร์โทรออก (Outbound)
- **Brand**: ใช้ชื่อ **ระบบ BC Call Center** และผู้ช่วยเสียง **J.A.R.V.I.S** (ไม่ใช้ `javis.bcsmethai.com`)
- **Unified Repo**: รวม Frontend/SIP Engine (`server.mjs`, `lib/`, `src/`), `jarvis/` และชุด `livekit-voice-ai/` อยู่ในโปรเจกต์นี้ทั้งหมด
