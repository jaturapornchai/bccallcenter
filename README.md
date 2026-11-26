# ระบบ BC Call Center + J.A.R.V.I.S + CRM (AI 5-Line Inbound Command Center)

ศูนย์บัญชาการรับโทรศัพท์อัตโนมัติ **5 คู่สาย** (Inbound-Only) ที่รวม **Next.js 15 Command Center + Node.js SIP/RTP Engine + J.A.R.V.I.S Voice Agent (`./jarvis`) + ระบบ CRM 360° & บอร์ดติดตามเคส + ระบบสนทนาหลายภาษาอัตโนมัติ + Ollama Cloud DeepSeek QA** ไว้ในโปรเจกต์เดียว

![ระบบ BC Call Center — โหมดมืด (Command Center Standby)](docs/screenshots/command-center-dark.png)

![ระบบ BC Call Center — โหมดสว่าง (J.A.R.V.I.S กำลังสนทนาสด)](docs/screenshots/command-center-light.png)

![ระบบ BC Call Center — หน้าจอ CRM 360° และจัดการเคสลูกค้า](docs/screenshots/crm-workspace.png)

---

## Objective (เป้าหมายของระบบ)
- **ทำงานอัตโนมัติด้วย AI 100% และพัฒนาตัวเองได้ (Zero-Human & Self-Improving)**: J.A.R.V.I.S รับสายเข้าทั้ง 5 คู่สาย (`AUTO_ANSWER_AI=true`), ตอบคำถามและให้คำปรึกษาจนจบด้วยตัวเองในสาย (ห้ามชวนคุยกับพนักงานคนจริงหรือเสนอโอนสายเอง รับฝากเรื่องลงระบบเฉพาะเมื่อลูกค้าขอเองหรือใช้ BC Account เวอร์ชันเก่า), บันทึก CRM, วิเคราะห์ QA และพัฒนาปรับปรุงระบบในโปรเจกต์ `d:\yalecom` จากหลักฐานสายจริงได้ทุกส่วนภายใต้กฎเหล็ก **ห้ามเดา ห้ามมั่ว ห้ามแต่งเติมข้อเท็จจริงเอง**
- **รวมระบบไว้ในที่เดียว (Unified Monorepo)**: รวม Frontend/SIP Engine (`server.mjs`, `lib/`, `src/`), ผู้ช่วยเสียง **J.A.R.V.I.S** (`jarvis/`), **คลังความรู้ Embedded BC Brain (`bcbrain/vault/` — 384 โน้ต + 735+ เมนู อ่านตรงเข้า RAM + PostgreSQL 18 `pgvector` ภายในโปรเจกต์เดียว ไม่ต้องผ่าน MCP HTTP API)** และชุดสถาปัตยกรรม **LiveKit SIP + LiveKit Agents** (`livekit-voice-ai/`) ไว้ในโปรเจกต์เดียว
- **2 บทบาทหลักของ J.A.R.V.I.S (Dual Role — Sales + Call Center) แบบเป็นธรรมชาติไม่น่าเบื่อ**:
  - **พนักงานขายโปรแกรม BC Ai Account**: คุยแบบเพื่อนผ่อนคลาย มีอารมณ์ในน้ำเสียง ไม่ซักไซ้ถามเยอะ ดึงให้ลูกค้าอยากคุยต่อสบายใจ พร้อมแนะนำจุดเด่น/โมดูลจากคลังความรู้จริงและรับฝากข้อมูลติดต่อกลับให้ทีมเดโม
  - **พนักงาน Call Center แนะนำการใช้งาน**: ช่วยตอบวิธีใช้งาน เมนู ขั้นตอน การตั้งค่า และแก้ไขปัญหาโปรแกรม BC Ai Account ตรงจุดทีละขั้น
  - **คลังความรู้ Voice-First Big Brain ในเครื่อง (`pythainlp` ตัดคำไทย + PostgreSQL 18 `pgvector` HNSW + Hybrid RRF, ไม่ผ่าน MCP HTTP API, Hot-Reload อัตโนมัติ)**:
    - รวมคลังความรู้ **BC Brain (`bcbrain/vault/` — 384 โน้ต, 735+ เมนู 4 หลัก `1001`–`9070`, 600+ ข้อความ Error)** เป็นเนื้อเดียวกับโปรเจกต์นี้ โดยออกแบบเพื่อ **ระบบรับโทรศัพท์ด้วยเสียง (Voice-First)** โดยเฉพาะ: ตัดรูปภาพ Markdown, ลิงก์ YouTube และโน้ตสารบัญลิงก์สื่อล้วนออกทั้งหมด (`_strip_voice_media` + `_VOICE_EXCLUDE_SLUGS`) เพื่อนำพื้นที่งบคำตอบไปให้ข้อเท็จจริงและขั้นตอนที่ครบถ้วนเต็มที่ (`quick_answer` `ultra` 2,500 / `fast` 2,800 / `normal` 3,600 ตัวอักษร รวมได้สูงสุด 2–3 โน้ตที่เกี่ยวเนื่อง + แนบรหัสเมนู 4 หลักที่เกี่ยวข้องอัตโนมัติ และ Bootstrap Pack 25 โน้ตหลักครอบคลุมทุกโมดูล `1xxx`–`9xxx`)
    - **ตัดคำภาษาไทยเฉพาะทางบัญชี/ERP (`pythainlp` `newmm` + Custom BC Domain Dictionary)**: รวมคำเฉพาะทาง ~160+ คำ (เช่น `โปรโมชัน`, `ซื้อครบแถม`, `ต้นทุนแฝง`, `นำเข้าต่างประเทศ`, `อัตราแลกเปลี่ยน`, `ที่อยู่จัดส่ง`, `พิกัดดาวเทียม`, `กลุ่มสมาชิกรวมแต้ม`, `ใบกำกับภาษีอย่างย่อ`, `พักบิล`, `สต็อกติดลบ`, `เข้าทีหลังออกก่อน`, `หักณที่จ่าย`, `ภพ30`, `งบกำไรขาดทุน`, `แยกสาขา`, `หลายนิติบุคคล`) พร้อมกฎแก้คำเพี้ยน STT ภาษาไทย (`โปรโมชั่น`→`โปรโมชัน`, `ซื้อสองแถมหนึ่ง`, `พักบิน`, `ไลโฟ`, `อัตราแรกเปลี่ยน`, `ที่อยู่จากส่ง`, `กี่เบิร์`, `เก็บรูปได้กี่ลูก`, `รวมแต้`, `ตัดแทบ`) เพื่อไม่ให้ตัดคำเฉพาะทางแยกออกจากกัน
    - **PostgreSQL 18 `pgvector` HNSW + Thai `tsvector` GIN Hybrid RRF (`bccallcenter-pg` พอร์ต `127.0.0.1:5446`)**: แตกโน้ตและสารบัญ 735 เมนูเป็น **3,245 Section Chunks** ฝังเวกเตอร์ 1024 มิติด้วย Local Ollama **`bge-m3:latest`** (`jarvis/brain_pgvector.py`, `lib/brain-pgvector.mjs`, `docker-compose.brain.yml`) ค้นแบบ Single-SQL Hybrid RRF ผสาน **In-RAM BM25 + pgvector HNSW Cosine + PostgreSQL Thai GIN FTS** ทั้งใน J.A.R.V.I.S และช่องค้นหาบนหน้าเว็บ **สมอง BC** พร้อม Query Embedding LRU Cache, Incremental SHA-256 Sync อัตโนมัติ, Fail-Soft Circuit Breaker และ **Calibrated Semantic OOV Abstain Gate**
    - **ระบบตอบตรงเป้าหมายสูงสุด 4 ชั้น (Target-Aware Answer Precision Engine & Holistic Intent Understanding)**:
      1. **2-of-3 Triple-Ear Consensus & Domain Voting**: ซ่อมคำเพี้ยน Whisper GPU (`กี่ลูก`→`กี่รูป`, `กี่เบิร์`→`กี่เบอร์`, `รวมแต้`→`รวมแต้ม`, `ตัดแทบ`→`จัดแต้ม`) ตั้งแต่ด่านหูเมื่อ OpenThai `e2` & `e3` เห็นตรงกัน
      2. **Clean Topic Separation + Topic-Pinning**: ปักหมุดหัวข้อเดิมเมื่อถามต่อสั้นๆ แต่แยกบริบทอัตโนมัติเมื่อลูกค้าถามคุณสมบัติ/หัวข้อใหม่ชัดเจน (`_STANDALONE_TOPIC_RE`, `_is_explicit_topic_switch`, กรองคำบ่น/คำคลุมเครือออกจาก `_extract_recent_topic`) ไม่ให้คำถามเก่าปนเปื้อนคำถามใหม่
      3. **Question Target-Type Classifier 7 ประเภท + Target-Aware Section/FAQ Reranking + Multi-Aspect Fusion**: จำแนกเป้าหมายคำถาม (`count_limit`, `pricing_edition`, `where_menu_report`, `how_to_step`, `comparison`, `troubleshoot`, `yes_no_capability`) เพื่อดันคู่ Q&A/ตัวเลข/รหัสเมนูที่ตอบตรงเป้าขึ้นบนสุด พร้อมฟิวชันข้ามโน้ตอัตโนมัติ (เช่น นำเข้าต่างประเทศ `2051` + `3011` + `9021`, แนบรูป `5 ช่อง`/`25 MB`, ทะเบียนลูกหนี้เบอร์โทร/พิกัด GPS, กลุ่มสมาชิกรวมแต้ม) และคัด `BRAIN-NEARBY-SUPPLEMENT` เข้มขึ้น (`min_score=32.0`) ไม่ให้ดึงโน้ตคนละเรื่องมากวน
      4. **Bullseye-First Voice Prompt & Target Cue Injection**: ฉีดคำชี้เป้าหมาย 1 บรรทัดก่อนตอบให้ J.A.R.V.I.S ตอบตรงจุดตั้งแต่ประโยคแรกทันที (ฟันธง/จำนวน/ราคา/ชื่อเมนู+รหัส 4 หลักครบทุกหลัก), ยอมรับการแก้ชื่อลูกค้าและซิงก์ทับ CRM ทันที, เล่าจุดเด่นโมดูลจริงเมื่อถูกถามเทียบคู่แข่งโดยไม่ข้ามไป Excel, คง `bge-m3` ใน VRAM (`keep_alive=-1` + pre-warm ตอนสายเข้า), และตรวจผลค้นทั้งหมดก่อนแจ้ง "ยังไม่มีข้อมูลยืนยัน" (ชุดทดสอบ Golden Target-Precision Benchmark **112 คำถาม** รวมตรวจข้อเท็จจริงเป้าหมายแบบ `hit_all`: `python scripts/sync-brain-pgvector.py --benchmark`)
    - **วงจรพัฒนาสมองต่อเนื่อง (`Brain Miss Log` + เติมสมองจากหน้าเว็บ + โปรโมตคำสอน AI เข้า `pgvector`)**: บันทึกคำถามจากสายจริงที่ติด `Abstain` ลง `data/brain-miss-log.jsonl` (ปิดบังข้อมูลส่วนตัว 100%) จัดอันดับบนแท็บ **คำถามที่ตอบไม่ได้**; ผู้ดูแลสามารถกด **เพิ่ม/แก้ไขโน้ต**, กด **เติมลงสมอง** เพื่อปิดคิว **รอเติมเข้าสมอง** 1 คลิก (`POST /api/brain/notes`), หรือติ๊ก **บันทึกถาวรเข้าสมอง BC (pgvector)** ตอนกด **สอน AI** (`POST /api/teach`, `POST /api/brain/promote-teach`) ให้ Hot-Reload เข้า RAM ใน 3 วิ และซิงก์เข้า `pgvector` ทันที
    - แบ่งการค้นหาสายโทรศัพท์อัตโนมัติ: (1) `QA_CACHE` ตอบใน ~10ms (กรองคำถามกำกวม/Abstain/ติดชื่อลูกค้า), (2) **Zero-Blocking Search-Planner (`0.05s` cap + `0ms` Intent Expander + แยกบริบทหัวข้อใหม่จาก `user` turn ไม่ให้คำตอบบอทเก่าปน)**, และ (3) **In-Process Direct Hybrid Engine (`quick_answer`, `batch_ask`, `lookup_menu`, `lookup_error`, `batch_read`, `read_note`, `search_brain`, `traverse_brain`, `list_notes`)** (ซ่อน `find_media` จาก LLM สายโทรศัพท์) พร้อม `aioice` APIPA ICE filter
    - **ล็อกโซน `audience: "user"` 2 ชั้น (SQL + In-Memory) + Strict `🛑 Abstain` + Abstain-to-Nearby Verified Info Fallback**: สายลูกค้าและผู้ชมหน้าเว็บถูกบังคับให้อยู่ในโซนผู้ใช้งานเสมอ (ห้ามหลุดตาราง SQL/โค้ด/ไฟล์ภายใน); เมื่อ `quick_answer` / `batch_ask` คืน `🛑 Abstain` จะไม่แต่งเรื่องเอง แต่ค้นโน้ต/เมนูใกล้เคียงที่มีอยู่จริงจาก `BrainVaultMirror.search_nearby` (พร้อมตัวกรองคำบ่นลอยๆ เช่น `"ไม่เป็นอย่างงั้น"`/`"คุยต่อ"` ไม่ให้ดึงโน้ตผิดเรื่อง) และเสนอฝากเบอร์โทรกลับเฉพาะเมื่อจำเป็น ลูกค้าขอเอง หรือใช้ **BC Account เวอร์ชันเก่า** (ชุดทดสอบ: `python scripts/sync-brain-pgvector.py --benchmark`, `python scripts/test-mcp-startup.py`, `python scripts/test-voice-quality.py`)
  - **ส่งเบอร์โทรเข้า J.A.R.V.I.S + แนะนำตัวว่าเป็น AI + จำชื่อ & ยืนยันเบอร์โทรกลับ + รวม Callback Ticket เบอร์เดียวกัน + คำแนะนำโทรกลับให้พนักงาน**: ส่งหมายเลขผู้โทร (`caller_phone`) และข้อมูลลูกค้าจาก CRM เข้าสู่ J.A.R.V.I.S โดยตรงพร้อมเก็บโปรไฟล์รายเบอร์ (`jarvis/sessions/caller_profiles.json`): สายใหม่ครั้งแรกจะแนะนำตัวว่าเป็นผู้ช่วย **AI** และแจ้งว่าถ้าต้องการคุยกับพนักงานจริงสามารถแจ้งให้โทรกลับได้ พร้อมถามขอชื่อลูกค้าให้สำเร็จ ทักทายลูกค้าเก่าด้วยเสียงแคชทันที (`~2 วิ`), ตอบเบอร์โทรที่โทรเข้ามาได้ และหากขอคุยกับพนักงานจริงหรือขอให้โทรกลับจะถามยืนยันก่อนเสมอว่าจะให้โทรกลับเบอร์นี้หรือเบอร์อื่น และถามเรื่องที่ต้องการให้โทรกลับ (ปัญหาของโปรแกรมหรือเรื่องอื่น) หากสายนี้ยังไม่มีหัวข้อคุยต่อเนื่อง ก่อนบันทึก `leave_message` และส่ง event `callback_request` สร้าง Ticket ด่วน (`source: "ai_callback"`) **โดยหากเบอร์เดียวกันมี Ticket ที่ยังเปิดอยู่จะสรุปรวมเป็น 1 Ticket เดียวอัตโนมัติ** พร้อมแสดงรายละเอียดครบทุกประเด็น, **คำแนะนำในการโทรกลับให้พนักงาน** (จากกฎโดเมน + Post-Call QA), กระดิ่ง + **เสียงพูดแจ้งเตือนภาษาไทย (Web Speech API, 0 เครดิต ElevenLabs)**, แท็บ `โทรกลับ`, ปุ่มกด **`รับทราบแล้ว`** และ **`ปิด Ticket (พนักงานรับรู้แล้ว)`** ซิงก์เรียลไทม์ทุกหน้าจอ (พร้อมปกปิดข้อมูลในโหมดผู้ชม 100%, ชุดทดสอบ: `node scripts/test-callback-ticket.mjs`)
- **รับสายเข้าพร้อมกันสูงสุด 5 คู่สาย (Inbound-Only) พร้อมหน้าจอ Realtime 100%** ผ่าน SIP Trunk (UDP 5060 / G.711 PCMU 8kHz), WebSocket Heartbeat + Watchdog Auto-Reconnect (`LIVE` latency indicator), บทสนทนาสด (Live Transcript Feed + คำพูดสดแบบ Karaoke ทุก 180ms), Live Audio Meter จริง (ผู้โทร/AI), Motion Graphic เรดาร์/คลื่นเสียง และโหมดมืด/สว่าง
- **กระแสเสียงต่อเนื่อง 20ms Outbound RTP + ElevenLabs `eleven_v4_turbo` Streaming TTS + ITU-T G.711 μ-law & Telephony DSP**:
  - ตัวส่งกระแสเสียง `20ms` Drift-Compensated Outbound RTP Pacer พร้อม 60ms empty-queue jitter grace, Barge-in Flush และ **ตาราง ITU-T G.711 μ-law 16-bit (`MULAW_BIAS = 0x84`, `MULAW_MAX = 32635`)** ใน `lib/g711.mjs` / `src/lib/audio-bridge.ts` ทำให้เสียงบันทึก/เสียงออกสายคมชัดไม่แตกพร่า
  - รองรับ **ElevenLabs `eleven_v4_turbo` Streaming TTS** (`jarvis/elevenlabs_tts.py`, ปรับจูนเฉพาะสายโทรศัพท์ `stability 0.65`, `style 0.0`, `similarity_boost 0.72`, ปิด speaker boost) คู่กับ **Bandpass 110–3500 Hz + Soft Pause Noise Gate + Headroom Limiter `0.78`** ใน `jarvis/voice_agent.py` เพื่อตัดเสียงฮัม/ซ่าช่วงหยุดพูดและ fallback อัตโนมัติไป Local OmniVoice หากไม่ได้ตั้งค่า `ELEVENLABS_API_KEY` (ชุดทดสอบ: `python scripts/test-tts-audio-clean.py`)
  - **Local Disk TTS Cache (`jarvis/cache/tts/`) & Ollama Cloud DeepSeek FAQ Cache (`jarvis/cache/qa_cache.json`)**: เก็บไฟล์เสียง PCM 24kHz ลงดิสก์ (LRU สูงสุด 500 MB) และเก็บคำตอบ FAQ ที่ผ่าน Judge จาก **Ollama Cloud (`deepseek-v4.1-flash`, `reasoning_effort="none"`)** แล้ว (TTL 7 วัน, จับคำถามซ้ำ/หางเสียงต่าง/ใกล้เคียงอัตโนมัติ, กันคำตอบขัดข้องไม่ให้เข้า cache) เพื่อประหยัดทั้งเครดิต ElevenLabs และ LLM Token (ตรวจสถิติได้ที่ `GET /cache/stats`)
- **ระบบกันเสียงไอ/เสียงรบกวนรอบข้าง + Word-Verified Barge-in**:
  - กรองเสียงฮัม/ลมความถี่ต่ำด้วย **80 Hz High-Pass Filter** (`scipy.signal.butter/sosfilt`), **Noise Floor Gate (`-38 dBFS` สำหรับการรับสาย / `-31 dBFS` สำหรับพูดแทรกทั้ง word-verified และ confirmed-turn barge-in)**, ยืนยันเสียงพูดจริงด้วย STT VAD (`voiced()`) ใน `determine_pause` พร้อม **Semantic Incomplete-Turn Extension** (`0.55s` ปกติ / `0.95s` เมื่อค้างคำเชื่อม), **300ms VAD jitter tolerance + Hard Turn-End Cap** (กัน `EARLY_STT` ถูกยกเลิกซ้ำซ้อนและกันเดดแอร์ 10+ วิ), เก็บคำพูดค้าง (`carry`) ข้ามเธรด, กรองเสียงหลุดสั้นกว่า `0.15s` (พื้น `0.08s` สำหรับ `SIP_CUT_INS`) และกันจับคำเชื่อม/คำไทยที่มี `ชื่อ` แฝงเป็นชื่อลูกค้า พร้อมจับคำแก้ชื่อลูกค้าอัตโนมัติซิงก์ทับ CRM ทันที
  - ขณะบอทกำลังพูดจะไม่ตัดคำตอบจากเสียงคลิก/เสียงรบกวนสั้น `0.03s–0.13s` โดยตรวจทั้งความยาวเสียงพูดจริงจาก VAD (`voiced_s >= 0.12s` สำหรับคำสั่งหยุด/คีย์เวิร์ด และ `>= 0.22s` ทั่วไป) และ **OpenThai CPU Streaming** (`>= 3` ตัวอักษรฐาน, `barge_in_confirmed` + ล้างคิว 20ms RTP ทันที) ชุดทดสอบ: `python scripts/test-barge-in.py`, `python scripts/test-turn-onset.py`
- **ระบบฟัง 3 หูขนาน 6 ภาษา (Dual-Mode Triple-Ear ASR — ไทย / ลาว / อังกฤษ / จีน / ญี่ปุ่น / เกาหลี)**:
  - **สายภาษาไทย (GPU + CPU ขนานแบบแยก Pool)**: หูที่ 1 ใช้ **`typhoon-whisper-turbo`** บน GPU (`language="th"`, `BC_STT_PROMPT`, `repetition_penalty=1.15`, `no_repeat_ngram_size=3`, และตัวตรวจคำซ้ำพยางค์/คำโดเมนเห็นตรงกันของ `e2/e3` สลับไป OpenThai อัตโนมัติ, CER 2.3% ไม่หลอนเสียงไทยเป็นภาษาอังกฤษ) พร้อม GPU busy semaphore กันคิวกอง timeout เป็นลูกโซ่ คู่กับ **หูที่ 2 (`OpenThai 2.0 th-TH`)** และ **หูที่ 3 (`OpenThai 2.0 th-mix`)** บน CPU Pool แยกต่างหากสำหรับตรวจคำแทรก (Word-Verified Barge-in) และจับคำขอเปลี่ยนภาษา/ประโยคอังกฤษล้วน + อ่านออกเสียง URL (`www.bcaccount.com`) และ LINE ID (`@bcaccount`) เป็นภาษาไทยชัดเจนก่อนส่ง TTS
  - **สายภาษาอื่น (`en / zh / ja / ko / lo`)**: หูที่ 1 ใช้ **`faster-whisper-large-v3-turbo`** บน CPU (`int8`, `0 GB GPU VRAM`) ถอดเสียงเป็นอักษรของภาษานั้นแท้ๆ โดยไม่ทับศัพท์เป็นคำไทย พร้อม OpenThai CPU ขนานเพื่อรับคำสั่งกลับภาษาไทย
  - พูดเสียงแจ้งคั่นระหว่างค้นข้อมูลแบบสุ่ม 8 คำสั้นๆ เป็นธรรมชาติ (ไม่ขึ้นต้นด้วย "อ๋อ" ซ้ำซาก, Pre-rendered 0ms ไม่ใช้ GPU ตอนโทร)
- **บันทึกเสียงสนทนาจริง (Dual-Channel WAV) + เล่นย้อนหลัง Sync บทสนทนา + ปุ่มสอน AI 1 คลิก**:
  - `lib/call-recorder.mjs` บันทึกไฟล์เสียง Stereo 8kHz (`data/recordings/<sessionId>.wav`, ช่องซ้าย = ผู้โทร, ช่องขวา = J.A.R.V.I.S/เจ้าหน้าที่) สงวนสิทธิ์เปิดฟังเฉพาะแอดมินผ่าน `GET /api/recordings/:sessionId` (รองรับ HTTP Range Seek)
  - ในลิ้นชัก QA (`CallQaDrawer.tsx`) คลิกปุ่มเวลาในบทสนทนาเพื่อข้ามเสียงไปวินาทีนั้นทันที พร้อมปุ่ม **"สอน AI"** 1 คลิก (`POST /api/teach`) บันทึกคำตอบที่ถูกต้องลงคลัง FAQ ของ J.A.R.V.I.S เพื่อตอบได้ทันทีในสายถัดไป
- **Live Buying Signal + แดชบอร์ดสถิติ & Cache ROI + Emotion-Aware Voice + Auto Follow-up Card**:
  - แสดงป้าย **สัญญาณอารมณ์ & โอกาสปิดการขายแบบเรียลไทม์** (`สนใจซื้อสูง` / `กำลังพิจารณา` / `คุยสบายใจ` / `เริ่มกังวล`) บนการ์ดคู่สายและ Hero
  - แท็บ **สถิติ & ROI** (`AnalyticsWorkspace.tsx` + `GET /api/analytics`): สรุปจำนวนสายรายวัน/7 วัน, CSAT & AI Quality, สัดส่วนอารมณ์, Top หัวข้อ/โมดูลที่สนใจ, สถิติประหยัดเครดิต TTS/QA Cache และตารางจัดการคลังคำตอบที่สอน AI
  - **Emotion-Aware Voice Tuning**: ปรับโทนเสียง ElevenLabs `eleven_v4_turbo` อัตโนมัติ 3 อารมณ์ (`friendly` / `empathy` / `upbeat`) ตามบริบทคำพูด
  - **Auto Follow-up Message Card**: หลังวางสาย DeepSeek สร้างข้อความสรุปสำหรับส่งลูกค้า + แท็กโมดูลที่สนใจ พร้อมปุ่มคัดลอก 1 คลิกในหน้า QA (ไม่จำเป็นต้องเชื่อมต่อ LINE API)
- **ระบบหลายภาษา: หนึ่งสายหนึ่งภาษา เปลี่ยนได้เมื่อลูกค้ายืนยันเท่านั้น (Multi-Language & Translation)**:
  - **เบอร์เดิม**: จำภาษาประจำของเบอร์นั้นจาก CRM (`preferredLanguage`) ใช้ฟัง (STT) และแปลคำตอบจากฐานความรู้เป็นภาษาของลูกค้าทันที
  - **เบอร์ใหม่**: ค่าเริ่มต้นเป็น **ภาษาไทย** พร้อมทักทายทันทีว่าคุยได้ทุกภาษา (Pre-rendered 0ms wait)
  - **ตอบภาษาเดียวตลอดสาย**: AI ตอบเป็นภาษาของสายเท่านั้น ไม่สลับภาษาเองและไม่ถามเรื่องภาษาเอง คำที่ฟังเพี้ยนเป็นภาษาอื่น (เช่น "Okay." หรือคำจีนสั้นๆ) ไม่ทำให้ภาษาเปลี่ยน
  - **เปลี่ยนภาษาต้องถามและได้คำยืนยันก่อนทุกครั้ง**: เมื่อลูกค้าพูดภาษาอื่นชัดเจน หรือพูดขอเปลี่ยนภาษา ระบบถาม 1 คำถามด้วยภาษานั้น ("ต้องการเปลี่ยนเป็นภาษา…ไหม") ลูกค้าตอบรับในรอบถัดไป (ใช่/ครับ/Yes/好的 หรือพูดต่อเป็นภาษานั้นชัดเจน) จึงเปลี่ยนทั้งการฟัง การตอบ เสียงพูด และบันทึกลง CRM แล้วใช้ภาษานั้นไปจนกว่าจะยืนยันเปลี่ยนอีกครั้ง ถ้าปฏิเสธหรือไม่ตอบ คำถามนั้นถูกยกเลิกและคุยภาษาเดิมต่อ
  - **เจ้าหน้าที่เปลี่ยนให้ได้ทันที**: กดเลือกภาษาบนการ์ดคู่สาย ไม่ต้องถามยืนยัน
  - ชุดทดสอบกติกา: `python scripts/test-language-rules.py` (กรณีฟังเพี้ยนจากสายจริง + ลำดับถาม-ตอบ-เปลี่ยนภาษา + การเลือกหูหลัก)
- **ระบบ CRM 360° + บอร์ดติดตามเคสอัตโนมัติ**:
  - Screen-Pop แสดงชื่อลูกค้า บริษัท ระดับ (`VIP` / `ต้องดูแลพิเศษ`) ภาษา และจำนวนเคสค้างทันทีที่สายเข้า
  - แนบข้อมูล CRM + โน้ต + เคสค้าง + ประวัติการคุยเดิมให้ **J.A.R.V.I.S** เพื่อคุยต่อเนื่องได้เหมือนพนักงานคนจริง
  - สร้างเคสติดตาม (`TCK-...`) ให้อัตโนมัติเมื่อ DeepSeek QA พบว่าสายนั้นยังแก้ไม่จบหรือต้องให้เจ้าหน้าที่ติดต่อกลับ
- **วิเคราะห์คุณภาพสายอัตโนมัติหลังวางสาย (Post-Call QA)** ด้วย **Ollama Cloud (`deepseek-v4.1-flash`)** แสดงคะแนน CSAT (`/10`), คะแนนคุณภาพ AI (`/10`), อารมณ์และแนวโน้มอารมณ์ลูกค้า, หัวข้อ/สรุปบทสนทนา, สถานะและวิธีแก้ไข, ความเร่งด่วน/Follow-up และ AI Coaching Tip
- **โหมดผู้ชมดูได้ทุกฟีเจอร์พร้อมปกปิดความลับ 100% + สวิตช์เปิด-ปิดเสียง/ไมค์ & Auto-Sync แอดมินหลายคน**:
  - **ผู้ชมทั่วไปดูได้ครบทุกฟีเจอร์แบบ Read-Only**: เปิดดูหน้า `ศูนย์รับสาย`, `โทรกลับ`, `บันทึก QA`, `รายเบอร์`, `สายเข้า`, `ระบบ CRM 360°`, `บอร์ดติดตามเคส`, `สถิติ & ROI` และ **`สมอง BC`** (ดูคลังโน้ตโซนลูกค้า, ค้นหาสารบัญเมนู 4 หลัก `1001–9070`, คลิกข้าม `[[wikilinks]]`, และดูรายการ `Wishlist` ที่รอเติม — โดยฝั่งเซิร์ฟเวอร์ `lib/brain-store.mjs` บล็อกโน้ตโซนเทคนิค `dev` และปกปิดเบอร์มือถือสำหรับผู้ชม ขณะที่แอดมินสลับดูได้ทั้งโซนลูกค้า/เทคนิค/ทั้งหมด) และฝั่งเซิร์ฟเวอร์ (`lib/auth.mjs`) จะปกปิดข้อมูลความลับทุกจุดก่อนส่งออก ทั้งเบอร์โทร (`•••-•••-••#01`), ชื่อ/ชื่อเล่นลูกค้า (รวมชื่อที่ปรากฏในบทสนทนา/สรุป QA อิสระ), ชื่อร้าน/บริษัท, อีเมล, IP, โน้ต CRM ส่วนตัว, ชื่อพนักงาน, และ host/IP ของ SIP Trunk
  - **เห็นข้อมูลเต็มและควบคุมระบบเฉพาะบัญชีผู้ดูแล (`ADMIN_EMAIL` / `ADMIN_EMAILS`)**: สงวนสิทธิ์การเปิดฟังไฟล์เสียง (`/api/recordings/*`), เสียง RTP, การควบคุมสาย/CRM/Ticket และ `POST/DELETE /api/teach` ให้บัญชีผู้ดูแลที่ล็อกอิน Google ตรงกับรายการอีเมลใน `ADMIN_EMAIL` หรือ `ADMIN_EMAILS` (ใน `.env.local`, คั่นหลายบัญชีด้วย `,`) เท่านั้น และปิด `/api/internal/idle` เฉพาะ loopback
  - **สวิตช์เปิด–ปิดเสียง/ไมค์และเสียงเตือน Ticket สองทาง + ปิดเสียงโทรศัพท์อัตโนมัติ**: ปิดเสียงเรียกเข้าและสตรีมเสียง RTP เรียลไทม์อัตโนมัติสำหรับสายที่ AI ดูแล พร้อมสวิตช์ **เสียง/ไมค์** และ **เสียงเตือน Ticket** ที่กดเปิดและปิดกลับได้จริง (`AudioBridge.disableAudio()` ปิดสตรีมไมโครโฟนและหยุดเสียงสนทนาทันที), ปุ่ม **ฟังเสียง ↔ หยุดฟัง** รายสาย และป้องกันปุ่ม/ฟอร์มค้างทุกหน้าจอ (ชุดทดสอบ: `node scripts/test-security-and-audio.mjs`)
  - **Auto-Sync สำหรับผู้ดูแลเข้าพร้อมกันหลายคน (Multi-Admin Real-Time Sync)**: ซิงก์สถานะคู่สาย, คำสั่งควบคุมสาย, การแก้เคส CRM/ผู้รับผิดชอบ, การแจ้งเตือนเมื่อผู้ดูแลท่านอื่นอัปเดตข้อมูลลูกค้าระหว่างกำลังแก้ไข, คลังสอน AI (`data_sync`), และการแจ้งเตือน Toast (`admin_action` เฉพาะแอดมิน) รวมถึงตรวจสิทธิ์ Google Session และธีมข้ามแท็บอัตโนมัติ

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
- `server.mjs` — Next.js Production/Dev Server + Auth API (`/api/auth/*`), `/api/analytics`, `/api/teach`, `/api/recordings/*` + WebSocket (`/ws/sip`) พร้อมบังคับสิทธิ์ผู้ดูแลและกรองข้อมูลความลับสำหรับผู้ชม
- `lib/auth.mjs` — ตรวจสอบ Google ID Token, ตรวจสิทธิ์ `ADMIN_EMAIL`, ออก/ตรวจ `HttpOnly` HMAC-SHA256 Session Cookie และปกปิดข้อมูลความลับทุกจุดฝั่งเซิร์ฟเวอร์ (`sanitizeStateForViewer`, `sanitizeFreeTextForViewer`)
- `lib/sip-engine.mjs` — SIP UDP Trunk (5 คู่สาย), RTP Audio Bridge, Auto-Answer, Multi-Language Routing, CRM Sync และ Post-Call QA trigger
- `lib/jarvis-bridge.mjs` — WebRTC Bridge (`werift`, PCMU/8000), DataChannel/Energy Fast Barge-in และซิงก์ภาษา (`POST /language`)
- `lib/crm-store.mjs` — ฐานข้อมูล CRM โลคอล (โปรไฟล์ลูกค้า 360°, ภาษาประจำเบอร์, เคสติดตามงาน และ Auto-Ticket จาก AI QA)
- `lib/call-memory.mjs` — เก็บประวัติการสนทนารายเบอร์และสร้างบริบทคุยต่อเนื่อง (`data/call-memory.json`)
- `lib/call-analyzer.mjs` — วิเคราะห์หลังวางสายผ่าน Ollama Cloud (`deepseek-v4.1-flash`)
- `jarvis/` — ซอร์สโค้ด **J.A.R.V.I.S Voice Agent** (`voice_agent.py`, `brain_mirror.py`, `brain_pgvector.py`, `config.py`, `grounding.py`, `openthai_asr.py`, `google_tools.py`, `desktop_agent.py`, `static/`) รองรับ STT/TTS หลายภาษา, ตัดคำไทยพจนานุกรมเฉพาะทาง BC + PostgreSQL 18 `pgvector` HNSW Hybrid RRF และ `POST /language`
- `lib/brain-store.mjs` + `lib/brain-pgvector.mjs` — คลังความรู้ **สมอง BC** ฝั่ง Node.js (Hot-Reload + Hybrid Search ผ่าน `pgvector` + ล็อกโซน `user` สำหรับผู้ชม)
- `docker-compose.brain.yml` + `scripts/sync-brain-pgvector.py` — คอนเทนเนอร์ PostgreSQL 18 (`pgvector/pgvector:0.8.6-pg18`, พอร์ต `127.0.0.1:5446`) และสคริปต์สร้างดัชนี/ทดสอบความแม่นยำ 30 คำถาม (`--benchmark`)
- `src/` — หน้าจอ **ระบบ BC Call Center** (ค่าเริ่มต้น **โหมดสว่าง** สลับมืด/สว่างได้, Motion Graphic, แผง 5 คู่สายพร้อมปุ่มสลับภาษากลางสาย, ลิ้นชักผล QA และ **CRM Workspace**)
- `scripts/seed-sme-demo-data.mjs` — ล้างข้อมูลเก่าและจำลองชุดข้อมูลสาธิตธุรกิจ SME (`48` ราย, `6` ภาษา, `165` สาย, `32` เคส) พร้อมคำแนะนำโทรกลับ
- `scripts/start-jarvis.ps1` — สคริปต์รัน J.A.R.V.I.S จากโฟลเดอร์ `jarvis/`
- `scripts/dev-on-the-fly-scan.mjs` — สแกนสายใหม่ที่อัดเสียงไว้ (`data/recordings/*.wav`), กันรีวิวซ้อนกันด้วย lock (`--lock`), ดึง log ของ J.A.R.V.I.S ในช่วงเวลาของสาย โดยตัดบรรทัดไล่ที่อยู่เครือข่าย (ICE) ออกให้เหลือเฉพาะบทสนทนา (`--jarvis-log`), ตรวจสายว่างก่อน Deploy (`--check-idle`) และบันทึกผลรีวิว (`--mark-done`) สำหรับรอบพัฒนาตัวเองอัตโนมัติทุก **4 ชั่วโมง** (งานตั้งเวลา `yalecom-dev-on-the-fly` ในแอป Claude Desktop — ขั้นตอนทั้งหมดอยู่ที่ [docs/dev-on-the-fly.md](docs/dev-on-the-fly.md))
- `docs/brain-wishlist.md` — รายการ Markdown ค้างหัวข้อที่พบจากสายจริงของลูกค้าและต้องการให้ลุงจืดเพิ่มข้อมูลเข้า **BC Brain (สมอง)** (อัปเดตในรอบพัฒนาตัวเองทุก 4 ชม. พร้อมปิดบังข้อมูลส่วนตัวลูกค้า 100%)
- `scripts/wav-analyze.mjs` + `scripts/wav-transcribe.py` — วิเคราะห์ไฟล์เสียงสองช่องของแต่ละสาย: เวลาที่ AI ใช้ตอบ, การพูดแทรก/พูดทับ, ช่วงเงียบ, ระดับเสียง และถอดเสียงซ้ำแยกช่องด้วย Whisper large-v3-turbo พร้อมภาษาที่ตรวจพบ
- `scripts/restart-services.ps1` — รีสตาร์ท J.A.R.V.I.S / production server แบบเบื้องหลังเฉพาะเมื่อไม่มีสายค้าง แล้วตรวจว่าเว็บตอบ 200 (log อยู่ที่ `data/logs/server.log` และ `jarvis/logs/voice_agent.log`)
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
| `ADMIN_EMAIL` / `ADMIN_EMAILS` | อีเมลบัญชี Google ที่มีสิทธิ์ผู้ดูแลเต็มรูปแบบ (ดูเบอร์จริงและควบคุมระบบได้ รองรับหลายบัญชีโดยคั่นด้วยเครื่องหมายจุลภาค `,`) |
| `AUTH_COOKIE_SECRET` | คีย์สุ่มสำหรับลงนาม `HttpOnly` Session Cookie (เก็บใน `.env.local` เท่านั้น) |
| `BRAIN_PG_HOST` / `BRAIN_PG_PORT` | โฮสต์และพอร์ตของ PostgreSQL 18 `pgvector` สำหรับสมอง BC (`127.0.0.1` / `5446`) |
| `BRAIN_PG_DB` / `BRAIN_PG_USER` / `BRAIN_PG_PASSWORD` | ชื่อฐานข้อมูล, ผู้ใช้ และรหัสผ่านของ `bccallcenter-pg` (เก็บใน `.env.local` เท่านั้น) |
| `BRAIN_PG_ENABLED` | เปิด/ปิด Hybrid Vector Search (`true` / `false`; fail-soft ไปใช้ BM25 ใน RAM อัตโนมัติหากปิด) |
| `BRAIN_EMBED_URL` / `BRAIN_EMBED_MODEL` | Endpoint และโมเดล Embedding ของ Ollama โลคอล (`http://127.0.0.1:11434/api/embed`, `bge-m3:latest`) |

### 2. ตั้งค่า J.A.R.V.I.S (`jarvis/.env`)
คัดลอก `jarvis/.env.example` เป็น `jarvis/.env` แล้วใส่ `DEEPSEEK_API_KEY` (และเตรียมโฟลเดอร์โมเดลใน `jarvis/models/`):

```env
DEEPSEEK_API_KEY=your_deepseek_api_key_here
TTS_PROVIDER=auto
ELEVENLABS_API_KEY=your_elevenlabs_api_key_here
ELEVENLABS_VOICE_ID=H9eYlNHpm1vah6PPgYAk
ELEVENLABS_MODEL_ID=eleven_v4_turbo
PORT=7860
PUBLIC_PORT=7861
PUBLIC_SSH=
```

---

## Dependencies & Usage

```bash
# 1. สตาร์ท PostgreSQL 18 pgvector สำหรับสมอง BC และสร้างดัชนี + ทดสอบ 30 คำถาม
docker compose --env-file .env.local -f docker-compose.brain.yml up -d
python scripts/sync-brain-pgvector.py --benchmark

# 2. ติดตั้งและ Build ฝั่ง BC Call Center
npm install
npm run typecheck
npm run build

# 3. รัน J.A.R.V.I.S Voice Agent (เทอร์มินัลที่ 1)
npm run jarvis

# 4. รันระบบ BC Call Center (เทอร์มินัลที่ 2)
npm run start

# 5. (ทางเลือกแทนข้อ 3-4) เปิด/รีสตาร์ททั้งสองบริการแบบเบื้องหลังเมื่อไม่มีสายค้าง แล้วตรวจ HTTP 200
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/restart-services.ps1 -Target all

# 6. วิเคราะห์คุณภาพสายจากไฟล์เสียงที่อัดไว้
node scripts/wav-analyze.mjs <sessionId> --transcribe
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
- **LLM & Post-Call QA**: [Ollama](https://github.com/ollama/ollama) (DeepSeek & `bge-m3` Embeddings)
- **Thai NLP & Vector Search**: [PyThaiNLP](https://github.com/PyThaiNLP/pythainlp) & [pgvector](https://github.com/pgvector/pgvector) (PostgreSQL 18 HNSW + GIN)
