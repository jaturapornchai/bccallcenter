# ระบบ BC Call Center + J.A.R.V.I.S + CRM (AI 5-Line Inbound Command Center)

ศูนย์บัญชาการรับโทรศัพท์อัตโนมัติ **5 คู่สาย** (Inbound-Only) ที่รวม **Next.js 15 Command Center + Node.js SIP/RTP Engine + J.A.R.V.I.S Voice Agent (`./jarvis`) + ระบบ CRM 360° & บอร์ดติดตามเคส + ระบบสนทนาหลายภาษาอัตโนมัติ + Ollama Cloud DeepSeek QA** ไว้ในโปรเจกต์เดียว

![ระบบ BC Call Center — โหมดมืด (Command Center Standby)](docs/screenshots/command-center-dark.png)

![ระบบ BC Call Center — โหมดสว่าง (J.A.R.V.I.S กำลังสนทนาสด)](docs/screenshots/command-center-light.png)

![ระบบ BC Call Center — หน้าจอ CRM 360° และจัดการเคสลูกค้า](docs/screenshots/crm-workspace.png)

---

## Objective (เป้าหมายของระบบ)
- **รวมระบบไว้ในที่เดียว (Unified Monorepo)**: รวม Frontend/SIP Engine (`server.mjs`, `lib/`, `src/`), ผู้ช่วยเสียง **J.A.R.V.I.S** (`jarvis/`) และชุดสถาปัตยกรรม **LiveKit SIP + LiveKit Agents** (`livekit-voice-ai/`) ไว้ในโปรเจกต์เดียว
- **รับสายเข้าพร้อมกันสูงสุด 5 คู่สาย (Inbound-Only)** ผ่าน SIP Trunk (UDP 5060 / G.711 PCMU 8kHz) พร้อม Motion Graphic เรดาร์/คลื่นเสียง Equalizer แนวศูนย์บัญชาการ และโหมดมืด/สว่าง
- **ระบบหลายภาษาอัตโนมัติ + เปลี่ยนภาษากลางสายได้ (Multi-Language & Translation)**:
  - **เบอร์เดิม**: จำภาษาประจำของเบอร์นั้นจาก CRM (`preferredLanguage`) ใช้ฟัง (STT) และแปลคำตอบจากฐานความรู้เป็นภาษาของลูกค้าทันที
  - **เบอร์ใหม่**: ค่าเริ่มต้นเป็น **ภาษาไทย** พร้อมทักทายทันทีว่าคุยได้ทุกภาษา (Pre-rendered 0ms wait) และหากลูกค้าพูดภาษาอื่นเข้ามา ระบบจะถามยืนยันด้วยภาษานั้นก่อนสลับภาษาถาวร
  - **เปลี่ยนภาษากลางสายได้ตลอด**: ลูกค้าพูดขอเปลี่ยนภาษาเมื่อไหร่ หรือเจ้าหน้าที่กดเลือกภาษาบนการ์ดคู่สาย ระบบสลับทั้งการฟัง การแปลคำตอบ และบันทึกภาษาล่าสุดลง CRM ทันที
- **ระบบ CRM 360° + บอร์ดติดตามเคสอัตโนมัติ**:
  - Screen-Pop แสดงชื่อลูกค้า บริษัท ระดับ (`VIP` / `ต้องดูแลพิเศษ`) ภาษา และจำนวนเคสค้างทันทีที่สายเข้า
  - แนบข้อมูล CRM + โน้ต + เคสค้าง + ประวัติการคุยเดิมให้ **J.A.R.V.I.S** เพื่อคุยต่อเนื่องได้เหมือนพนักงานคนจริง
  - สร้างเคสติดตาม (`TCK-...`) ให้อัตโนมัติเมื่อ DeepSeek QA พบว่าสายนั้นยังแก้ไม่จบหรือต้องให้เจ้าหน้าที่ติดต่อกลับ
- **วิเคราะห์คุณภาพสายอัตโนมัติหลังวางสาย (Post-Call QA)** ด้วย **Ollama Cloud (`deepseek-v4.1-flash`)** แสดงคะแนน CSAT (`/10`), คะแนนคุณภาพ AI (`/10`), อารมณ์และแนวโน้มอารมณ์ลูกค้า, หัวข้อ/สรุปบทสนทนา, สถานะและวิธีแก้ไข, ความเร่งด่วน/Follow-up และ AI Coaching Tip
- **ระบบยืนยันตัวตนด้วย Google Sign-In และโหมดผู้ชมแบบปกปิดเบอร์ (Privacy & Read-Only Mode)**:
  - หากยังไม่ได้เข้าสู่ระบบด้วยบัญชีผู้ดูแล (`ADMIN_EMAIL`) หน้าเว็บจะเข้าสู่ **โหมดผู้ชม (ดูได้อย่างเดียว)** โดยอัตโนมัติ
  - เซิร์ฟเวอร์จะปกปิดหมายเลขโทรศัพท์ของผู้โทรทุกเบอร์ (เป็นรหัสแทน เช่น `•••-•••-••#01`), ชื่อ/บริษัทลูกค้า, IP ของ SIP Trunk และเบอร์โทรในบทสนทนา/สรุป QA ก่อนส่งผ่าน WebSocket (โดยยังแสดงเบอร์ Call Center หลักจาก `SIP_USER` ได้)
  - ปิดกั้นคำสั่งควบคุมสาย, การแก้ไข CRM/เคส, การสั่งวิเคราะห์ QA ใหม่ และการรับ-ส่งเสียงสนทนาทั้งฝั่ง UI และฝั่งเซิร์ฟเวอร์ WebSocket จนกว่าจะเข้าสู่ระบบด้วยบัญชีผู้ดูแลผ่าน Google Sign-In (`HttpOnly` HMAC-signed cookie)

---

## Architecture & Workflow

```mermaid
flowchart LR
    Caller["ลูกค้าโทรเข้า (PSTN / มือถือ)"] --> SIP["SIP Trunk (UDP 5060 / RTP)"]
    SIP --> Engine["Node.js SIP/RTP Engine (server.mjs + lib/sip-engine.mjs)"]
    Engine <-->|WebRTC PCMU/8000 + Barge-in + POST /language + SSE| Jarvis["J.A.R.V.I.S Voice Agent (jarvis/voice_agent.py)"]
    Engine <-->|Customer 360° + Language + Auto-Ticket| CRM["CRM Store (lib/crm-store.mjs -> data/crm-db.json)"]
    Engine -->|Persist Transcript per Phone| Mem["CallMemoryStore (data/call-memory.json)"]
    Engine -->|Post-Call Transcript| QA["Ollama Cloud DeepSeek (lib/call-analyzer.mjs)"]
    Engine <-->|WebSocket /ws/sip| UI["Next.js Command Center & CRM UI (Dark / Light)"]
```

### โครงสร้างโปรเจกต์
- `server.mjs` — Next.js Production/Dev Server + Auth API (`/api/auth/*`) + WebSocket (`/ws/sip`) พร้อมบังคับสิทธิ์ผู้ดูแล/ผู้ชม
- `lib/auth.mjs` — ตรวจสอบ Google ID Token, ออก/ตรวจ `HttpOnly` HMAC-SHA256 Session Cookie และปกปิดข้อมูลหมายเลขโทรศัพท์ผู้โทรฝั่งเซิร์ฟเวอร์ (`sanitizeStateForViewer`)
- `lib/sip-engine.mjs` — SIP UDP Trunk (5 คู่สาย), RTP Audio Bridge, Auto-Answer, Multi-Language Routing, CRM Sync และ Post-Call QA trigger
- `lib/jarvis-bridge.mjs` — WebRTC Bridge (`werift`, PCMU/8000), DataChannel/Energy Fast Barge-in และซิงก์ภาษา (`POST /language`)
- `lib/crm-store.mjs` — ฐานข้อมูล CRM โลคอล (โปรไฟล์ลูกค้า 360°, ภาษาประจำเบอร์, เคสติดตามงาน และ Auto-Ticket จาก AI QA)
- `lib/call-memory.mjs` — เก็บประวัติการสนทนารายเบอร์และสร้างบริบทคุยต่อเนื่อง (`data/call-memory.json`)
- `lib/call-analyzer.mjs` — วิเคราะห์หลังวางสายผ่าน Ollama Cloud (`deepseek-v4.1-flash`)
- `jarvis/` — ซอร์สโค้ด **J.A.R.V.I.S Voice Agent** (`voice_agent.py`, `config.py`, `grounding.py`, `openthai_asr.py`, `google_tools.py`, `desktop_agent.py`, `static/`) รองรับ STT/TTS หลายภาษาและ `POST /language`
- `src/` — หน้าจอ **ระบบ BC Call Center** (โหมดมืด/สว่าง, Motion Graphic, แผง 5 คู่สายพร้อมปุ่มสลับภาษากลางสาย, ลิ้นชักผล QA และ **CRM Workspace**)
- `scripts/start-jarvis.ps1` — สคริปต์รัน J.A.R.V.I.S จากโฟลเดอร์ `jarvis/`
- `livekit-voice-ai/` — ชุดสถาปัตยกรรม **Full-Duplex LiveKit Server + LiveKit SIP + LiveKit Agents (Python v1.x)** (Inbound-Only + Asterisk PJSIP Registrar + Multi-Language STT/LLM + DTMF Fast-Path + SIP REFER Transfer + Thai Normalizer) ดูรายละเอียดเพิ่มเติมที่ [livekit-voice-ai/README.md](livekit-voice-ai/README.md)

---

## Configuration

### 1. ตั้งค่า BC Call Center (`.env.local`)
คัดลอก `.env.example` เป็น `.env.local` (**ห้าม commit `.env.local` หรือ `data/` ลง Git**):

| ตัวแปร | คำอธิบาย |
| :--- | :--- |
| `SIP_HOST` | Hostname หรือ IP ของ SIP Server |
| `SIP_FAILOVER_HOST` | IP สำรอง (ถ้ามี) |
| `SIP_PORT` | พอร์ต SIP (ค่าเริ่มต้น `5060`) |
| `SIP_USER` | Username / เบอร์ Trunk |
| `SIP_PASSWORD` | รหัสผ่าน SIP (เก็บใน `.env.local` เท่านั้น) |
| `SIP_MAX_LINES` | จำนวนคู่สายสูงสุด (ค่าเริ่มต้น `5`) |
| `PORT` | พอร์ต HTTP/WebSocket ของ Next.js (ค่าเริ่มต้น `3000`) |
| `JARVIS_URL` | Endpoint ภายในของ J.A.R.V.I.S (ค่าเริ่มต้น `http://127.0.0.1:7861`) |
| `AUTO_ANSWER_AI` | เปิดรับสายอัตโนมัติด้วย J.A.R.V.I.S (`true` / `false`) |
| `OLLAMA_BASE_URL` | Endpoint ของ Ollama Cloud (`https://ollama.com`) |
| `OLLAMA_MODEL` | โมเดล DeepSeek ล่าสุด (เช่น `deepseek-v4.1-flash`) |
| `OLLAMA_API_KEY` | API Key ของ Ollama Cloud (เก็บใน `.env.local` เท่านั้น) |
| `GOOGLE_CLIENT_ID` | Google OAuth 2.0 Web Client ID สำหรับปุ่มเข้าสู่ระบบด้วย Google (ต้องเพิ่มโดเมนใน Authorized JavaScript origins ใน Google Cloud Console) |
| `ADMIN_EMAIL` | อีเมลบัญชี Google ที่มีสิทธิ์ผู้ดูแลเต็มรูปแบบ (ดูเบอร์จริงและควบคุมระบบได้) |
| `AUTH_COOKIE_SECRET` | คีย์สุ่มสำหรับลงนาม `HttpOnly` Session Cookie (เก็บใน `.env.local` เท่านั้น) |

### 2. ตั้งค่า J.A.R.V.I.S (`jarvis/.env`)
คัดลอก `jarvis/.env.example` เป็น `jarvis/.env` แล้วใส่ `DEEPSEEK_API_KEY` (และเตรียมโฟลเดอร์โมเดลใน `jarvis/models/`):

```env
DEEPSEEK_API_KEY=your_deepseek_api_key_here
PORT=7860
PUBLIC_PORT=7861
PUBLIC_SSH=
```

---

## Dependencies & Usage

```bash
# 1. ติดตั้งและ Build ฝั่ง BC Call Center
npm install
npm run typecheck
npm run build

# 2. รัน J.A.R.V.I.S Voice Agent (เทอร์มินัลที่ 1)
npm run jarvis

# 3. รันระบบ BC Call Center (เทอร์มินัลที่ 2)
npm run start
```

---

## Limitations & Security Notes
- เบราว์เซอร์ต้องกดปุ่ม **เปิดเสียง/ไมค์** 1 ครั้ง (หรือกด **รับสายแทน AI**) เพื่อขอสิทธิ์ไมโครโฟนและปลดล็อก Autoplay Policy
- ไฟล์ความลับและข้อมูล runtime ทั้งหมด (`.env.local`, `jarvis/.env`, `jarvis/models/`, `jarvis/.venv/`, `jarvis/sessions/`, `livekit-voice-ai/.env`, `livekit-voice-ai/asterisk/pjsip.conf`, `data/`) ถูกกันออกจาก Git ผ่าน `.gitignore`
- ไม่มีรหัสผ่าน, API Key, Public IP หรือเบอร์มือถือส่วนตัวฝังในซอร์สโค้ดหรือภาพหน้าจอที่อยู่ใน Repository

---

## Credits & Open-Source Attribution
ระบบนี้พัฒนาต่อยอดและผสานการทำงานร่วมกับโครงการโอเพ่นซอร์สชั้นนำดังนี้:
- **Repository หลัก**: [jaturapornchai/bccallcenter](https://github.com/jaturapornchai/bccallcenter)
- **Web Framework**: [Next.js](https://github.com/vercel/next.js)
- **Node.js WebRTC Bridge**: [werift-webrtc](https://github.com/shinyoshiaki/werift-webrtc)
- **Real-time Voice Transport**: [FastRTC](https://github.com/gradio-app/fastrtc) & [LiveKit Agents](https://github.com/livekit/agents)
- **Speech-to-Text (STT)**: [Faster-Whisper](https://github.com/SYSTRAN/faster-whisper)
- **Multilingual Text-to-Speech (TTS)**: [k2-fsa/OmniVoice](https://github.com/k2-fsa/OmniVoice)
- **LLM & Post-Call QA**: [Ollama](https://github.com/ollama/ollama) (DeepSeek)
