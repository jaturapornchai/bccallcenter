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
- **Unified Repo (รวม BC Brain เป็นเนื้อเดียวกัน ไม่ผ่าน MCP API)**: รวม Frontend/SIP Engine (`server.mjs`, `lib/`, `src/`), ผู้ช่วยเสียง **J.A.R.V.I.S** (`jarvis/`), คลังความรู้ **Embedded BC Brain** (`bcbrain/vault/`, อ่านตรงเข้า RAM ผ่าน `jarvis/brain_mirror.py` พร้อม mtime hot-reload โดยไม่ผ่าน MCP HTTP API), และชุด `livekit-voice-ai/` อยู่ในโปรเจกต์นี้ทั้งหมด
- **ปรับปรุงคลังความรู้ใน `bcbrain/vault/` ได้โดยตรง**: เมื่อพบช่องว่างความรู้จากสายจริงหรือได้ข้อมูลยืนยันจากลุงจืด ให้เพิ่ม/แก้ไขโน้ต `.md` ใน `bcbrain/vault/` ของโปรเจกต์นี้ได้ทันที (ห้ามเดาหรือแต่งเติมข้อมูลเอง, ล็อกโซน `audience: "user"` ปลอดศัพท์โค้ด/SQL สำหรับสายลูกค้า, และสแกนความลับก่อน commit เสมอ) ส่วนหัวข้อที่ยังรอข้อเท็จจริงจากลุงจืดให้จดไว้ใน `docs/brain-wishlist.md`

## UX/UI Ownership Rule (Claude Opus)
- **ให้ Claude Opus รับผิดชอบงาน UX/UI หน้าจอทั้งหมดของโปรเจกต์นี้**: ทุกงานออกแบบ ปรับเลย์เอาต์ ปรับสไตล์/ธีม จัดตำแหน่งองค์ประกอบ และตรวจรีวิว UX/UI บนหน้าเว็บทั้งหมด (`src/app/**`, `src/components/**`, `jarvis/static/**`) ให้มอบหมายหรือให้ **Claude Opus** (รุ่นสูงสุดที่มี เช่น `Claude Opus 5.5 (High)`) เป็นผู้รับผิดชอบหลักเสมอ
- **มาตรฐาน UX/UI ที่ต้องยึด**: ภาษาไทยเข้าใจง่ายสำหรับผู้ใช้คนไทย, Baseline-First (ปุ่ม/แท็บ/ช่องกรอก/ไอคอน/Badge ในแถวเดียวกันอยู่ระนาบ Baseline เป๊ะ), ใช้ Global Shared CSS (`.cc-*` / `.tone-*`) รองรับทั้งโหมดมืดและโหมดสว่าง 100% และตรวจสอบหน้าจอจริงผ่าน Browser หลัง Deploy เสมอ
