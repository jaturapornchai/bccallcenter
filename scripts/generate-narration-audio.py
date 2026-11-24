"""Generate broadcast-quality Thai male narration (ElevenLabs Decha) + studio ambient underscore
and build videos/work/master_audio.wav + videos/work/timeline.json (4-5 min 4K showcase)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import wave
from pathlib import Path

import httpx
import numpy as np
from scipy.signal import butter, sosfiltfilt

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / "jarvis" / ".env"
WORK_DIR = ROOT / "videos" / "work"
AUDIO_CACHE_DIR = WORK_DIR / "audio"
ASSETS_DIR = ROOT / "videos" / "assets"

DECHA_VOICE_ID = "deCz1h6mjB7HnY2xQz08"  # Decha - Confident, Deep, Resonant Thai Male
MODEL_ID = "eleven_v4_turbo"
SR = 48000  # YouTube broadcast stereo 48 kHz
SRC_SR = 44100  # ElevenLabs pcm_44100 high-fidelity master

SCENES = [
    {
        "id": "scene_01",
        "tag": "ปัญหาของธุรกิจ SME",
        "title": "สายหลุดนอกเวลา = เสียโอกาสขาย",
        "layout": "pain_point",
        "subtitles": [
            "หลายธุรกิจ SME ไทย พนักงานน้อย ลูกค้าโทรซ้อน พักเที่ยง หรือโทรนอกเวลาก็รับไม่ทัน",
            "ทุกสายที่หลุดไป คือยอดขายและความประทับใจที่เสียให้คู่แข่ง",
        ],
        "narration": (
            "หลายธุรกิจเอสเอ็มอีไทยเจอปัญหาเดียวกันครับ พนักงานมีจำกัด "
            "พอมีลูกค้าโทรเข้าพร้อมกันหลายสาย หรือโทรเข้ามาช่วงพักเที่ยง นอกเวลางาน และวันหยุด "
            "รับไม่ทัน ก็เสียทั้งโอกาสขายและเสียลูกค้าให้คู่แข่งครับ"
        ),
    },
    {
        "id": "scene_02",
        "tag": "แนะนำระบบ",
        "title": "BC Call Center + ผู้ช่วยเสียง AI J.A.R.V.I.S",
        "layout": "intro_hero",
        "subtitles": [
            "ระบบ BC Call Center เชื่อม AI J.A.R.V.I.S เข้ากับเบอร์ธุรกิจจริง 24 ชม.",
            "ทำหน้าที่ทั้งพนักงานขายและ Call Center พร้อมกัน 5 คู่สาย • ทดลองโทร 02-124-4148",
        ],
        "narration": (
            "พบกับ ระบบบีซีคอลเซ็นเตอร์ ที่เชื่อมผู้ช่วยเสียงเอไอ เจอร์วิส เข้ากับสายโทรศัพท์ธุรกิจจริงครับ "
            "เป็นทั้งพนักงานขายที่ช่วยแนะนำสินค้าและปิดการขาย และเป็นพนักงานคอลเซ็นเตอร์ช่วยตอบปัญหา "
            "รับพร้อมกันห้าคู่สายตลอดยี่สิบสี่ชั่วโมง ลองโทรคุยจริงได้ที่ ศูนย์สอง หนึ่งสองสี่ สี่หนึ่งสี่แปด ครับ"
        ),
    },
    {
        "id": "scene_03",
        "tag": "สถาปัตยกรรมอัจฉริยะ",
        "title": "เบื้องหลังการทำงานแบบเรียลไทม์",
        "layout": "architecture",
        "subtitles": [
            "SIP 5 คู่สาย -> ฟัง 3 หูขนานตัดเสียงรบกวน + พูดแทรกได้ -> คลังความรู้ BC Brain",
            "ตอบเสียงธรรมชาติในเสี้ยววินาที พร้อมวิเคราะห์ QA และบันทึกลง CRM อัตโนมัติ",
        ],
        "narration": (
            "เบื้องหลังการทำงานเริ่มจากคู่สายเอสไอพีห้าสายส่งเสียงเข้าสู่ระบบฟังสามหูที่กรองเสียงรบกวนและพูดแทรกได้เหมือนคนจริง "
            "จากนั้นค้นคำตอบจากคลังความรู้บริษัทแบบแม่นยำไม่มั่วข้อมูล สังเคราะห์เป็นเสียงพูดชัดเจนตอบกลับในเสี้ยววินาที "
            "พร้อมสรุปผลส่งเข้าสู่ระบบซีอาร์เอ็มอัตโนมัติครับ"
        ),
    },
    {
        "id": "scene_04",
        "tag": "จุดเด่นระดับองค์กร",
        "title": "คุยได้หลายภาษา ช่วยพนักงานได้เยอะ!",
        "layout": "multilang",
        "subtitles": [
            "คุยได้ 6 ภาษาอัตโนมัติ: ไทย • อังกฤษ • จีน • ญี่ปุ่น • เกาหลี • ลาว",
            "จำภาษาประจำเบอร์ใน CRM และสรุปภาษาไทยให้พนักงานไทยทำงานต่อได้ทันที ช่วยลดภาระได้มหาศาล",
        ],
        "narration": (
            "จุดเด่นสำคัญที่ช่วยพนักงานไทยได้เยอะมากคือ เจอร์วิสคุยได้หลายภาษาถึงหกภาษาอัตโนมัติครับ "
            "ทั้งไทย อังกฤษ จีน ญี่ปุ่น เกาหลี และลาว "
            "เมื่อลูกค้าต่างชาติโทรเข้ามา ระบบจะฟังออกและสลับภาษาให้ทันที พร้อมจำภาษาประจำเบอร์ไว้ในซีอาร์เอ็ม "
            "แล้วสรุปความต้องการเป็นภาษาไทยให้พนักงานทำงานต่อได้เลย ช่วยแบ่งเบาภาระได้เยอะโดยไม่ต้องจ้างล่ามเพิ่มครับ"
        ),
    },
    {
        "id": "scene_05",
        "tag": "ตัวอย่างหน้าจอจริง • โหมดสว่าง",
        "title": "ศูนย์บัญชาการ 5 คู่สาย โหมดสว่าง (ข้อมูลสาธิต)",
        "layout": "screen",
        "screenshot": "01-command-light.png",
        "callout": {
            "x": 0.04,
            "y": 0.30,
            "w": 0.56,
            "h": 0.64,
            "label": "ข้อมูลสาธิต: แผงรับสาย 5 คู่สาย • บทสนทนาสด • สัญญาณ Hot Lead",
        },
        "secondaryScreenshot": "02-command-dark.png",
        "showDemoBadge": True,
        "subtitles": [
            "หมายเหตุ: ข้อมูลลูกค้า เบอร์โทร และตัวเลขทั้งหมดบนหน้าจอเป็นข้อมูลจำลองเพื่อการสาธิตระบบ (Demo)",
            "ดูสถานะ 5 คู่สาย บทสนทนาสด สัญญาณ Hot Lead ในโหมดสว่างสบายตา ไม่มีเสียงโทรกวนโต๊ะทำงาน",
        ],
        "narration": (
            "มาชมหน้าจอการทำงานจริงในโหมดสว่างกันครับ "
            "ขอแจ้งให้ทราบก่อนว่า ข้อมูลลูกค้า เบอร์โทร และสถิติทั้งหมดบนหน้าจอนี้เป็นข้อมูลจำลองเพื่อการสาธิตระบบนะครับ "
            "บนศูนย์บัญชาการ คุณจะเห็นทั้งห้าคู่สายแบบเรียลไทม์ บทสนทนาสด ป้ายสัญญาณลูกค้าพร้อมซื้อ "
            "เลือกสลับโหมดสว่างหรือโหมดมืดได้ตามถนัด พร้อมปิดเสียงเรียกเข้าบนเว็บไม่ให้กวนสมาธิพนักงานครับ"
        ),
    },
    {
        "id": "scene_06",
        "tag": "ระบบแนะนำการทำงานให้พนักงาน",
        "title": "รวม Ticket เบอร์เดียวกัน + คำแนะนำในการโทรกลับ",
        "layout": "screen",
        "screenshot": "03-callback-focus.png",
        "callout": {
            "x": 0.14,
            "y": 0.24,
            "w": 0.72,
            "h": 0.68,
            "label": "รวมเคสเบอร์เดียวกันเป็น 1 Ticket + คำแนะนำให้พนักงานโทรกลับ",
        },
        "showDemoBadge": True,
        "subtitles": [
            "เบอร์เดิมโทรซ้ำ ระบบสรุปรวมเป็น 1 Ticket เดียว ไม่ซ้ำซ้อน พร้อมเสียงพูดแจ้งเตือนภาษาไทย",
            "มีกล่อง 'คำแนะนำในการโทรกลับให้พนักงาน' เตรียมข้อมูลให้พร้อมก่อนโทรและปิดเคสใน 1 คลิก",
        ],
        "narration": (
            "ส่วนที่ช่วยแนะนำการทำงานให้พนักงานได้ดีที่สุดคือ ระบบแจ้งเตือนโทรกลับครับ "
            "เมื่อลูกค้าขอคุยกับพนักงานจริง เจอร์วิสจะแนะนำตัวว่าเป็นเอไอ ถามชื่อ ยืนยันเบอร์ และเก็บประเด็นให้ครบ "
            "ถ้าเบอร์เดิมโทรซ้ำ ระบบจะสรุปรวมเป็นหนึ่งทิกเก็ตเดียวไม่ซ้ำซ้อน พร้อมเสียงพูดเตือนบนเว็บ "
            "และมีกล่องคำแนะนำในการโทรกลับเป็นข้อๆ ว่าควรเปิดสายยังไง เตรียมข้อมูลหรือรีโมตอะไร แล้วกดปิดเคสในคลิกเดียวครับ"
        ),
    },
    {
        "id": "scene_07",
        "tag": "คุมคุณภาพทุกสาย 100%",
        "title": "AI วิเคราะห์หลังวางสาย + สอน AI ง่ายใน 1 คลิก",
        "layout": "screen",
        "screenshot": "04-qa-drawer.png",
        "callout": {
            "x": 0.56,
            "y": 0.10,
            "w": 0.41,
            "h": 0.84,
            "label": "คะแนน CSAT • เทรนด์อารมณ์ • ข้อความส่งต่อ • สอน AI 1 คลิก",
        },
        "showDemoBadge": True,
        "subtitles": [
            "ทุกสายหลังวางสายมี DeepSeek QA ให้คะแนน CSAT และสรุปแนวทางแก้ไขครบถ้วน",
            "คัดลอกข้อความสรุปส่งลูกค้าได้ทันที หรือกด 'สอน AI' เพิ่มคำตอบใหม่ในคลิกเดียว",
        ],
        "narration": (
            "หลังวางสายทุกสาย เอไอจะวิเคราะห์คุณภาพการสนทนาให้ทันทีครับ "
            "แสดงคะแนนความพึงพอใจซีแซท เทรนด์อารมณ์ลูกค้า และสรุปแนวทางแก้ไข "
            "พร้อมข้อความสุภาพให้คัดลอกส่งไลน์ต่อลูกค้าได้เลย และหากอยากสอนคำตอบใหม่ของร้านเพิ่ม "
            "แค่กดปุ่ม สอนเอไอ เพียงคลิกเดียว สายถัดไปก็ตอบได้ทันทีโดยไม่ต้องเขียนโค้ดครับ"
        ),
    },
    {
        "id": "scene_08",
        "tag": "ฐานข้อมูลลูกค้าครบวงจร",
        "title": "ระบบ CRM 360 องศา & บอร์ดติดตามเคส",
        "layout": "screen",
        "screenshot": "05-crm-360.png",
        "callout": {
            "x": 0.14,
            "y": 0.25,
            "w": 0.72,
            "h": 0.68,
            "label": "จำชื่อ ระดับ VIP ภาษาประจำเบอร์ (6 ภาษา) และบอร์ดติดตามเคส",
        },
        "secondaryScreenshot": "06-crm-tickets.png",
        "showDemoBadge": True,
        "subtitles": [
            "จำชื่อ บริษัท ระดับ VIP ภาษาประจำเบอร์ และประวัติสายเดิม คุยต่อเนื่องเหมือนพนักงานประจำ",
            "บอร์ดติดตามเคสแยกความเร่งด่วนชัดเจน ไม่ตกหล่นแม้แต่สายเดียว",
        ],
        "narration": (
            "ในหน้าซีอาร์เอ็มสามร้อยหกสิบองศาและบอร์ดติดตามเคส ระบบจะเก็บชื่อลูกค้า ชื่อร้าน ระดับวีไอพี "
            "ภาษาประจำตัวทั้งหกภาษา และประวัติการคุยย้อนหลังทั้งหมด "
            "เวลาลูกค้าเก่าโทรกลับมา เจอร์วิสจะทักชื่อและคุยต่อจากเรื่องเดิมได้ทันทีเหมือนพนักงานประจำ "
            "และจัดลำดับความเร่งด่วนบนบอร์ดติดตามเคสไม่ให้ตกหล่นครับ"
        ),
    },
    {
        "id": "scene_09",
        "tag": "ROI & ความปลอดภัยข้อมูล",
        "title": "แดชบอร์ดสถิติ ROI + โหมดผู้ชมปกปิดความลับ",
        "layout": "screen",
        "screenshot": "07-analytics-roi.png",
        "callout": {
            "x": 0.14,
            "y": 0.18,
            "w": 0.72,
            "h": 0.72,
            "label": "ข้อมูลสาธิต: กราฟ 7 วัน • Hot Leads • Cache ROI • โหมดผู้ชมปกปิดความลับ",
        },
        "secondaryScreenshot": "08-viewer-privacy.png",
        "showDemoBadge": True,
        "subtitles": [
            "ข้อมูลสาธิตในแดชบอร์ด ROI: ยอดสาย 7 วัน Hot Leads CSAT และความประหยัดจาก Cache",
            "โหมดผู้ชมเปิดดูได้ทุกฟีเจอร์ แต่ปกปิดเบอร์ ชื่อ บริษัท และไฟล์เสียงเฉพาะผู้ดูแลระบบ",
        ],
        "narration": (
            "แท็บสถิติและอาร์โอไอจะสรุปตัวเลขสาธิตให้ผู้บริหารวัดผลความคุ้มค่าได้ชัดเจนครับ "
            "ทั้งยอดสายเจ็ดวัน ลีดพร้อมซื้อ คะแนนความพึงพอใจ และความประหยัดจากระบบแคช "
            "พร้อมโหมดผู้ชมที่เปิดดูได้ครบทุกฟีเจอร์ แต่ฝั่งเซิร์ฟเวอร์จะปกปิดเบอร์โทร ชื่อลูกค้า และไฟล์เสียงทั้งหมดให้เห็นเฉพาะผู้ดูแลระบบครับ"
        ),
    },
    {
        "id": "scene_10",
        "tag": "คุ้มค่าสำหรับ SME ไทย",
        "title": "เปลี่ยนพนักงาน 1 คน ให้รับสายได้เหมือนทีม 5-10 คน",
        "layout": "sme_value",
        "subtitles": [
            "รับสาย 24 ชม. 5 คู่สาย • คุยได้ 6 ภาษา • ความรู้องค์กรไม่หายแม้พนักงานเปลี่ยน",
            "เหมาะกับร้านยาง อู่ซ่อมรถ รับเหมาก่อสร้าง ค้าส่ง-ปลีก คลินิก และโรงงานไทย",
        ],
        "narration": (
            "สรุปแล้ว ระบบบีซีคอลเซ็นเตอร์ช่วยเอสเอ็มอีไทยได้อย่างคุ้มค่ามากครับ "
            "รับสายยี่สิบสี่ชั่วโมงพร้อมกันห้าสาย คุยได้หกภาษา ช่วยให้พนักงานคนเดียวทำงานได้เหมือนทีมห้าถึงสิบคน "
            "ประหยัดทั้งเงินเดือน ค่าโอที และความรู้สินค้าที่สอนไว้ยังอยู่กับบริษัทตลอดไป "
            "เหมาะทั้งร้านยาง อู่ซ่อมรถ รับเหมาก่อสร้าง ร้านค้าส่ง คลินิก โรงงาน และงานบริการทุกขนาดครับ"
        ),
    },
    {
        "id": "scene_11",
        "tag": "ทดลองใช้งานจริงวันนี้",
        "title": "โทรทดสอบกับ J.A.R.V.I.S ได้เลยที่ 02-124-4148",
        "layout": "cta_finale",
        "subtitles": [
            "ลองสัมผัสประสบการณ์จริง! โทรเลยตอนนี้ที่ 02-124-4148 ตลอด 24 ชั่วโมง",
            "ลองถามข้อมูล ลองสลับภาษา หรือฝากให้พนักงานโทรกลับ • callcenter.bcsmethai.com",
        ],
        "narration": (
            "หากสนใจนำระบบนี้ไปช่วยเพิ่มยอดขายและดูแลลูกค้าให้ธุรกิจของคุณ "
            "เชิญโทรเข้ามาทดลองคุยจริงกับเจอร์วิสได้เลยตอนนี้ที่เบอร์ ศูนย์สอง หนึ่งสองสี่ สี่หนึ่งสี่แปด "
            "ลองถามคำถาม ลองเปลี่ยนภาษา หรือฝากเรื่องให้พนักงานโทรกลับได้ตลอดยี่สิบสี่ชั่วโมงครับ ขอบคุณครับ"
        ),
    },
]


def load_api_key() -> str:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("ELEVENLABS_API_KEY="):
            return s.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("ELEVENLABS_API_KEY missing in jarvis/.env")


def synthesize_decha_wav(client: httpx.Client, scene_id: str, text: str) -> Path:
    import subprocess
    from scipy.signal import resample_poly

    AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    sig = hashlib.sha256(f"{DECHA_VOICE_ID}|{MODEL_ID}|v2|{text}".encode("utf-8")).hexdigest()[:16]
    wav_path = AUDIO_CACHE_DIR / f"{scene_id}_{sig}.wav"
    if wav_path.exists() and wav_path.stat().st_size > 8000:
        return wav_path

    body = {
        "text": text,
        "model_id": MODEL_ID,
        "language_code": "th",
        "voice_settings": {
            "stability": 0.64,
            "similarity_boost": 0.78,
            "style": 0.08,
            "use_speaker_boost": False,
            "speed": 0.90,
        },
    }

    pcm48 = None
    last_err = ""
    for fmt in ("mp3_44100_192", "mp3_44100_128", "pcm_24000"):
        url = f"/v1/text-to-speech/{DECHA_VOICE_ID}?output_format={fmt}"
        resp = client.post(url, json=body)
        if resp.status_code == 200:
            if fmt.startswith("mp3"):
                proc = subprocess.run(
                    ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar", str(SR), "pipe:1"],
                    input=resp.content,
                    capture_output=True,
                    check=True,
                )
                pcm48 = np.frombuffer(proc.stdout, dtype="<i2").astype(np.float32) / 32768.0
            else:
                usable = len(resp.content) - (len(resp.content) % 2)
                pcm24 = np.frombuffer(resp.content[:usable], dtype="<i2").astype(np.float32) / 32768.0
                pcm48 = resample_poly(pcm24, 2, 1).astype(np.float32)
            print(f"    {scene_id}: synthesized via {fmt}")
            break
        last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
    if pcm48 is None:
        raise RuntimeError(f"ElevenLabs {scene_id} failed: {last_err}")

    pcm48 = pcm48 - float(np.mean(pcm48))
    rms = float(np.sqrt(np.mean(pcm48**2)))
    if rms > 1e-4:
        pcm48 = pcm48 * (0.20 / rms)
    pcm48 = np.tanh(pcm48 * 1.05) * 0.92
    i16 = np.clip(pcm48 * 32767.0, -32768, 32767).astype("<i2")

    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(i16.tobytes())
    return wav_path


def make_corporate_underscore(total_sec: float, transition_times: list[float]) -> np.ndarray:
    """Synthesize warm, subtle, wide stereo studio corporate pad + soft chime cues @ 48 kHz."""
    n = int(round(total_sec * SR))
    t = np.arange(n, dtype=np.float32) / SR
    # Warm 4-bar chord progression (Cadd9 -> G/B -> Am7 -> Fmaj7) cycling gently every 16 seconds
    chords = [
        (130.81, 196.00, 261.63, 293.66, 329.63),  # Cadd9
        (123.47, 196.00, 246.94, 293.66, 392.00),  # G/B
        (110.00, 164.81, 220.00, 261.63, 392.00),  # Am7
        (87.31, 174.61, 220.00, 261.63, 329.63),   # Fmaj7
    ]
    left = np.zeros(n, dtype=np.float32)
    right = np.zeros(n, dtype=np.float32)

    bar_len = 4.0
    fade_samples = int(0.45 * SR)
    total_bars = int(np.ceil(total_sec / bar_len))
    for b in range(total_bars):
        i0 = int(b * bar_len * SR)
        i1 = min(n, int((b + 1) * bar_len * SR))
        if i1 <= i0:
            break
        seg_t = t[i0:i1] - (b * bar_len)
        freqs = chords[b % len(chords)]
        pad_l = np.zeros(i1 - i0, dtype=np.float32)
        pad_r = np.zeros(i1 - i0, dtype=np.float32)
        for fi, f in enumerate(freqs):
            detune = 1.0 + (fi - 2) * 0.0011
            w = 1.0 / (fi + 1.4)
            pad_l += w * np.sin(2.0 * np.pi * f * seg_t)
            pad_r += w * np.sin(2.0 * np.pi * f * detune * seg_t + 0.35 * fi)
        # Soft gentle arpeggio pluck every 1.0s
        for beat in range(4):
            bt = beat * 1.0
            b_idx = int(bt * SR)
            if b_idx < i1 - i0:
                rem = i1 - i0 - b_idx
                dt = np.arange(rem, dtype=np.float32) / SR
                f_arp = freqs[1 + (beat % (len(freqs) - 1))] * 2.0
                env = np.exp(-dt * 3.8) * (1.0 - np.exp(-dt * 120.0))
                note = 0.22 * env * np.sin(2.0 * np.pi * f_arp * dt)
                if beat % 2 == 0:
                    pad_l[b_idx:] += note * 0.85
                    pad_r[b_idx:] += note * 0.55
                else:
                    pad_l[b_idx:] += note * 0.55
                    pad_r[b_idx:] += note * 0.85
        # Smooth cross-bar amplitude envelope
        env = np.ones(i1 - i0, dtype=np.float32)
        fs = min(fade_samples, (i1 - i0) // 3)
        if fs > 2:
            ramp = 0.5 * (1.0 - np.cos(np.linspace(0, np.pi, fs, dtype=np.float32)))
            env[:fs] *= ramp
            env[-fs:] *= ramp[::-1]
        left[i0:i1] += pad_l * env
        right[i0:i1] += pad_r * env

    # Warm lowpass @ 2.4 kHz so underscore sits cleanly behind Decha voiceover
    sos_lp = butter(3, 2400.0, btype="lowpass", fs=SR, output="sos")
    left = sosfiltfilt(sos_lp, left).astype(np.float32)
    right = sosfiltfilt(sos_lp, right).astype(np.float32)

    # Subtle bright studio transition chime at chapter changes
    for tt in transition_times:
        c0 = int(tt * SR)
        c_len = min(n - c0, int(0.9 * SR))
        if c0 >= 0 and c_len > 100:
            dt = np.arange(c_len, dtype=np.float32) / SR
            chime = (0.14 * np.sin(2 * np.pi * 880.0 * dt) + 0.10 * np.sin(2 * np.pi * 1318.5 * dt)) * np.exp(-dt * 6.5) * (1.0 - np.exp(-dt * 200.0))
            left[c0 : c0 + c_len] += chime * 0.8
            right[c0 : c0 + c_len] += chime * 0.8

    stereo = np.stack([left, right], axis=1)
    peak = float(np.max(np.abs(stereo))) + 1e-6
    return (stereo / peak).astype(np.float32)


def compute_subtitle_cues(subtitles: list[str], audio_dur: float) -> list[dict]:
    weights = [max(1, len(re.sub(r"\s+", "", s))) for s in subtitles]
    total_w = sum(weights)
    cur = 0.25
    usable = max(1.0, audio_dur - 0.5)
    cues = []
    for s, w in zip(subtitles, weights):
        dur = usable * (w / total_w)
        cues.append({"text": s, "start": round(cur, 3), "end": round(cur + dur, 3)})
        cur += dur
    if cues:
        cues[-1]["end"] = round(audio_dur, 3)
    return cues


def main() -> None:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    api_key = load_api_key()
    total_chars = sum(len(s["narration"]) for s in SCENES)
    print(f"Total narration chars: {total_chars}")

    scene_wavs: list[tuple[dict, np.ndarray, float]] = []
    with httpx.Client(
        base_url="https://api.elevenlabs.io",
        timeout=httpx.Timeout(connect=10.0, read=60.0, write=15.0, pool=10.0),
        headers={"xi-api-key": api_key, "Content-Type": "application/json"},
    ) as client:
        for sc in SCENES:
            wp = synthesize_decha_wav(client, sc["id"], sc["narration"])
            with wave.open(str(wp), "rb") as wf:
                raw = wf.readframes(wf.getnframes())
            arr = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
            dur = arr.size / SR
            scene_wavs.append((sc, arr, dur))
            print(f"  {sc['id']}: voice={dur:.2f}s ({len(sc['narration'])} chars) -> {wp.name}")

    # Pad each scene cleanly (0.80s lead + 1.25s tail) so viewers absorb motion graphics & UI comfortably (~4:20-4:45 total)
    lead_s = 0.80
    tail_s = 1.25
    timeline_scenes = []
    cursor_s = 0.0
    voice_segments = []
    transition_times = []

    for idx, (sc, voice_arr, voice_dur) in enumerate(scene_wavs):
        extra_tail = 1.6 if idx == len(scene_wavs) - 1 else tail_s
        scene_dur = round(lead_s + voice_dur + extra_tail, 3)
        start_s = round(cursor_s, 3)
        end_s = round(cursor_s + scene_dur, 3)
        if idx > 0:
            transition_times.append(start_s + 0.05)

        voice_segments.append((int(round((start_s + lead_s) * SR)), voice_arr))
        timeline_scenes.append(
            {
                "index": idx,
                "id": sc["id"],
                "tag": sc["tag"],
                "title": sc["title"],
                "layout": sc["layout"],
                "screenshot": sc.get("screenshot"),
                "secondaryScreenshot": sc.get("secondaryScreenshot"),
                "callout": sc.get("callout"),
                "showDemoBadge": bool(sc.get("showDemoBadge", False)),
                "startSec": start_s,
                "endSec": end_s,
                "durationSec": round(scene_dur, 3),
                "subtitles": compute_subtitle_cues(sc["subtitles"], scene_dur),
            }
        )
        cursor_s = end_s

    total_sec = round(cursor_s, 3)
    n_total = int(round(total_sec * SR))
    voice_track = np.zeros(n_total, dtype=np.float32)
    for off, arr in voice_segments:
        take = min(arr.size, n_total - off)
        if take > 0:
            voice_track[off : off + take] += arr[:take]

    # Envelope follower to duck corporate music gently under Decha narration
    from scipy.ndimage import maximum_filter1d, uniform_filter1d

    speech_env = maximum_filter1d(np.abs(voice_track), int(0.18 * SR))
    speech_env = uniform_filter1d(speech_env, int(0.35 * SR))
    duck = np.where(speech_env > 0.015, 0.055, 0.145).astype(np.float32)
    duck = uniform_filter1d(duck, int(0.45 * SR))

    # Fade music smoothly at end
    out_fade = min(int(3.0 * SR), n_total // 4)
    if out_fade > 10:
        duck[-out_fade:] *= np.linspace(1.0, 0.0, out_fade, dtype=np.float32)

    bgm = make_corporate_underscore(total_sec, transition_times)
    mix = np.zeros((n_total, 2), dtype=np.float32)
    mix[:, 0] = voice_track * 0.88 + bgm[:, 0] * duck
    mix[:, 1] = voice_track * 0.88 + bgm[:, 1] * duck

    peak = float(np.max(np.abs(mix)))
    if peak > 0.94:
        mix = mix * (0.94 / peak)

    out_wav = WORK_DIR / "master_audio.wav"
    i16 = np.clip(mix * 32767.0, -32768, 32767).astype("<i2")
    with wave.open(str(out_wav), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(i16.tobytes())

    tl_path = WORK_DIR / "timeline.json"
    tl_path.write_text(
        json.dumps(
            {
                "totalSec": total_sec,
                "fps": 30,
                "width": 3840,
                "height": 2160,
                "voiceName": "Decha (ElevenLabs)",
                "voiceId": DECHA_VOICE_ID,
                "totalChars": total_chars,
                "scenes": timeline_scenes,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"MASTER AUDIO READY: {out_wav} | duration={total_sec:.2f}s ({total_sec/60:.2f} min)")


if __name__ == "__main__":
    main()
