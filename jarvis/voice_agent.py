"""Local real-time Thai voice assistant (phone-call style secretary).

Browser (static/index.html: mic over WebRTC, SVG status ring)
    -> FastRTC (Silero VAD pause detection, barge-in)
    -> Typhoon Whisper Turbo (GPU, the words of a finished speech) + OpenThai 2.0 ASR Streaming (CPU, the live line)
    -> DeepSeek streaming + tools (web, docs, bcbrain MCP)
    -> spoken summary -> OmniVoice Thai (local, cloned voice) -> speaker;   details after [จอ] -> on-screen panel

Run: python voice_agent.py   then open http://127.0.0.1:7860
"""
import ast
import asyncio
import base64
import calendar
import contextvars
import datetime
import hashlib
import html
import hmac
import itertools
import json
import logging
import operator
import os
import queue
import re
import subprocess
import sys
import threading
import time
import warnings
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import asynccontextmanager
from decimal import ROUND_HALF_UP, Decimal
from logging.handlers import RotatingFileHandler
from types import SimpleNamespace

import aioice.ice
import httpx
import librosa
import numpy as np
import uvicorn
from ddgs import DDGS
import torch
from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastrtc import AdditionalOutputs, ReplyOnPause, SileroVadOptions, Stream, audio_to_float32, get_current_context
from faster_whisper import WhisperModel
from faster_whisper.vad import VadOptions, get_speech_timestamps
from fastrtc.utils import create_message
from pypdf import PdfReader
from pythainlp.util import num_to_thaiword
from scipy.ndimage import maximum_filter1d, uniform_filter1d
from scipy.signal import butter, resample_poly, sosfilt, sosfiltfilt

warnings.filterwarnings("ignore", category=SyntaxWarning, module=r"f5_tts_th.*")
from f5_tts_th.normalize import normalize_text  # noqa: E402  (digits and signs to Thai words: the one part of f5_tts_th still used)
from omnivoice import OmniVoice  # noqa: E402
from omnivoice.models.omnivoice import OmniVoiceGenerationConfig  # noqa: E402

from brain_mirror import BrainVaultMirror, LocalBrainClient  # noqa: E402
import config as C  # noqa: E402
import desktop_agent  # noqa: E402
from elevenlabs_tts import ElevenLabsTTS, SPEAKABLE_RE  # noqa: E402
import grounding  # noqa: E402
import google_tools  # noqa: E402
from openthai_asr import OpenThaiASR  # noqa: E402
from qa_cache import QACache  # noqa: E402

QA_CACHE = QACache(C.QA_CACHE_PATH, ttl_s=C.QA_CACHE_TTL_S, max_entries=C.QA_CACHE_MAX_ENTRIES)

# Thai log lines on a cp1252 console/pipe on Windows would raise UnicodeEncodeError.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
# A detached server's console is kept nowhere; the hourly Dev-on-the-Fly review reads this file instead.
os.makedirs(C.LOG_DIR, exist_ok=True)
_file_log = RotatingFileHandler(os.path.join(C.LOG_DIR, "voice_agent.log"), maxBytes=5_000_000, backupCount=5,
                                encoding="utf-8")
_file_log.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
logging.getLogger().addHandler(_file_log)
log = logging.getLogger("voice")
logging.getLogger("httpx").setLevel(logging.WARNING)

# Thai separates phrases with spaces, not punctuation; "." is not a boundary so "1,250.50" stays whole.
BOUNDARY = re.compile(r"\s")
SENTENCE_END = re.compile(r"(?:ครับ|ค่ะ|คะ|[!?])\s")  # where a spoken sentence ends: one chunk = one tune
SENTENCE_TAIL = re.compile(r"(?:ครับ|ค่ะ|คะ|[!?])\s*$")
DANGLING_HEAD = re.compile(r"^(?:แต่|แล้ว|พร้อม|และ|โดย|ส่วน|หรือ|ซึ่ง|จากนั้น|ต่อมา)")
# what TTS must not read: markdown marks, a table's |---| rule, a list's number, a link's target (ลุงจืด 2026-11-23:
# strip markdown and table marks before the voice if any slip through), and the Chinese/Japanese the boss slips in
JUNK = re.compile(r"<think>.*?</think>|\]\([^)\s]*\)|[*#_`~>|\[\]{}]|[-:=]{3,}|^\s*[-•]\s*|^\s*\d+[.)]\s+|[\u3000-\u303f\u3040-\u30ff\u4e00-\u9fff\uff00-\uffef]", re.S | re.M)
# ponytail: keyword route. Things that change daily: these words force a fresh web_search before the brain speaks.
# a customer's phone read back went to brain.bcsmethai.com as a search (role-play 2026-11-04): such numbers stay
# out of the BC brain query; local docs still get them. ponytail: names still go; skip the prefetch when needed
LONG_NUMBER = re.compile(rf"\d[\d -]{{6,}}\d|(?:(?:{'|'.join(grounding.DIGIT_WORDS)})\s*){{7,}}")  # phone, tax id, account no., in digits or read as words


def masked(text):  # customers' phone/tax/account numbers stay out of the server log
    return LONG_NUMBER.sub("###", text)


# 2026-11-08 06:06-06:09: asked for worked examples, the boss twice made a table without calc and every row was cut.
# ponytail: "ตัวอย่าง" also matches "มีตัวอย่างลูกค้าไหม": one wasted calc line, then the usual answer
EXAMPLE_ASK = re.compile(r"ตัวอย่าง|สมมติ|ตาราง\S{0,4}คำนวณ")
# log 2026-11-09: a plain "50 หาร 0.5 บวก 50" got no calc and ended on CUT_ALL. Checked on masked() words, so a
# phone's dashes are no sum. ponytail: "ลบ 3 รายการ" or a date 10/11 costs one wasted calc round
SUM_ASK = re.compile(r"\d\s*(?:บวก|ลบ|คูณ|หาร)|(?:บวก|ลบ|คูณ|หาร)\s*\d|\d\s*[-+*/x×]\s*\d")
# topics whose worked example genuinely computes numbers (costing, tax, offset, discount); non-math topics (warehouse/location,
# menu workflow, SQL) skip forced round-0 calc (~1.2 s faster) and pick tree/flow/case/SQL examples instead of fake sums
CALC_ASK = re.compile(r"คำนวณ|ต้นทุน|ถัวเฉลี่ย|เฉลี่ย|ไฟโฟ|fifo|(?<!ใบกำกับ)ภาษี|vat|หัก\s*ณ|ส่วนลด|ดอกเบี้ย|ค่าเสื่อม|กำไร|ขาดทุน|"
                      r"ยอดรวม|หักกลบ|มัดจำ|งวด|ผ่อน|ปันส่วน|ราคาขาย|เดบิต|เครดิต", re.I)
FAST_CHAT = re.compile(r"^(?:สวัสดี|หวัดดี|ฮัลโหล|ขอบคุณ(?:มาก)?|โอเค(?:ขอบคุณ(?:มาก)?)?|รับทราบ|เข้าใจแล้ว|ขอคิดดูก่อน|คิดดูก่อน|แค่นี้ก่อน|วางสายก่อน)"
                       r"(?:\s*(?:ครับผม|ครับ|ค่ะ|คะ|นะคะ|นะครับ|นะ))*[\s.!?…]*$")
LATEST_ASK = re.compile(r"ข่าว|ล่าสุด|ทอง|น้ำมัน|หุ้น|ค่าเงิน|อัตราแลกเปลี่ยน|ดอลลาร์|บิตคอยน์|คริปโต|อากาศ|ฝนตก|พยากรณ์|"
                        r"ผลบอล|ฟุตบอล|หวย|สลากกินแบ่ง|ราคา.{0,8}วันนี้|วันนี้.{0,8}ราคา")
CONFIRM = re.compile(r"ยืนยัน|กดเลย|ทำเลย|เปิดเลย|ตกลง|ได้เลย|โอเค|ใช่")  # the caller's yes to a held computer action
REFUSE = re.compile(r"ไม่|อย่า|ยกเลิก|หยุด")
# only then does the turn carry hh:mm; no bare ตอนนี้: "ตอนนี้ในกล้องเห็นอะไร" got the clock and answered that the
# camera showed a clock reading 08:54 (the frame was a red circle)
TIME_ASK = re.compile(r"กี่โมง|เวลา|นาที|ทันไหม|สายไหม|ชั่วโมง")
DATE_ASK = re.compile(r"วันที่|วันอะไร|วันไหน|เดือน|ปี|พรุ่งนี้|เมื่อวาน|มะรืน|สัปดาห์|อาทิตย์|นัด|ปฏิทิน|กำหนด")  # ...or the date
TH_DAYS = "จันทร์ อังคาร พุธ พฤหัสบดี ศุกร์ เสาร์ อาทิตย์".split()
TH_MONTHS = "มกราคม กุมภาพันธ์ มีนาคม เมษายน พฤษภาคม มิถุนายน กรกฎาคม สิงหาคม กันยายน ตุลาคม พฤศจิกายน ธันวาคม".split()


def load_stt():
    return OpenThaiASR(C.STT_MODEL_DIR, lang=C.STT_LANGUAGE, intra_threads=C.STT_THREADS)


_MULTI_STT = None
_MULTI_STT_LOCK = threading.Lock()


def load_final_stt():
    """Load Thai-specialized Typhoon Whisper Turbo CT2 on GPU (2.3% CER on Thai phone calls).
    Falls back to HF FINAL_STT_MODEL if the local CT2 directory is absent."""
    if not C.HAS_CUDA:
        return None
    if os.path.isdir(C.FINAL_STT_MODEL_DIR) and "typhoon" in C.FINAL_STT_MODEL.lower():
        return WhisperModel(C.FINAL_STT_MODEL_DIR, device="cuda", compute_type=C.FINAL_STT_COMPUTE_TYPE)
    try:
        return WhisperModel(C.FINAL_STT_MODEL, device="cuda", compute_type=C.FINAL_STT_COMPUTE_TYPE, local_files_only=True)
    except Exception:
        try:
            return WhisperModel(C.FINAL_STT_MODEL, device="cuda", compute_type=C.FINAL_STT_COMPUTE_TYPE)
        except Exception:
            log.warning("Fallback to local FINAL_STT_MODEL_DIR: %s", C.FINAL_STT_MODEL_DIR)
            return WhisperModel(C.FINAL_STT_MODEL_DIR, device="cuda", compute_type=C.FINAL_STT_COMPUTE_TYPE)


def get_multi_stt():
    """Lazy-load Multilingual Whisper Large-v3-Turbo on CPU (int8, 0 GPU VRAM) for non-Thai calls (en/zh/ja/ko/lo)."""
    global _MULTI_STT
    if _MULTI_STT is not None:
        return _MULTI_STT
    with _MULTI_STT_LOCK:
        if _MULTI_STT is not None:
            return _MULTI_STT
        try:
            log.info("Loading multilingual CPU STT (%s, int8) for non-Thai call...", C.MULTI_STT_MODEL)
            _MULTI_STT = WhisperModel(
                C.MULTI_STT_MODEL,
                device="cpu",
                compute_type="int8",
                cpu_threads=C.STT_THREADS,
                local_files_only=True,
            )
        except Exception:
            try:
                _MULTI_STT = WhisperModel(C.MULTI_STT_MODEL, device="cpu", compute_type="int8", cpu_threads=C.STT_THREADS)
            except Exception:
                log.warning("Multilingual CPU STT unavailable, falling back to GPU final_stt")
                return final_stt
        return _MULTI_STT


_HP_SOS_16K = butter(4, 80.0, btype="highpass", fs=16000, output="sos")


def _audio_dbfs(arr: np.ndarray) -> float:
    if arr is None or arr.size == 0:
        return -100.0
    rms = float(np.sqrt(np.mean(arr.astype(np.float32) ** 2)))
    return 20.0 * float(np.log10(max(rms, 1e-5)))


def to16k(audio):  # fastrtc frames -> 16 kHz float32 with 80 Hz high-pass (cuts AC hum/wind/rumble)
    sr, data = audio
    data = audio_to_float32(np.squeeze(data))
    if data.size == 0:
        return data
    if sr == 8000:
        out = resample_poly(data, 2, 1).astype(np.float32, copy=False)
    elif sr == 16000:
        out = data.astype(np.float32, copy=False)
    elif sr == 24000:
        out = resample_poly(data, 2, 3).astype(np.float32, copy=False)
    elif sr == 48000:
        out = resample_poly(data, 1, 3).astype(np.float32, copy=False)
    else:
        out = librosa.resample(data, orig_sr=sr, target_sr=16000).astype(np.float32, copy=False)
    if out.size >= 32:
        out = sosfilt(_HP_SOS_16K, out).astype(np.float32, copy=False)
    return out


def voiced(audio):
    """The speech only, 16 kHz, by transcribe_final()'s own VAD: empty = it would transcribe nothing. No padding (its
    0.4 s a side made a 0.5 s word 2 s long), so the length is the speech's own."""
    data = to16k(audio)
    spans = get_speech_timestamps(data, VadOptions(**C.STT_VAD_PARAMS, speech_pad_ms=0))
    return np.concatenate([data[:0], *(data[t["start"] : t["end"]] for t in spans)])


def stt_hears(audio):
    return voiced(audio).size > 0


STT_LOCK = threading.Lock()
LIVE = {}  # handler id -> {"t": monotonic, "fed": samples, "text": last partial, "busy": bool, "stream": OpenThaiStream}
LIVE_ERROR_LOG_GAP_S = 10.0  # live_words() runs every LIVE_STEP_S: a failing one logs once in this long


def transcribe(audio):
    return stt.transcribe(to16k(audio))


_DEFAULT_TRANSCRIBE = transcribe


CALL_LANG: dict[str, str | None] = {}  # webrtc_id -> ISO lang code ("th","en","zh","ja","ko","lo","my","vi","km") or None (auto)
CALL_CONTEXT: dict[str, str] = {}  # webrtc_id -> initial CRM/caller context injected without extra LLM turn
CALL_CALLER_META: dict[str, dict] = {}  # webrtc_id -> {"phone", "name", "company", "totalCalls", "lastTopic"}
LAST_WAIT_AT: dict[str, float] = {}  # webrtc_id -> monotonic ts of last wait filler (avoid repetitive cues)
PENDING_LANG_CONFIRM: dict[str, str] = {}  # webrtc_id -> detected other language awaiting caller confirmation
CALL_GREET_SENT_AT: dict[str, float] = {}  # webrtc_id -> monotonic ts when greeting/intro started (detect early barge-in)

CALLER_PROFILES_PATH = os.path.join(C.SESSIONS_DIR, "caller_profiles.json")
CALLER_PROFILES_LOCK = threading.Lock()
_CALLER_PROFILES_CACHE: dict[str, dict] | None = None
_TH_DIGITS = "ศูนย์ หนึ่ง สอง สาม สี่ ห้า หก เจ็ด แปด เก้า".split()
_NAME_PREFIX_RE = re.compile(r"^(?:ชื่อเล่น|ชื่อว่า|ชื่อ|เรียกว่า|ว่า|คือ|เป็น)\s*")
_NAME_SUFFIX_RE = re.compile(r"(?:ชื่อเล่น|ชื่อว่า|ชื่อ|ครับผม|นะครับ|นะคะ|ครับ|ค่ะ|คะ|คับ|ก็ได้|นะ|เอง)$")
_NAME_SPLIT_RE = re.compile(r"(?:ชื่อเล่น|ชื่อว่า|ชื่อ|เรียกว่า|ครับผม|นะครับ|นะคะ|ครับ|ค่ะ|คะ|คับ)")
_HONORIFIC_RE = re.compile(r"^(?:คุณ|พี่|ลุง|ป้า|น้า|อา|น้อง|อาจารย์|หมอ|เฮีย|เจ๊)")
_EXPLICIT_NAME_TH_RE = re.compile(
    r"(?:ผมชื่อว่า|ดิฉันชื่อว่า|หนูชื่อว่า|ฉันชื่อว่า|พี่ชื่อว่า|ผมชื่อ|ดิฉันชื่อ|หนูชื่อ|ฉันชื่อ|พี่ชื่อ|"
    r"เรียกผมว่า|เรียกพี่ว่า|เรียกหนูว่า|เรียกฉันว่า|(?<![ก-๙])(?:ชื่อว่า|เรียกว่า|ชื่อเล่นว่า|ชื่อเล่น|ชื่อ))\s*([ก-๙a-zA-Z]{2,20})"
)
_EXPLICIT_NAME_EN_RE = re.compile(r"(?:my name is|i am|i'm|call me)\s+([A-Za-z]{2,20})", re.I)
_ASKED_NAME_BOT_RE = re.compile(
    r"(?:ขอเรียกคุณชื่อ|ขอเรียกชื่อ|ขอทราบชื่อ|ชื่อเล่นว่าอะไร|ชื่ออะไรดี|ชื่ออะไรครับ|เรียกคุณว่าอะไร|may i have your name|what(?:'s| is) your name)",
    re.I,
)
_BAD_CALLER_NAMES = {
    "อะไร", "เล่น", "จริง", "โปรแกรม", "ระบบ", "บริษัท", "ร้าน", "ลูกค้า", "เมนู", "สินค้า",
    "ไม่", "ครับ", "ค่ะ", "คะ", "คับ", "ไม่สะดวก", "ไม่เป็นไร", "ไม่บอก", "ไม่ต้อง", "เบอร์",
    "ราคา", "ใช่", "ไม่ใช่", "โอเค", "ได้เลย", "ดี", "ครับผม", "นะครับ", "นะคะ", "เปลี่ยนเลย", "เรื่องเดิม",
    "และ", "แล้ว", "ก็", "นะ", "นี้", "นั้น", "นี่", "นั่น", "อันนี้", "อันนั้น", "คือ", "หรือ", "แต่", "เพราะ", "ถ้า",
    "กับ", "จาก", "เป็น", "อยู่", "ที่", "ใน", "มา", "ไป", "ได้", "เอา", "ดู", "ลอง", "ถาม", "พูด", "ฟัง", "ช่วย",
    "เอ่อ", "อ่า", "อ้า", "อืม", "อือ", "ฮึ", "หึ", "อ๋อ", "อ้อ", "จ้า", "จ้ะ", "เหรอ", "หรอ", "เลย", "มาก", "หน่อย", "ด้วย",
    "เดี๋ยว", "แป๊บ", "หยุด", "พอ", "เจ๊", "เฮีย", "พี่", "น้อง", "คุณ", "ผม", "ฉัน", "หนู", "ดิฉัน", "มัน", "เขา", "เรา",
    "ไหม", "มั้ย", "หรือ", "อะไร", "ยังไง", "เท่าไหร่", "กี่", "บ้าง", "รึเปล่า", "เปล่า", "เหรอครับ", "เหรอคะ",
    "สวัสดี", "สวัสดีครับ", "สวัสดีค่ะ", "หวัดดี", "ฮัลโหล", "ทักทาย", "ขอบคุณ",
    "calling", "asking", "looking", "trying", "interested", "using", "not", "here", "fine", "good", "okay",
    "hello", "hi", "yes", "no", "thanks", "thank", "and",
}


def _normalize_phone(raw: str | None) -> str:
    s = re.sub(r"[^\d+]", "", str(raw or "").strip())
    if s.startswith("+66"):
        return "0" + s[3:]
    if s.startswith("66") and len(s) >= 10:
        return "0" + s[2:]
    return s


def _format_phone_spoken(phone: str) -> tuple[str, str]:
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return "", ""
    if len(digits) == 10:
        dashed = f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    elif len(digits) == 9:
        dashed = f"{digits[:2]}-{digits[2:5]}-{digits[5:]}"
    else:
        dashed = digits
    spoken = " ".join(_TH_DIGITS[int(ch)] for ch in digits if ch.isdigit())
    return dashed, spoken


def _clean_caller_name(raw: str | None) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    for _ in range(3):
        ns = _NAME_SUFFIX_RE.sub("", _NAME_PREFIX_RE.sub("", s)).strip()
        if ns == s:
            break
        s = ns
    # Handle repeated speech without spaces like "แบงค์ชื่อแบงค์" -> take first clean token before "ชื่อ"
    parts = [p.strip() for p in _NAME_SPLIT_RE.split(s) if p.strip()]
    if parts:
        s = parts[0]
    return s[:80]


def _format_addressed_name(name: str) -> str:
    clean_n = _clean_caller_name(name)
    if not clean_n:
        return ""
    return clean_n if _HONORIFIC_RE.match(clean_n) else f"คุณ{clean_n}"


_GARBLED_NAME_RE = re.compile(
    r"(?:หรือ|แล้ว|แต่|เพราะ|ถ้า|ว่า|ยังไง|อย่างไร|หรือเปล่า|รึเปล่า|ได้ไหม|ได้มั้ย|มั้ย|อะไร|เท่าไหร่|กันได้|โยงกัน|"
    r"การ[ก-๙]{2,}|ความ[ก-๙]{2,}|เรื่อง|ระบบ|โปรแกรม|สต๊อก|สต็อก|สินค้า|ราคา|บัญชี|ภาษี|สาขา|คลัง|เมนู|รายงาน|เซิร์ฟเวอร์)"
)
_BARE_TONE_CUT_RE = re.compile(r"(?<![าำะัิีึืุูเแโใไ])[ก-ฮ][่-๋]$")


def _is_plausible_name(cand: str, explicit: bool = False) -> bool:
    if not cand or len(cand) < 2 or cand.lower() in _BAD_CALLER_NAMES:
        return False
    if _GARBLED_NAME_RE.search(cand):
        return False
    if not re.search(r"[เแโใไ][ก-ฮ]{1,2}[่-๋]$", cand) and _BARE_TONE_CUT_RE.search(cand):
        return False
    if not explicit and re.fullmatch(r"[ก-๙]+", cand) and len(cand) > 10:
        return False
    return True


def _extract_caller_name_py(text: str, prev_bot: str = "") -> tuple[str, bool]:
    s = (text or "").strip()
    if not s:
        return "", False
    if m := _EXPLICIT_NAME_TH_RE.search(s):
        cand = _clean_caller_name(m.group(1))
        if _is_plausible_name(cand, explicit=True):
            is_self_intro = bool(re.match(r"^(?:ผม|ดิฉัน|หนู|ฉัน|พี่|เรียก(?:ผม|พี่|หนู|ฉัน))", m.group(0)))
            return cand, is_self_intro
    if m := _EXPLICIT_NAME_EN_RE.search(s):
        cand = m.group(1).strip()
        if _is_plausible_name(cand, explicit=True):
            return cand, True
    if prev_bot and _ASKED_NAME_BOT_RE.search(prev_bot):
        if m := re.match(r"^(?:ผม|ดิฉัน|หนู|ฉัน|พี่|คุณ)?\s*([ก-๙a-zA-Z]{2,18})(?:\s*(?:ครับผม|นะครับ|นะคะ|ครับ|ค่ะ|คะ|คับ|ก็ได้))?$", s):
            cand = _clean_caller_name(m.group(1))
            if (
                _is_plausible_name(cand, explicit=False)
                and not re.match(r"^(?:ไม่|ขอ|อยาก|มี|ทำ|ราคา|เบอร์|และ|แล้ว|แต่|หรือ|ถ้า|คือ|เอ่อ|อัน|มัน|นี่|นั่น|คุณ|เจ๊)", cand)
            ):
                return cand, False
    return "", False


def _load_all_caller_profiles() -> dict[str, dict]:
    global _CALLER_PROFILES_CACHE
    if _CALLER_PROFILES_CACHE is not None:
        return _CALLER_PROFILES_CACHE
    try:
        if os.path.exists(CALLER_PROFILES_PATH):
            with open(CALLER_PROFILES_PATH, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                _CALLER_PROFILES_CACHE = data
                return _CALLER_PROFILES_CACHE
    except Exception:
        log.warning("caller_profiles load failed, starting fresh")
    _CALLER_PROFILES_CACHE = {}
    return _CALLER_PROFILES_CACHE


def _save_all_caller_profiles(profiles: dict[str, dict]) -> None:
    try:
        os.makedirs(C.SESSIONS_DIR, exist_ok=True)
        tmp = f"{CALLER_PROFILES_PATH}.{threading.get_ident()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(profiles, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CALLER_PROFILES_PATH)
    except OSError as e:
        log.warning("caller_profiles save failed: %s", e)


def _sync_caller_profile(phone: str, meta: dict) -> dict:
    norm_phone = _normalize_phone(phone)
    in_name = _clean_caller_name(meta.get("name"))
    if in_name and not _is_plausible_name(in_name, explicit=True):
        in_name = ""
    cleaned_in = {
        "phone": norm_phone,
        "name": in_name,
        "company": str(meta.get("company") or "").strip()[:120],
        "totalCalls": int(meta.get("totalCalls") or 0),
        "lastTopic": str(meta.get("lastTopic") or "").strip()[:80],
    }
    if not norm_phone:
        return cleaned_in
    with CALLER_PROFILES_LOCK:
        profiles = _load_all_caller_profiles()
        existing = profiles.get(norm_phone) if isinstance(profiles.get(norm_phone), dict) else {}
        existing_name = _clean_caller_name(existing.get("name"))
        if existing_name and not _is_plausible_name(existing_name, explicit=True):
            existing_name = ""
        merged = {
            "phone": norm_phone,
            "name": cleaned_in["name"] or existing_name,
            "company": cleaned_in["company"] or str(existing.get("company") or "").strip()[:120],
            "totalCalls": max(cleaned_in["totalCalls"], int(existing.get("totalCalls") or 0)),
            "lastTopic": cleaned_in["lastTopic"] or str(existing.get("lastTopic") or "").strip()[:80],
            "updatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        }
        profiles[norm_phone] = merged
        _save_all_caller_profiles(profiles)
        return dict(merged)
LANG_NAMES = {
    "th": "ภาษาไทย (Thai)",
    "en": "ภาษาอังกฤษ (English)",
    "zh": "ภาษาจีน (Chinese)",
    "ja": "ภาษาญี่ปุ่น (Japanese)",
    "ko": "ภาษาเกาหลี (Korean)",
    "lo": "ภาษาลาว (Lao)",
    "my": "ภาษาเมียนมา (Burmese)",
    "vi": "ภาษาเวียดนาม (Vietnamese)",
    "km": "ภาษากัมพูชา (Khmer)",
}
LANG_CONFIRM_PROMPTS = {
    "th": "ได้ยินว่าเป็นภาษาไทย ต้องการเปลี่ยนกลับมาคุยเป็นภาษาไทยไหมครับ",
    "en": "I heard you speaking English. Would you like to switch our conversation to English?",
    "zh": "我听到您说中文，请问要切换成中文继续沟通吗？",
    "ja": "日本語でお話しされていますね。日本語に切り替えてよろしいですか？",
    "ko": "한국어로 말씀하셨네요. 한국어로 대화를 변경할까요?",
    "lo": "ຂ້ອຍໄດ້ຍິນທ່ານເວົ້າພາສາລາວ ຕ້ອງການປ່ຽນເປັນພາສາລາວເລີຍບໍ",
    "my": "မြန်မာဘာသာဖြင့် ပြောဆိုရန် ပြောင်းလိုပါသလား",
    "vi": "Bạn có muốn chuyển cuộc trò chuyện sang tiếng Việt không?",
    "km": "តើអ្នកចង់ប្តូរការសន្ទនាទៅជាភាសាខ្មែរទេ?",
}
LANG_SWITCHED_ACK = {
    "th": "ได้เลยครับ เปลี่ยนเป็นภาษาไทยแล้ว มีอะไรให้ผมช่วยดูแลครับ",
    "en": "Sure, I have switched to English. How can I help you today?",
    "zh": "好的，已为您切换为中文。请问有什么可以帮您？",
    "ja": "かしこまりました、日本語に切り替えました。どのようなご用件でしょうか？",
    "ko": "네, 한국어로 변경했습니다. 무엇을 도와드릴까요?",
    "lo": "ໄດ້ເລີຍ ປ່ຽນເປັນພາສາລາວແລ້ວ ມີຫຍັງໃຫ້ຊ່ວຍບໍ",
    "my": "ဟုတ်ကဲ့ မြန်မာဘာသာသို့ ပြောင်းလဲပြီးပါပြီ။ ဘာကူညီပေးရမလဲ။",
    "vi": "Vâng, tôi đã chuyển sang tiếng Việt. Tôi có thể giúp gì cho bạn?",
    "km": "បាទ ខ្ញុំបានប្តូរទៅភាសាខ្មែរហើយ តើខ្ញុំអាចជួយអ្វីបាន?",
}
MULTILANG_FAST_CHAT = re.compile(
    r"^(hi|hello|hey|good\s*(morning|afternoon|evening)|how\s*are\s*you|what\s*(do\s*you\s*do|can\s*you\s*do|is\s*your\s*name)|who\s*are\s*you|can\s*you\s*hear\s*me|thank\s*you|thanks|ok|okay|yes|no|sure|bye|ได้ยินไหม|ได้ยินมั้ย|สวัสดีได้ยินไหม|ฮัลโหล|ทดสอบ|เทส|สบายดีไหม|คุณคือใคร|ทำอะไรได้บ้าง|ขอบคุณครับ|ขอบคุณค่ะ|你好|听得到吗|谢谢|再见|こんにちは|ありがとう|안녕하세요)[\s!?.…]*$",
    re.I,
)


CORE_MULTI_LANGS = {"th", "lo", "en", "zh", "ja", "ko"}
CALL_LAST_STT_LANG: dict[str, tuple[str | None, float]] = {}  # webrtc_id -> Whisper's (language, probability) for the turn being answered


def _detect_explicit_lang_switch(text: str) -> str | None:
    low = (text or "").strip().lower()
    if not low:
        return None
    # Explicit English requests (including "can't understand thai" / "don't speak thai")
    if re.search(r"(ภาษาอังกฤษ|พูดอังกฤษ|คุยอังกฤษ|speak\s+(?:in\s+)?english|in english|switch to english|english please|can'?t understand thai|don'?t (?:speak|understand) thai)", low):
        return "en"
    if re.search(r"(ภาษาจีน|พูดจีน|คุยจีน|speak\s+(?:in\s+)?chinese|in chinese|mandarin|说中文|讲中文|切换.*中文|用中文|普通话)", low):
        return "zh"
    if re.search(r"(ภาษาญี่ปุ่น|พูดญี่ปุ่น|คุยญี่ปุ่น|speak\s+(?:in\s+)?japanese|in japanese|日本語で|にほんご)", low):
        return "ja"
    if re.search(r"(ภาษาเกาหลี|พูดเกาหลี|คุยเกาหลี|speak\s+(?:in\s+)?korean|in korean|한국어로|한국말)", low):
        return "ko"
    if re.search(r"(ภาษาลาว|พูดลาว|คุยลาว|เว้าลาว|เว้าภาษาลาว|บาสาลาว|บาสาดาว|speak\s+(?:in\s+)?lao|in lao|ພາສາລາວ|ເວົ້າລາວ|ສະບາຍດີ)", low):
        return "lo"
    if re.search(r"(ภาษาพม่า|ภาษาเมียนมา|speak burmese|myanmar language|မြန်မာ)", low):
        return "my"
    if re.search(r"(ภาษาเวียดนาม|speak vietnamese|tiếng việt)", low):
        return "vi"
    if re.search(r"(ภาษาเขมร|ภาษากัมพูชา|speak khmer|ភាសាខ្មែរ)", low):
        return "km"
    # Match Thai switch only if NOT negated ("can't understand thai" already handled above)
    if re.search(r"(ภาษาไทย|พูดไทย|คุยไทย|speak\s+(?:in\s+)?thai|in thai|switch to thai)", low) and not re.search(r"(?:can'?t|don'?t|not)\s+\w*\s*thai", low):
        return "th"
    return None


def _detect_script_lang(text: str) -> str | None:
    s = (text or "").strip()
    if not s:
        return None
    # Count characters by script to avoid false matches on mixed technical terms (e.g. "BC Ai Account")
    n_th = len(re.findall(r"[\u0E00-\u0E7F]", s))
    n_lo = len(re.findall(r"[\u0E80-\u0EFF]", s))
    n_ja = len(re.findall(r"[\u3040-\u30FF]", s))
    n_ko = len(re.findall(r"[\uAC00-\uD7AF]", s))
    n_zh = len(re.findall(r"[\u4E00-\u9FFF]", s))
    n_my = len(re.findall(r"[\u1000-\u109F]", s))
    n_km = len(re.findall(r"[\u1780-\u17FF]", s))
    latin_words = re.findall(r"[a-zA-Z]{2,}", s)

    if n_ja >= 2:
        return "ja"
    if n_ko >= 2:
        return "ko"
    if n_zh >= 2 and n_ja == 0:
        return "zh"
    if n_lo >= 2 and n_lo >= n_th:
        return "lo"
    if n_my >= 2:
        return "my"
    if n_km >= 2:
        return "km"
    if n_th >= 2 and n_th >= len(latin_words) * 2:
        return "th"
    if len(latin_words) >= 2 and n_th == 0:
        return "en"
    return None


LANG_SURE_PROB = 0.80  # Thai misheard as a whole English sentence reached 0.68 ("He wore it out.", 2026-11-23)


def _clearly_spoken_lang(text: str, stt_lang: str | None, stt_prob: float) -> str | None:
    """The language this turn was clearly spoken in, else None. Thai speech on a phone line is often heard as a short
    foreign phrase (2026-11-23: "Okay." en 91%, four Chinese characters zh 90%, a Vietnamese sentence 59%), so a turn
    counts only when Whisper is sure, the script agrees with it and the phrase is long enough."""
    lang = _detect_script_lang(text)
    if lang not in CORE_MULTI_LANGS or stt_lang != lang or stt_prob < LANG_SURE_PROB:
        return None
    if lang == "en":
        long_enough = len(re.findall(r"[A-Za-z]{2,}", text)) >= 3
    else:  # characters, not words: these scripts are written without spaces
        long_enough = len(re.sub(r"\s+", "", text)) >= 6
    return lang if long_enough else None


_LANG_YES_START = re.compile(r"(?:yes|yeah|yep|sure|ok|okay)(?![a-z])|ใช่(?!ไหม|มั้ย|หรือ|เหรอ)|ตกลง|โอเค|好(?![吗嗎])|是的|对(?!不)|可以|はい|네|예|ເຈົ້າ", re.I)
# ครับ/ได้/ดี open most Thai sentences ("ได้ยินไหม"): they are a yes only as the whole reply ("ได้ครับ")
_LANG_YES_WHOLE = re.compile(r"(?:ครับผม|ครับ|ค่ะ|ได้|ดี|เอา|เปลี่ยน|please|switch|ໄດ້)(?:ครับผม|ครับ|ค่ะ|คะ|เลย|นะ|ได้|please|switch)*", re.I)
# "ไม่ทราบว่า..." only opens a polite question
_LANG_NO_START = re.compile(r"(?:no|nope|nah|not|don'?t|cancel)(?![a-z])|ไม่(?!ทราบ)|อย่า|不(?:用|要|是|行|必)|不[\s,.!，。！]*$|いいえ|아니|ບໍ່", re.I)


def _is_lang_confirm_yes(text: str) -> bool:
    said = (text or "").strip()
    return bool(_LANG_YES_START.match(said) or _LANG_YES_WHOLE.fullmatch(re.sub(r"[\s,.!?…]+", "", said)))


def _is_lang_confirm_no(text: str) -> bool:
    return bool(_LANG_NO_START.match((text or "").strip()))


def _lang_turn(heard: str, cur_lang: str | None, pending: str | None, stt: tuple[str | None, float]) -> tuple[str, str | None]:
    """What one caller turn means for the call's language. The language changes only on the caller's yes to the
    confirm question ("switch"); another language clearly spoken or asked for raises that question ("ask"), and weak
    evidence changes nothing ("keep"). `pending` is the language asked about on the turn before: this turn answers it
    or drops it. "adopt" is the first clear language of a call the operator set to auto."""
    explicit = _detect_explicit_lang_switch(heard)
    spoken = _clearly_spoken_lang(heard, *stt)
    declined = bool(pending) and _is_lang_confirm_no(heard)
    if pending and not declined and (_is_lang_confirm_yes(heard) or pending in (explicit, spoken)):
        return "switch", pending
    if not cur_lang and spoken:
        return "adopt", spoken
    wanted = explicit or spoken
    if wanted and wanted != cur_lang and not (declined and wanted == pending):
        return "ask", wanted
    return ("declined", pending) if declined else ("keep", None)


EAR1_GPU_POOL = ThreadPoolExecutor(2)
EAR23_CPU_POOL = ThreadPoolExecutor(4)
TRIPLE_EAR_POOL = EAR23_CPU_POOL  # backward-compatible alias
_EAR1_GPU_SEM = threading.Semaphore(1)  # CTranslate2 runs 1 GPU job at a time: skip queuing if busy to prevent domino timeouts
LAST_ALT_HEARD: dict[str, list[str]] = {}  # webrtc_id -> distinct alternate transcripts from secondary ears
BC_STT_PROMPT = "BC Ai Account, บีซีเอไอแอคเคาท์, รายงาน, ใบสั่งซื้อ, ใบสั่งจอง, ใบรับสินค้า, ต้นทุนสินค้า, FIFO, FEFO, VAT, แพ็กเกจ"
_STT_STUTTER_3_RE = re.compile(r"([\u0E00-\u0E7F]{3,})\1")
_STT_STUTTER_2X3_RE = re.compile(r"([\u0E00-\u0E7F]{2,})\1\1")


def _has_stt_stutter(text: str) -> bool:
    """Detect Whisper syllable/phrase repetition loops without spaces (e.g. 'บาทบาทบาท', 'เท่าไหร่าไหร่')."""
    s = (text or "").strip()
    return bool(s and (_STT_STUTTER_3_RE.search(s) or _STT_STUTTER_2X3_RE.search(s)))


def _stt_stutter_guard_helper_anchor():  # kept adjacent to _choose_triple_ear
    pass


_STT_DOMAIN_FIX_RE = re.compile(
    r"(?:เวอร์ชัน|เวอร์ชั่น|เบอร์|แพ็กเกจ|สต็อก|สต๊อก|บัญชี|ภาษี|รายงาน|ใบกำกับ|ใบเสนอราคา|ใบเสร็จ|ลูกหนี้|เจ้าหนี้|ต้นทุน|คลาวด์|ออฟฟิศ|ไลน์|เว็บไซต์)"
)


def _prefer_openthai_domain(ear1: str, ear2: str, ear3: str) -> bool:
    """True when both OpenThai ears (e2 & e3) agree on a canonical BC domain word missing from Whisper e1."""
    e1, e2, e3 = (ear1 or "").strip(), (ear2 or "").strip(), (ear3 or "").strip()
    if not e2 or not e3 or _has_stt_stutter(e2):
        return False
    shared = set(_STT_DOMAIN_FIX_RE.findall(e2)) & set(_STT_DOMAIN_FIX_RE.findall(e3))
    if not shared:
        return False
    m1 = set(_STT_DOMAIN_FIX_RE.findall(e1))
    return bool(shared - m1)


def _choose_triple_ear(ear1: str, ear2: str, ear3: str, lang: str | None) -> tuple[str, list[str]]:
    """Select the primary transcript from Triple-Ear ASR (ear1=Whisper GPU, ear2=OpenThai th-TH, ear3=OpenThai th-mix)
    and collect distinct alternate hearings to pass to DeepSeek at 0ms extra latency."""
    cands = [t.strip() for t in (ear1, ear2, ear3)]
    e1, e2, e3 = cands
    if lang and lang != "th":
        picked = e1 or e3 or e2
    elif e3 and (_detect_explicit_lang_switch(e3) or re.search(r"[A-Za-z]{2,}", e3)):
        picked = e3  # th-mix kept the English words or the language request that th-TH spells out in Thai
    else:
        # Thai call: the Thai-only ears lead and Whisper rides along as an alternate. On phone audio Whisper is the
        # weakest ear for Thai (2026-11-23, 24 turns heard by both): they differed 18 times, the Thai ears had the
        # sensible text in 15 and Whisper in none, at 92-97% confidence ("มีผ่านมันทีตัวอย่างไม่" for "มีแผนบัญชีตัวอย่างไหม")
        picked = e2 or e3 or e1
    alts = []
    norm_pick = re.sub(r"\s+", "", picked).lower()
    for t in (e1, e2, e3):
        if t and re.sub(r"\s+", "", t).lower() != norm_pick and t not in alts:
            alts.append(t)
    return picked, alts


def transcribe_final(audio):
    """Multilingual Triple-Ear Parallel ASR (th / lo / en / zh / ja / ko):
    - Ear 1 (GPU CUDA): Multilingual Whisper Large-v3-Turbo with true auto language detection (never forces foreign speech into Thai!)
    - Ear 2 (CPU ONNX th-TH) + Ear 3 (CPU ONNX th-mix): Batched B=2 OpenThai 2.0 ASR (~180ms) used only when speech is Thai."""
    data = to16k(audio)
    if data.size < 400:
        return ""
    cid = call_id() if "call_id" in globals() else ""
    req_lang = CALL_LANG.get(cid) if cid in CALL_LANG else "th"
    phone_mode = "on_phone" in globals() and on_phone()
    t_stt = time.perf_counter()

    def _run_ear1():
        use_multi = bool(req_lang and req_lang != "th")
        model = get_multi_stt() if use_multi else final_stt
        if model is None:
            return "", None, 0.0
        if not use_multi:
            # CTranslate2 executes one GPU job at a time: if a previous timed-out job still holds the GPU,
            # fail fast instead of queueing behind it and causing a chain of 1.6s+ domino timeouts.
            if not _EAR1_GPU_SEM.acquire(timeout=0.35):
                log.info("TRIPLE-EAR: Ear1 GPU busy, using fast CPU Ear2/3")
                return "", None, 0.0
            try:
                # Thai call -> Typhoon Whisper Turbo locked to Thai + concise domain anchor prompt
                segments, info = model.transcribe(
                    data,
                    language="th",
                    beam_size=2,
                    initial_prompt=BC_STT_PROMPT,
                    repetition_penalty=1.15,
                    no_repeat_ngram_size=3,
                    vad_filter=True,
                    vad_parameters=C.STT_VAD_PARAMS,
                    without_timestamps=True,
                    condition_on_previous_text=False,
                )
                txt = "".join(s.text for s in segments).strip()
                return txt, "th", float(getattr(info, "language_probability", 0.95) or 0.95)
            except Exception:
                log.exception("Ear1 (Typhoon GPU) failed")
                return "", None, 0.0
            finally:
                _EAR1_GPU_SEM.release()

        try:
            # Non-Thai call (en/zh/ja/ko/lo) -> Multilingual Whisper Large-v3-Turbo on CPU (int8) locked to active call language
            segments, info = model.transcribe(
                data,
                language=req_lang if req_lang in LANG_NAMES else None,
                beam_size=1,
                initial_prompt=None,
                repetition_penalty=1.1,
                no_repeat_ngram_size=3,
                vad_filter=True,
                vad_parameters=C.STT_VAD_PARAMS,
                without_timestamps=True,
                condition_on_previous_text=False,
            )
            txt = "".join(s.text for s in segments).strip()
            det_lang = req_lang if req_lang in LANG_NAMES else getattr(info, "language", None)
            det_prob = float(getattr(info, "language_probability", 0.90) or 0.90)
            return txt, det_lang, det_prob
        except Exception:
            log.exception("Ear1 (Multilingual CPU) failed")
            return "", None, 0.0

    def _run_ears23():
        try:
            if hasattr(stt, "transcribe_dual_prompts"):
                return stt.transcribe_dual_prompts(data)
            t = stt.transcribe(data).strip()
            return t, ""
        except Exception:
            log.exception("Ear2/3 (OpenThai B=2) failed")
            return "", ""

    # Always run Ear 1 (GPU Typhoon / CPU Multilingual Whisper) and Ear 2/3 (CPU OpenThai th-TH + th-mix) IN PARALLEL
    # on separate pools so a slow GPU job can never starve CPU OpenThai.
    f_gpu = EAR1_GPU_POOL.submit(_run_ear1)
    f_cpu = EAR23_CPU_POOL.submit(_run_ears23)

    ear2, ear3 = f_cpu.result()
    ear1, det_lang, det_prob = "", None, 0.0
    use_multi_call = bool(req_lang and req_lang != "th")
    deadline = (3.5 if use_multi_call else 2.2) if phone_mode else 4.0
    rem = max(0.05, deadline - (time.perf_counter() - t_stt))
    try:
        ear1, det_lang, det_prob = f_gpu.result(timeout=rem)
    except Exception:
        log.info("TRIPLE-EAR: Ear1 >%.1fs deadline, using fast CPU Ear2/3", deadline)

    # In a Thai call (GPU Typhoon locked to 'th'), if OpenThai th-mix (ear3) clearly heard a pure English sentence
    # (>= 3 English words, zero Thai chars), promote English detection so the caller can still switch to English naturally!
    if (not use_multi_call) and ear3 and _detect_script_lang(ear3) == "en" and len(re.findall(r"[A-Za-z]{2,}", ear3)) >= 3:
        det_lang, det_prob = "en", 0.90

    if cid:
        CALL_LAST_STT_LANG[cid] = (det_lang, det_prob)  # every turn, also when Ear 1 gave no verdict: reply() must not judge this turn by the last one's

    # Check if CPU OpenThai caught an explicit Thai/Lao language-switch request (e.g. "ช่วยพูดภาษาเกาหลี", "ภาษาลาว")
    # that Whisper missed or misheard as a foreign word / hallucination ("Thank you." / "吹鋪得大夠力")
    cpu_switch_text = ear3 if _detect_explicit_lang_switch(ear3) else (ear2 if _detect_explicit_lang_switch(ear2) else "")
    if cpu_switch_text and not _detect_explicit_lang_switch(ear1):
        # Prefer OpenThai explicit language switch when Whisper didn't detect one (or low confidence)
        if det_prob < 0.85 or _detect_explicit_lang_switch(cpu_switch_text) != req_lang:
            if cid:
                LAST_ALT_HEARD[cid] = [ear1] if ear1 else []
            dt = time.perf_counter() - t_stt
            log.info(
                "TRIPLE-EAR-SWITCH (%.2fs | req=%s det=%s %.0f%%) picked=%s | e1=%s",
                dt, req_lang or "-", det_lang or "-", det_prob * 100, masked(cpu_switch_text), masked(ear1 or "-"),
            )
            return cpu_switch_text

    # The caller is answering "switch back to Thai?": Whisper hears a one-word Thai reply as foreign ("Bye."),
    # so a yes/no heard by the Thai ears is the answer
    if cid and PENDING_LANG_CONFIRM.get(cid) == "th":
        thai_answer = next((t for t in (ear2, ear3) if _is_lang_confirm_yes(t) or _is_lang_confirm_no(t)), "")
        if thai_answer:
            LAST_ALT_HEARD[cid] = [ear1] if ear1 else []
            log.info(
                "TRIPLE-EAR-ANSWER (%.2fs | req=%s det=%s %.0f%%) picked=%s | e1=%s",
                time.perf_counter() - t_stt, req_lang or "-", det_lang or "-", det_prob * 100, masked(thai_answer), masked(ear1 or "-"),
            )
            return thai_answer

    # When in a non-Thai call and Whisper confidently heard non-Thai (or current language), trust Ear 1 100%
    script_e1 = _detect_script_lang(ear1)
    is_non_thai = bool(
        ear1
        and (
            (det_lang in (CORE_MULTI_LANGS - {"th"}) and det_prob >= 0.45)
            or (script_e1 in (CORE_MULTI_LANGS - {"th"}) and det_prob >= 0.40)
        )
    )
    if is_non_thai and req_lang == "th" and cid not in PENDING_LANG_CONFIRM:
        # Thai call with no language question waiting for its answer: a short or unsure foreign guess is Thai misheard
        # ("Okay." en 91%), so Ear 1 leads only on what reply() itself would raise the language question for
        is_non_thai = bool(_clearly_spoken_lang(ear1, det_lang, det_prob) or _detect_explicit_lang_switch(ear1))
    if is_non_thai:
        picked = ear1
        if cid:
            LAST_ALT_HEARD[cid] = []
        dt = time.perf_counter() - t_stt
        log.info(
            "TRIPLE-EAR-MULTI (%.2fs | req=%s det=%s %.0f%% script=%s) picked=%s",
            dt, req_lang or "-", det_lang or "-", det_prob * 100, script_e1 or "-", masked(picked),
        )
        return picked

    picked, alts = _choose_triple_ear(ear1, ear2, ear3, req_lang)
    # When Ear 1 is Thai-specialized Typhoon Whisper Turbo (2.3% CER vs OpenThai 17.6% CER) on a Thai call,
    # let Typhoon lead unless OpenThai th-mix caught an explicit language-switch / pure English sentence,
    # OR Typhoon fell into a syllable-repetition loop ("บาทบาทบาท", "เท่าไหร่าไหร่") that OpenThai heard cleanly.
    if (
        (not use_multi_call)
        and "typhoon" in C.FINAL_STT_MODEL.lower()
        and ear1
        and not (ear3 and (_detect_explicit_lang_switch(ear3) or _detect_script_lang(ear3) == "en"))
    ):
        e2_clean = (ear2 or "").strip()
        e1_garbled = (
            _has_stt_stutter(ear1)
            or (len(e2_clean) >= 8 and len(ear1.strip()) >= len(e2_clean) * 2 and not _has_stt_stutter(e2_clean))
        )
        if (e1_garbled and len(e2_clean) >= 4 and not _has_stt_stutter(e2_clean)) or _prefer_openthai_domain(ear1, ear2, ear3):
            picked = ear2
            alt_sources = (ear1, ear3)
        else:
            picked = ear1
            alt_sources = (ear2, ear3)
        norm_p = re.sub(r"\s+", "", picked).lower()
        alts = []
        for t in alt_sources:
            if t and re.sub(r"\s+", "", t).lower() != norm_p and t not in alts:
                alts.append(t)
    if cid:
        LAST_ALT_HEARD[cid] = alts
    dt = time.perf_counter() - t_stt
    if picked:
        log.info(
            "TRIPLE-EAR (%.2fs | req=%s det=%s %.0f%%) picked=%s | e1=%s | e2=%s | e3=%s",
            dt, req_lang or "-", det_lang or "-", det_prob * 100, masked(picked), masked(ear1 or "-"), masked(ear2 or "-"), masked(ear3 or "-"),
        )
    return picked


EARLY = {}  # handler -> (the speech so far, its transcription job), started in the silence before a turn ends
EARLY_STT = ThreadPoolExecutor(2)  # ponytail: CTranslate2 runs one GPU job at a time anyway
LIVE_STT_POOL = ThreadPoolExecutor(2)  # dedicated pool for OpenThai live_words so barge-in never queues behind early STT


def early_words(data):
    """What determine_pause already transcribed of this very speech (the caller said nothing after it), else None."""
    flat = data.reshape(-1)
    for key, (head, job) in list(EARLY.items()):
        if head.size <= flat.size and np.array_equal(head, flat[: head.size]) and EARLY.pop(key, None):
            return job.result()
    return None


NUM_WORDS = [*"0123456789", *grounding.DIGIT_WORDS, *grounding.UNIT]
NUM_END = re.compile(rf"(?:{'|'.join(NUM_WORDS)})[\s,.\-]*$")


def inside_number(before, after):
    """A phone number read back in groups ("ศูนย์แปดหนึ่ง หนึ่งสองสาม ...") cut at its spaces became three
    numbers no source had, and the number guard dropped them all. What follows may be only the start of a word
    (DeepSeek streams "ส" then "ี่"), so a number word it could still become counts too."""
    rest = after.lstrip()
    return bool(NUM_END.search(before)) and any(w.startswith(rest[: len(w)]) for w in NUM_WORDS)


def chunk_sentences(tokens, min_chars=20, max_chars=140):
    """Group streamed tokens into speakable chunks: cut at the last sentence end (ครับ, ค่ะ) once min_chars are in,
    or at the last space once max_chars are, never inside a number."""
    # ลุงจืด 2026-11-08 "คุยเป็นธรรมชาติ มนุษย์ มากที่สุด": a cut at any space after 20 characters spoke
    # "เรื่องนี้ต้องบอกตรงๆ / ครับว่าเลือกประเภทต้นทุนเป็น / LOT FIFO ได้", each piece with its own falling tune
    buf = ""
    whole = lambda ms: [x for x in ms if not inside_number(buf[: x.end()], buf[x.end() :])]
    for tok in tokens:
        buf += tok
        if len(buf) >= min_chars and (m := whole(SENTENCE_END.finditer(buf)) or (len(buf) >= max_chars and whole(BOUNDARY.finditer(buf)))):
            cut = m[-1].end()
            if buf[:cut].strip():
                yield buf[:cut]
                buf = buf[cut:]
    if buf.strip():
        yield buf


def until_screen(tokens, screen):
    """Pass spoken tokens through; stop at SCREEN_MARK and put what follows it into `screen`.
    Stopping (not just filtering) lets chunk_sentences flush the last spoken phrase right away."""
    buf, mark = "", C.SCREEN_MARK
    for tok in tokens:
        buf += tok
        if mark in buf:
            speech, rest = buf.split(mark, 1)
            screen.append(rest)
            yield speech
            return
        keep = next((k for k in range(len(mark) - 1, 0, -1) if buf.endswith(mark[:k])), 0)  # maybe a split marker
        yield buf[: len(buf) - keep]
        buf = buf[len(buf) - keep:]
    yield buf


def clean(text):
    out = re.sub(r"\s+", " ", JUNK.sub("", text)).strip()
    return out if SPEAKABLE_RE.search(out) else ""


def thai_day(d):
    """The day in digits and in the words to say. Log 2026-11-15 11:26 and a replay 2026-11-17: the boss turned 2569
    into สองพันหกร้อยหกสิบเก้า by itself and the number guard cut the whole date; now it has the words to copy."""
    head, month = f"วัน{TH_DAYS[d.weekday()]}ที่", TH_MONTHS[d.month - 1]
    return f"{head} {d.day} {month} {d.year + 543} (อ่านว่า {head}{num_to_thaiword(d.day)} {month} {num_to_thaiword(d.year + 543)})"


def thai_now(clock=True):
    n = datetime.datetime.now()
    return f"{thai_day(n)} เวลา {n:%H:%M} น." if clock else thai_day(n)


def add_months(d, n):
    y, m = divmod(d.year * 12 + d.month - 1 + n, 12)
    return d.replace(year=y, month=m + 1, day=min(d.day, calendar.monthrange(y, m + 1)[1]))


# ลุงจืด's call 2026-11-17 04:43 "อีก10วันวันที่เท่าไหร่", twice: the boss counted the right day itself and the number guard
# cut it, no source held 27. The day the caller's words point at is counted here, so it is a source.
# ponytail: digits only ("อีกสิบวัน" gets today's date and no sum); a month or year step keeps the day number, capped
# at the month's end; a weekday name ("วันศุกร์หน้า") is not counted
DAY_WORD = {"เมื่อวานซืน": -2, "เมื่อวาน": -1, "พรุ่งนี้": 1, "มะรืน": 2}
DATE_SHIFT = re.compile(r"(สิ้นเดือน)(หน้า)?|(เมื่อวานซืน|เมื่อวาน|พรุ่งนี้|มะรืน)|"
                        r"(อีก)?(\d{1,4})?(วัน|สัปดาห์|(?<!วัน)อาทิตย์|เดือน|ปี)(ข้างหน้า|หน้า|ก่อน|ที่แล้ว|ที่ผ่านมา)?")


def date_shifts(bare, today):
    """["อีก10วัน ตรงกับวันศุกร์ที่ 27 พฤศจิกายน 2569 (อ่านว่า ...)", ...] for the days the caller's words (no spaces) point at."""
    out = []
    for m in DATE_SHIFT.finditer(bare):
        end, nxt, word, more, n, unit, tail = m.groups()
        label = m.group(0)
        try:
            if end:
                day = add_months(today, 1 if nxt else 0)
                day = day.replace(day=calendar.monthrange(day.year, day.month)[1])
            elif word:
                day = today + datetime.timedelta(DAY_WORD[word])
            else:
                back = tail in ("ก่อน", "ที่แล้ว", "ที่ผ่านมา")
                if tail == "หน้า" or (back and not n):  # เดือนหน้า, ปีที่แล้ว: one step; "วันที่ 10 เดือนหน้า" is no 10 months
                    if unit == "วัน":  # วันหน้า, วันก่อน name no day
                        continue
                    n, label = 1, unit + tail
                elif not (n and (more or tail)):  # "เครดิต 30 วัน" points at no day
                    continue
                step = -int(n) if back else int(n)
                day = (add_months(today, step * (12 if unit == "ปี" else 1)) if unit in ("เดือน", "ปี") else
                       today + datetime.timedelta(step * (1 if unit == "วัน" else 7)))
        except (ValueError, OverflowError):  # past year 9999
            continue
        out.append(f"{label} ตรงกับ{thai_day(day)}")
    return out[:3]


def clock_note(heard):
    """The clock for this turn, only when the caller asks the date or time (why: see reply)."""
    bare = re.sub(r"\s+", "", heard)  # the STT spaces words out: "วันนี้ วัน อะไร" missed วันอะไร (call 2026-11-09 06:39)
    shifts = date_shifts(bare, datetime.date.today())
    if shifts or DATE_ASK.search(bare) or TIME_ASK.search(bare):
        counted = f" ระบบนับวันให้แล้ว ใช้วันที่ตามนี้ ห้ามนับเอง: {' / '.join(shifts)}" if shifts else ""
        return f"\n(ระบบ: นาฬิกาในเครื่องตอนนี้ {thai_now(bool(TIME_ASK.search(bare)))}{counted})"
    return ""


_THAI_TERMS = (
    ("BC AI Account", "บีซี เอไอ แอคเคานต์"),
    ("Google Drive", "กูเกิลไดรฟ์"),
    ("Account", "แอคเคานต์"),
    ("FIFO", "ไฟโฟ"),
    ("LIFO", "ไลโฟ"),
    ("LOT", "ล็อต"),
    ("POS", "พีโอเอส"),
    ("VAT", "แวต"),
    ("WHT", "หัก ณ ที่จ่าย"),
    ("SKU", "เอสเคยู"),
    ("SQL", "เอสคิวแอล"),
    ("API", "เอพีไอ"),
    ("ERP", "อีอาร์พี"),
    ("SME", "เอสเอ็มอี"),
    ("PDF", "พีดีเอฟ"),
    ("CSV", "ซีเอสวี"),
    ("Gmail", "จีเมล"),
    ("Google", "กูเกิล"),
    ("Online", "ออนไลน์"),
    ("Cloud", "คลาวด์"),
    # ลุงจืด 2026-11-17 "พูดภาษาอังกฤษ ไม่ชัด": each word below was read three times as written and three times in this Thai
    # spelling, then read back by Whisper. As written it came back wrong (Channel "จารย์ลก", Webhook "เว็บอก", Prep dropped),
    # in Thai spelling right. LINE was spelled out letter by letter ("แอลไอเอ็นอี") by the rule below.
    # ponytail: only words the log shows the boss leaving in English; a new one is read as written until it is added here
    ("LINE", "ไลน์"),
    ("Channel", "แชนแนล"),
    ("Prep", "เพรป"),
    ("e-Tax", "อีแท็กซ์"),
    ("AutoCare", "ออโต้แคร์"),
    ("Webhook", "เว็บฮุก"),
    ("ChatGPT", "แชตจีพีที"),
    ("Starter", "สตาร์ตเตอร์"),
    ("Desktop", "เดสก์ท็อป"),
    ("query", "คิวรี"),
    ("e-Filing", "อีไฟลิ่ง"),
)
_TERM_RE = re.compile(r"(?i)(?<![A-Za-z0-9])(" + "|".join(re.escape(k) for k, _ in _THAI_TERMS) + r")(?![A-Za-z0-9])")
_TERM_MAP = {k.lower(): f" {v} " for k, v in _THAI_TERMS}
_LETTER_TH = dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", (
    "เอ", "บี", "ซี", "ดี", "อี", "เอฟ", "จี", "เอช", "ไอ", "เจ", "เค", "แอล", "เอ็ม",
    "เอ็น", "โอ", "พี", "คิว", "อาร์", "เอส", "ที", "ยู", "วี", "ดับเบิลยู", "เอ็กซ์", "วาย", "แซด",
)))


_URL_LINE_TTS_RULES = (
    (re.compile(r"https?://", re.I), ""),
    (re.compile(r"(?:www\.)?bcaccount\.com/pricing\b", re.I), " ดับเบิลยู ดับเบิลยู ดับเบิลยู ดอท บีซี แอคเคานต์ ดอท คอม สแลช ไพรซ์ซิ่ง "),
    (
        re.compile(r"(?:www\.)?bcaccount\.com\b", re.I),
        lambda m: (" ดับเบิลยู ดับเบิลยู ดับเบิลยู ดอท " if m.group(0).lower().startswith("www.") else " ") + "บีซี แอคเคานต์ ดอท คอม ",
    ),
    (re.compile(r"(?:@bcaccount|แอดบีซีแอคเคาท์|แอดบีซีแอคเคานต์)", re.I), " แอด บี ซี แอค เคานต์ "),
    (re.compile(r"\bwww\.", re.I), " ดับเบิลยู ดับเบิลยู ดับเบิลยู ดอท "),
    (re.compile(r"\.co\.th\b", re.I), " ดอท ซี โอ ดอท ที เอช "),
    (re.compile(r"\.com\b", re.I), " ดอท คอม "),
)


def _thai_pronounce(text):
    """Write English terms in Thai spelling and spell short acronyms out by letter. Measured on F5-TTS-THAI, the voice
    before 2026-11-17: the words in _THAI_TERMS came back wrong as written and right in Thai spelling (Whisper read-back).
    ponytail: not measured again on OmniVoice; Thai spelling is what its Thai training data looks like."""
    # the prompts and the boss spell Account "แอคเคาท์": the greeting came back "BCAX เข้าครับ"; with "แอคเคานต์" it came back right
    out = str(text)
    for pat, rep in _URL_LINE_TTS_RULES:
        out = pat.sub(rep, out)
    out = re.sub(r"@([A-Za-z0-9_]{2,})", r" แอด \1 ", out)
    out = _TERM_RE.sub(lambda m: _TERM_MAP[m.group(1).lower()], out.replace("แอคเคาท์", "แอคเคานต์"))
    out = re.sub(r"(?<![A-Za-z0-9])[A-Z]{2,5}(?![A-Za-z0-9])",
                 lambda m: " " + "".join(_LETTER_TH[c] for c in m.group(0)) + " ", out)
    return re.sub(r"\s+", " ", out).strip()


def _digits(digits):
    return "".join(num_to_thaiword(int(d)) for d in digits)


def _number_words(m):
    whole, frac = m.group(1).replace(",", ""), (m.group(2) or "").rstrip("0") and m.group(2)
    # a leading zero, or ten digits and more, is a code and not an amount: said one digit at a time
    coded = "," not in m.group(1) and (len(whole) > 9 or (whole[0] == "0" and len(whole) > 2))
    words = _digits(whole) if coded else num_to_thaiword(int(whole))
    return f" {words}{'จุด' + _digits(frac) if frac else ''}{'เปอร์เซ็นต์' if m.group(3) else ''} "


_NUMBER_RE = re.compile(r"(?<!\d)(?<!\d\.)(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?!\d|\.\d)(\s?%)?")


def _say_numbers(text):
    """Digits to Thai words before the voice's own normalizer gets them. Measured 2026-11-17: f5_tts_th's normalize_text
    drops the signs first and reads what is left, so "87.50 บาท" came out as แปดพันเจ็ดร้อยห้าสิบ บาท, "7%" as เจ็ด, "3-5 วัน" as
    สามสิบห้า วัน, "10:30 น." as หนึ่งพันสามสิบ, "081-234-5678" without its ศูนย์, and 31 as สามสิบหนึ่ง.
    ponytail: satang are read as digits after จุด (87.50 is not "บาท ... สตางค์"), a date written 17/11/2569 as three numbers."""
    out = re.sub(r"(?<![\d.,-])0\d{1,2}(?:-\d{3,4}){1,2}(?![\d-])",  # a phone number, group by group
                 lambda m: " " + " ".join(_digits(g) for g in m.group(0).split("-")) + " ", str(text))
    out = re.sub(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)(?: ?น\.)?",  # 10:30 น.
                 lambda m: f" {num_to_thaiword(int(m.group(1)))}นาฬิกา{num_to_thaiword(int(m.group(2))) + 'นาที' if int(m.group(2)) else ''} ", out)
    out = re.sub(r"(?<![\d-])([1-9][\d,.]*) ?- ?([1-9][\d,.]*)(?![\d-])", r"\1 ถึง \2", out)  # 3-5 วัน
    return _NUMBER_RE.sub(_number_words, out)


def load_tts():
    """Prefer ElevenLabs Flash v2.5 Streaming TTS when configured (0 GPU VRAM, ~150ms TTFA);
    otherwise load local OmniVoice Thai on GPU."""
    use_el = (C.TTS_PROVIDER == "elevenlabs") or (C.TTS_PROVIDER == "auto" and bool(C.ELEVENLABS_API_KEY))
    if use_el and C.ELEVENLABS_API_KEY:
        el = ElevenLabsTTS(
            api_key=C.ELEVENLABS_API_KEY,
            voice_id=C.ELEVENLABS_VOICE_ID,
            model_id=C.ELEVENLABS_MODEL_ID,
            speed=C.ELEVENLABS_SPEED,
            cache_dir=C.TTS_CACHE_DIR,
            max_cache_mb=C.TTS_CACHE_MAX_MB,
        )
        log.info(
            "TTS engine: ElevenLabs Streaming (%s, voice=%s, 0 GPU VRAM, disk cache=%s)",
            C.ELEVENLABS_MODEL_ID, C.ELEVENLABS_VOICE_ID, C.TTS_CACHE_DIR,
        )
        return ("elevenlabs", el)
    kw = dict(device_map="cuda:0" if C.HAS_CUDA else "cpu", dtype=torch.float16 if C.HAS_CUDA else torch.float32)
    try:
        model = OmniVoice.from_pretrained(C.TTS_MODEL, local_files_only=True, **kw)  # no network at start once it is cached
    except Exception:
        model = OmniVoice.from_pretrained(C.TTS_MODEL, **kw)
    assert model.sampling_rate == C.TTS_SAMPLE_RATE, model.sampling_rate
    return model, model.create_voice_clone_prompt(ref_audio=C.REF_AUDIO_PATH, ref_text=C.REF_TEXT)


TTS_LOCK = threading.Lock()  # ponytail: one TTS phrase at a time for every caller; a second model if callers queue


_TTS_BP_SOS = butter(4, [110.0, 3500.0], btype="bandpass", fs=C.TTS_SAMPLE_RATE, output="sos")


def level(arr):
    """Clean telephony conditioning: DC removal, 110-3500 Hz zero-phase bandpass (anti-alias + rumble/hiss cut),
    soft pause noise expander/gate, active-speech RMS normalization to C.TTS_RMS, smooth 0.78 peak limiter,
    and 6ms cosine edge fades."""
    arr = np.asarray(arr, dtype=np.float32).reshape(-1)
    sr = C.TTS_SAMPLE_RATE
    if arr.size < 64:
        return arr
    arr = arr - float(np.mean(arr))
    if arr.size > 32:
        arr = sosfiltfilt(_TTS_BP_SOS, arr).astype(np.float32)
    env_win = max(1, int(0.015 * sr))
    rms_env = np.sqrt(np.maximum(uniform_filter1d(arr * arr, env_win), 1e-12))
    peak_env = float(np.max(rms_env))
    if peak_env < 1e-4:
        return np.zeros_like(arr)
    active_mask = rms_env > max(0.012, peak_env * 0.12)
    speech_rms = (
        float(np.sqrt(np.mean(arr[active_mask] ** 2)))
        if np.any(active_mask)
        else float(np.sqrt(np.mean(arr ** 2)))
    )
    if speech_rms < 1e-4:
        return np.zeros_like(arr)
    # Soft noise expander for room hiss / breath pauses (smooth S-curve down to -28 dB floor)
    gate_hi = speech_rms * 0.20
    gate_lo = speech_rms * 0.06
    t = np.clip((rms_env - gate_lo) / max(gate_hi - gate_lo, 1e-6), 0.0, 1.0)
    gate_gain = 0.04 + 0.96 * (t * t * (3.0 - 2.0 * t))
    hold_win = max(1, int(0.025 * sr))
    gate_gain = uniform_filter1d(maximum_filter1d(gate_gain, hold_win), hold_win)
    arr = (arr * gate_gain).astype(np.float32)
    # Normalize active speech to C.TTS_RMS with smooth peak limiter @ 0.78 (-2.2 dBFS)
    arr = arr * (C.TTS_RMS / speech_rms)
    win = 2 * int(0.02 * sr) + 1
    gain = np.minimum(1.0, 0.78 / np.maximum(maximum_filter1d(np.abs(arr), win), 1e-6))
    arr = (arr * uniform_filter1d(gain, win)).astype(np.float32)
    fade = min(int(0.006 * sr), arr.size // 4)
    if fade > 1:
        ramp = 0.5 * (1.0 - np.cos(np.linspace(0.0, np.pi, fade, dtype=np.float32)))
        arr[:fade] *= ramp
        arr[-fade:] *= ramp[::-1]
    return arr


def _prepare_tts_text(text, lang):
    if lang == "th":
        norm = normalize_text(_thai_pronounce(_say_numbers(text))).strip()
    else:
        norm = re.sub(r"[#*_`~|]", " ", text or "").strip()
    return norm if SPEAKABLE_RE.search(norm) else ""


_EMPATHY_RE = re.compile(r"(?:ขออภัย|เสียใจ|เข้าใจเลย|ลำบาก|ติดปัญหา|ยุ่งยาก|กังวล|หงุดหงิด|ช้า|พัง|เออเรอร์|error|sorry|problem|issue)", re.I)
_UPBEAT_RE = re.compile(r"(?:โอ้โห|เยี่ยมเลย|ยินดีที่ได้|ดีใจที่ได้|สุดยอด|ได้เลยครับ|สบายเลย|ตื่นเต้น|คุ้มมาก|great|awesome|welcome)", re.I)


def _detect_reply_emotion(heard: str, text: str) -> str:
    """Pick ElevenLabs voice emotion tone ('empathy' | 'upbeat' | 'friendly') from caller + reply text."""
    h = heard or ""
    t = text or ""
    if _EMPATHY_RE.search(h) or _EMPATHY_RE.search(t):
        return "empathy"
    if _UPBEAT_RE.search(t):
        return "upbeat"
    return "friendly"


def speak(text, lang_override=None, emotion="friendly"):
    cid = call_id() if "call_id" in globals() else ""
    lang = lang_override or CALL_LANG.get(cid) or "th"
    norm = _prepare_tts_text(text, lang)
    if not norm:
        return np.zeros((1, C.TTS_SAMPLE_RATE // 10), np.float32)
    if isinstance(tts, tuple) and tts[0] == "elevenlabs":
        el: ElevenLabsTTS = tts[1]
        try:
            arr = el.synthesize_pcm24k(norm, lang=lang, emotion=emotion).reshape(-1)
            if arr.size:
                return level(arr).reshape(1, -1)
        except Exception:
            log.exception("ElevenLabs synthesize failed")
        return np.zeros((1, C.TTS_SAMPLE_RATE // 10), np.float32)
    model, voice = tts
    # Balanced diffusion steps on phone (14 steps: crisp Thai articulation).
    steps = C.SIP_TTS_STEP if ("on_phone" in globals() and on_phone()) else C.TTS_STEP
    for _ in range(3):
        with TTS_LOCK, torch.inference_mode():
            wav = model.generate(text=norm, language=lang, voice_clone_prompt=voice, speed=C.TTS_SPEED,
                                 generation_config=OmniVoiceGenerationConfig(num_step=steps, guidance_scale=C.TTS_CFG))[0]
        arr = np.asarray(wav, dtype=np.float32).reshape(-1) if wav is not None else np.zeros(0, np.float32)
        if arr.size:
            _, idx = librosa.effects.trim(arr, top_db=40)
            if idx[1] > idx[0]:
                pad_pre, pad_post = int(0.03 * C.TTS_SAMPLE_RATE), int(0.05 * C.TTS_SAMPLE_RATE)
                arr = arr[max(0, int(idx[0]) - pad_pre):min(arr.size, int(idx[1]) + pad_post)]
            if arr.size:
                return level(arr).reshape(1, -1)
    return np.zeros((1, C.TTS_SAMPLE_RATE // 10), np.float32)


def stream_bot_out(text, lang_override=None, lead=0.0, emotion="friendly"):
    """Yield one seamless, level-normalized, edge-faded audio buffer per sentence so FastRTC's resampler
    and the 20ms RTP pacer never suffer chunk-boundary clicks or mid-word underruns."""
    cid = call_id() if "call_id" in globals() else ""
    lang = lang_override or CALL_LANG.get(cid) or "th"
    norm = _prepare_tts_text(text, lang)
    if not norm:
        return
    yield bot_out(text, speak(text, lang_override=lang_override, emotion=emotion), lead=lead)


def bot_out(text, wav, lead=0.0, filler=False):  # carries exact audio duration (s); optional silent preroll prevents WebRTC/speaker clipping word 1
    arr = np.asarray(wav, dtype=np.float32).reshape(1, -1)
    pad = int(round(max(0.0, lead) * C.TTS_SAMPLE_RATE))
    out = np.concatenate((np.zeros((1, pad), np.float32), arr), axis=1) if pad else arr
    ev = {"kind": "bot", "text": text, "dur": round(arr.size / C.TTS_SAMPLE_RATE, 2)}
    if filler:
        ev["filler"] = True
    if pad:
        ev["lead"] = round(pad / C.TTS_SAMPLE_RATE, 2)
    return (C.TTS_SAMPLE_RATE, out), AdditionalOutputs(ev)


# ---------------------------------------------------------------- tools: each returns (cards for the screen, text for the LLM)
def web_search(query):
    hits = DDGS(timeout=5).text(query, region="th-th", max_results=5)
    cards = [{"source": "web", "title": h["title"], "snippet": h["body"], "url": h["href"]} for h in hits]
    text = json.dumps(cards, ensure_ascii=False)
    try:  # full text of the top hit, so the on-screen answer can be complete rather than snippet-deep
        text += "\n\nเนื้อหาจากผลแรก:\n" + str(DDGS(timeout=4).extract(hits[0]["href"], fmt="text_plain")["content"])
    except Exception as e:
        log.info("extract skipped: %s", e)
    return cards, text


def read_doc(path):
    if path.lower().endswith(".pdf"):
        return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def search_docs(query):
    # ponytail: re-reads docs/ and scores 1000-char windows by character-trigram overlap (Thai has no spaces);
    # fine for a few hundred pages, switch to bge-m3 embeddings + a cached index when docs/ grows.
    grams = {w[i:i + 3] for w in query.lower().split() for i in range(max(1, len(w) - 2))}
    overlap = lambda s: sum(s.lower().count(g) for g in grams)  # noqa: E731
    hits = []
    for root, _, files in os.walk(C.DOCS_DIR):
        for name in files:
            path = os.path.join(root, name)
            if not name.lower().endswith((".txt", ".md", ".csv", ".pdf")) or os.path.getsize(path) > 20_000_000:
                continue
            try:
                text = read_doc(path)
            except Exception as e:
                log.warning("skip unreadable doc %s: %s", name, e)
                continue
            for i in range(0, max(1, len(text)), 800):
                chunk = text[i:i + 1000]
                if score := overlap(chunk):
                    hits.append((score, os.path.relpath(path, C.DOCS_DIR), chunk.strip()))
    hits.sort(key=lambda h: -h[0])
    hits = hits[:4]
    # the card shows the best-matching line; the LLM gets the whole window around it
    cards = [{"source": "docs", "title": name, "snippet": max(chunk.splitlines(), key=overlap)[:300], "url": None}
             for _, name, chunk in hits]
    found = [{"file": name, "text": chunk} for _, name, chunk in hits]
    return cards, json.dumps(found, ensure_ascii=False) if found else "ไม่พบในเอกสาร"


CALC_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}


def calc_value(node, src):
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return Decimal(ast.get_source_segment(src, node))  # the digits as written: no binary float in a money sum
    if isinstance(node, ast.BinOp) and type(node.op) in CALC_OPS:
        return CALC_OPS[type(node.op)](calc_value(node.left, src), calc_value(node.right, src))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = calc_value(node.operand, src)
        return -value if isinstance(node.op, ast.USub) else value
    raise ValueError("ใช้ได้แค่ตัวเลข + - * / และวงเล็บ")


def big_reading(value):
    """log 2026-11-15 09:22: calc gave 200,000,000,000,000, the boss read it out as สองแสนล้านล้าน (1000 times too big)
    and the number guard cut the only answer. From a million up a result carries its Thai words for the boss to copy."""
    if abs(value) < 1_000_000:
        return ""
    whole, _, frac = f"{abs(value):f}".partition(".")
    words = num_to_thaiword(int(whole)) + ("จุด" + "".join(num_to_thaiword(int(d)) for d in frac) if frac else "")
    return f" (อ่านว่า {'ลบ' if value < 0 else ''}{words})"


def calc(lines):
    """ลุงจืด 2026-11-08 "เอา deepseek มาเสริมได้ถ้าไม่รู้หรือทำไม่ได้": a worked example the caller asked for
    (call 05:16, "ช่วยทำตัวอย่างให้หน่อย") had every number cut, none was in a source. Code does the sums, so the
    numbers and labels here are this turn's sources. ponytail: a label's words become sources too, so the boss
    could pass a claim through one; one sum per line, an earlier line's result is written out, not named."""
    out = []
    for line in str(lines).splitlines()[:120]:
        label, _, expr = line.rpartition(":")
        src = expr.replace(",", "").replace("×", "*").replace("÷", "/").strip()[:200]
        if not src:
            continue
        try:
            value = calc_value(ast.parse(src, mode="eval").body, src)
            value = value.quantize(Decimal("0.01"), ROUND_HALF_UP).normalize()  # baht and satang, half up, once
            out.append(f"{label.strip()} {src} = {value:,f}{big_reading(value)}".strip())
        except (SyntaxError, ValueError, ArithmeticError) as e:
            out.append(f"{line.strip()} คิดไม่ได้ ({e})")
    text = "\n".join(out)
    card = {"source": "calc", "title": "เครื่องคิดเลข", "snippet": text[:300], "url": None}
    # eval 2026-11-08: a label without its unit ("ชิ้นละ 100: 10*100") got all 4 spoken example sentences cut over
    # บาท. ponytail: in a turn that used calc, บาท and ชิ้น pass the judge even for another currency; digits are still checked
    return [card], f"{text}\n(หน่วยตามชื่อบรรทัด เช่น บาท ชิ้น)" if text else "ไม่มีสูตรให้คิด"


# ลุงจืด 2026-11-08: what the BC brain and the web don't have, DeepSeek's own knowledge may answer, told to the caller
# as that and never as BC data. The boss's own memory is still cut by the judge: only this worker's answer is a source
GENERAL_ASK = (f"{C.EXPERT}แต่งานนี้ตอบความรู้ทั่วไปได้ทุกเรื่อง ห้ามปฏิเสธว่าอยู่นอกขอบเขต "  # eval r14: the persona made it refuse the sky
               "ตอบคำถามจากความรู้ทั่วไปเป็นภาษาไทย 2-4 ประโยค เฉพาะส่วนที่แน่ใจ ส่วนที่ไม่แน่ใจให้บอกว่าไม่แน่ใจ "
               "ห้ามตอบเรื่องโปรแกรม BC ราคา โปรโมชัน หรือข้อมูลของบริษัทใด ให้ตอบว่าต้องดูจากคลังความรู้ "
               # log 2026-11-08 07:21: it said "ค่าขนส่งถือเป็นค่าจ้างทำของหรือค่าบริการ" (RD ruling 0706/8419: transport is its own class)
               "อัตราภาษี มาตรา หรือข้อกฎหมายไทย ห้ามตอบจากความจำ ให้ตอบว่าในคลังไม่มีเรื่องนี้ ต้องให้นักบัญชียืนยัน "
               "อัตราหรือเรื่องอื่นที่เปลี่ยนตามเวลา ให้บอกด้วยว่าควรเช็กกับแหล่งทางการล่าสุด")


def general_knowledge(question):
    text, _ = worker_chat([{"role": "system", "content": GENERAL_ASK}, {"role": "user", "content": str(question)[:500]}], None)
    text = " ".join(text.split())
    card = {"source": "deepseek", "title": "ความรู้ทั่วไป (ไม่ใช่ข้อมูลของโปรแกรม)", "snippet": text[:300], "url": None}
    return [card], f"ความรู้ทั่วไปจาก DeepSeek ไม่ใช่ข้อมูลของโปรแกรม ต้องบอกผู้ฟังสั้นๆ ว่าเป็นความรู้ทั่วไป: {text}"


MCP_CACHE_TTL_S = 300.0
MCP_CACHE_MAX = 256
MCP_CACHEABLE_TOOLS = frozenset({"quick_answer", "batch_ask", "lookup_menu", "lookup_error", "batch_read", "read_note", "search_brain", "ask_brain"})
_BOOTSTRAP_LOCK = threading.Lock()
_BOOTSTRAP_CACHE: dict[str, dict] = {}
BRAIN_AUDIENCE_TOOLS = frozenset({
    "quick_answer", "batch_ask", "lookup_menu", "lookup_error", "batch_read",
    "ask_brain", "search_brain", "traverse_brain", "list_notes", "find_media",
})


def _is_brain_abstain(text: str) -> bool:
    s = str(text or "")
    if "🛑" not in s and "abstain" not in s.lower():
        return False
    if "[batch_ask" in s and re.search(r"confidence=(?:high|medium)\b", s, re.I):
        return False
    return True


def load_bootstrap_pack(audience: str = "user", force: bool = False) -> str:
    """โหลด BC Brain bootstrap-pack จากสมองในเครื่องลง RAM (0ms, ไม่ต้องผ่าน HTTP/MCP API)."""
    mirror = globals().get("BRAIN_MIRROR")
    if mirror is not None:
        if not mirror.ready and getattr(mirror, "vault_dir", "") and os.path.isdir(mirror.vault_dir):
            mirror.load_from_dir(mirror.vault_dir, force=True)
        if mirror.ready and hasattr(mirror, "get_bootstrap_pack"):
            local_pack = mirror.get_bootstrap_pack(audience)
            if local_pack:
                return local_pack
    url = getattr(C, "BRAIN_BOOTSTRAP_URL", "")
    ttl = float(getattr(C, "BRAIN_BOOTSTRAP_TTL_S", 1800))
    if not url:
        return ""
    now_m = time.monotonic()
    with _BOOTSTRAP_LOCK:
        entry = _BOOTSTRAP_CACHE.get(audience)
        if entry and not force and now_m - entry["ts"] < ttl:
            return entry["text"]
        prev_etag = entry.get("etag") if entry else None
        prev_text = entry.get("text", "") if entry else ""
    try:
        headers = {"Accept": "text/markdown, text/plain"}
        if prev_etag:
            headers["If-None-Match"] = prev_etag
        r = httpx.get(f"{url}?audience={audience}", headers=headers, timeout=5.0)
        if r.status_code == 304 and prev_text:
            with _BOOTSTRAP_LOCK:
                _BOOTSTRAP_CACHE[audience] = {"ts": now_m, "etag": prev_etag, "text": prev_text}
            return prev_text
        if r.status_code == 200 and r.text:
            text = r.text.strip()[:20000]
            etag = r.headers.get("etag") or prev_etag
            with _BOOTSTRAP_LOCK:
                _BOOTSTRAP_CACHE[audience] = {"ts": now_m, "etag": etag, "text": text}
            return text
    except Exception as e:
        log.warning("BC Brain bootstrap-pack(%s) fetch skipped: %s", audience, e)
    return prev_text


class MCPClient:
    """MCP v2.3.0 POST-only streamable-HTTP client with JSON-RPC batch array and 300s RAM cache."""

    def __init__(self, url):
        self.url, self.http, self.n = url, httpx.Client(timeout=20, limits=httpx.Limits(keepalive_expiry=120)), 0  # kept alive between turns
        self.headers = {"Accept": "application/json, text/event-stream"}
        if not hasattr(self, "_cache"):
            self._cache: dict[str, tuple[float, str]] = {}
            self._cache_lock = threading.Lock()
            self._rpc_lock = threading.RLock()
        with self._rpc_lock:
            self.rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "thai-voice-agent", "version": "2.3.0"}})
            self.http.post(url, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=self.headers)

    def _cache_key(self, name, args) -> str | None:
        if name not in MCP_CACHEABLE_TOOLS:
            return None
        return f"{name}:{json.dumps(args, ensure_ascii=False, sort_keys=True)}"

    def _cache_get(self, cache_key: str | None) -> str | None:
        if not cache_key:
            return None
        now_m = time.monotonic()
        with self._cache_lock:
            hit = self._cache.get(cache_key)
            if hit and now_m - hit[0] < MCP_CACHE_TTL_S:
                return hit[1]
        return None

    def _cache_put(self, cache_key: str | None, text: str) -> None:
        if not cache_key or not text:
            return
        with self._cache_lock:
            if len(self._cache) >= MCP_CACHE_MAX:
                self._cache.pop(next(iter(self._cache)), None)
            self._cache[cache_key] = (time.monotonic(), text)

    def rpc(self, method, params):
        with self._rpc_lock:
            self.n += 1
            r = self.http.post(self.url, json={"jsonrpc": "2.0", "id": self.n, "method": method, "params": params}, headers=self.headers)
            r.raise_for_status()
            if sid := r.headers.get("mcp-session-id"):
                self.headers["Mcp-Session-Id"] = sid
        body = r.text
        if "event-stream" in r.headers.get("content-type", ""):
            body = [line[5:] for line in body.splitlines() if line.startswith("data:")][-1]
        msg = json.loads(body)
        if "error" in msg:
            raise RuntimeError(msg["error"].get("message"))
        return msg["result"]

    def read_only_tools(self):  # a voice command must never write into another system
        return [t for t in self.rpc("tools/list", {})["tools"] if (t.get("annotations") or {}).get("readOnlyHint")]

    def call(self, name, args):
        cache_key = self._cache_key(name, args)
        if (cached := self._cache_get(cache_key)) is not None:
            return cached
        try:
            result = self.rpc("tools/call", {"name": name, "arguments": args})
        except httpx.HTTPStatusError:  # session expired (server restarted) -> new session, retry once
            self.__init__(self.url)
            result = self.rpc("tools/call", {"name": name, "arguments": args})
        text = "".join(c.get("text", "") for c in result.get("content", []))
        self._cache_put(cache_key, text)
        return text

    def call_batch(self, items: list[tuple[str, dict]]) -> list[str]:
        """Execute multiple MCP tools/call over a single POST /mcp JSON-RPC batch array and fill RAM cache."""
        results = [""] * len(items)
        pending = []
        for idx, (name, args) in enumerate(items):
            ck = self._cache_key(name, args)
            if (cached := self._cache_get(ck)) is not None:
                results[idx] = cached
            else:
                pending.append((idx, name, args, ck))
        if not pending:
            return results
        if len(pending) == 1:
            idx, name, args, _ = pending[0]
            results[idx] = self.call(name, args)
            return results
        with self._rpc_lock:
            payload = []
            id_map = {}
            for idx, name, args, ck in pending:
                self.n += 1
                req_id = self.n
                id_map[req_id] = (idx, name, args, ck)
                payload.append({"jsonrpc": "2.0", "id": req_id, "method": "tools/call", "params": {"name": name, "arguments": args}})
            r = self.http.post(self.url, json=payload, headers=self.headers)
            if r.status_code >= 400:
                self.__init__(self.url)
                r = self.http.post(self.url, json=payload, headers=self.headers)
            r.raise_for_status()
            if sid := r.headers.get("mcp-session-id"):
                self.headers["Mcp-Session-Id"] = sid
        parsed = json.loads(r.text)
        if not isinstance(parsed, list):
            for idx, name, args, _ in pending:
                results[idx] = self.call(name, args)
            return results
        for msg in parsed:
            req_id = msg.get("id")
            if req_id not in id_map:
                continue
            idx, name, args, ck = id_map.pop(req_id)
            if "error" in msg:
                results[idx] = self.call(name, args)
                continue
            txt = "".join(c.get("text", "") for c in (msg.get("result") or {}).get("content", []))
            self._cache_put(ck, txt)
            results[idx] = txt
        for idx, name, args, _ in id_map.values():
            results[idx] = self.call(name, args)
        return results


def tool_spec(name, description, parameters):
    return {"type": "function", "function": {"name": name, "description": description, "parameters": parameters}}


def one_arg(name, desc):
    return {"type": "object", "properties": {name: {"type": "string", "description": desc}}, "required": [name]}


TOOLS = {"web_search": web_search, "search_docs": search_docs, "calc": calc,
         "general_knowledge": general_knowledge}
WORKER = {"web_search": "web", "search_docs": "docs", "calc": "calc", "general_knowledge": "deepseek"}  # tool -> team member shown on screen
TOOL_SPECS = [
    tool_spec("web_search", "ค้นข้อมูลจากอินเทอร์เน็ต เช่น ข่าว ราคา สภาพอากาศ หรือข้อเท็จจริงที่ไม่แน่ใจ", one_arg("query", "คำค้น")),
    tool_spec("search_docs", "ค้นเอกสารและไฟล์ของผู้ใช้ในเครื่อง", one_arg("query", "คำสำคัญสั้นๆ คั่นด้วยเว้นวรรค เช่น ร้าน เวลาเปิด")),
    tool_spec("calc", "เครื่องคิดเลข คิดตัวเลขที่ต้องคำนวณก่อนพูดทุกครั้ง เช่น ตัวอย่างที่ผู้ฟังขอ ต้นทุนถัวเฉลี่ย VAT ยอดรวม",
              one_arg("lines", "บรรทัดละหนึ่งสูตร เขียนว่า สิ่งที่คิดพร้อมหน่วย: สูตร ใช้ได้แค่ตัวเลข + - * / และวงเล็บ "
                      "เช่น รับเข้า 10 ชิ้น ชิ้นละ 50 บาท: 10*50 ตัวเลขทุกช่องของตารางรวมถึงยอดคงเหลือและแถวรวมสุดท้ายต้องมีบรรทัดของมัน "
                      "รหัสสมมติ เช่น LOT A001 ให้อยู่ในชื่อบรรทัด")),
    tool_spec("general_knowledge", "ถามความรู้ทั่วไปที่ไม่ใช่เรื่องโปรแกรม BC ราคา หรือของบริษัท ใช้เมื่อคลังความรู้และเว็บไม่มีคำตอบเท่านั้น",
              one_arg("question", "คำถามเต็มประโยค")),
]
STATUS = {"web_search": "กำลังค้นหาในอินเทอร์เน็ต", "search_docs": "กำลังค้นเอกสารในเครื่อง",
          "calc": "กำลังคำนวณ", "general_knowledge": "กำลังถามความรู้ทั่วไป"}
FILLER_TEXT = {
    "hello": "สวัสดีครับ",
    "phone_intro": "ผมเจาร์วิส ผู้ช่วยเอไออัตโนมัติของบีซีคอลเซ็นเตอร์ครับ ตอนนี้เวอร์ชันล่าสุดคือบีซีเอไอแอคเคาท์ ถามเจาร์วิสได้ในเวอร์ชันใหม่เลยทุกเรื่อง ส่วนถ้าใช้บีซีแอคเคาท์เวอร์ชันเก่าแจ้งฝากเบอร์โทรกลับไว้ในระบบได้ครับ ไม่ทราบว่าขอเรียกคุณชื่ออะไรดีครับ",
    "version_notice": "ตอนนี้เวอร์ชันล่าสุดคือบีซีเอไอแอคเคาท์ ถามเจาร์วิสได้ในเวอร์ชันใหม่เลยทุกเรื่อง ส่วนถ้าใช้บีซีแอคเคาท์เวอร์ชันเก่าฝากเบอร์โทรกลับไว้ในระบบได้นะครับ",
    "returning_ask": "วันนี้มีเรื่องไหนให้ผมช่วยดูครับ",
    "wait_th_1": "แป๊บนะครับ",
    "wait_th_2": "ครับผม ขอเช็กนิดนึงนะ",
    "wait_th_3": "เดี๋ยวดูให้นะครับ",
    "wait_th_4": "ได้ครับ แป๊บเดียว",
    "wait_th_5": "ครับ ขอเปิดดูแป๊บนะครับ",
    "wait_th_6": "โอเคครับ รอนิดนึงนะ",
    "wait_th_7": "เดี๋ยวเช็กให้เลยครับ",
    "wait_th_8": "ครับผม แป๊บเดียวครับ",
    "wait_en_1": "Sure, one sec.",
    "wait_en_2": "Got it, let me check.",
    "wait_en_3": "Just a moment.",
    "lang_ack_th": LANG_SWITCHED_ACK["th"],
    "lang_ack_en": LANG_SWITCHED_ACK["en"],
    "lang_confirm_th": LANG_CONFIRM_PROMPTS["th"],
    "lang_confirm_en": LANG_CONFIRM_PROMPTS["en"],
}  # pre-rendered greeting & short, varied natural cues (0ms LLM/TTS wait)
FILLERS = {}  # pre-rendered at warm-up (served from disk cache at 0 credits)
_WAIT_CYCLE_TH = itertools.cycle(
    ("wait_th_1", "wait_th_2", "wait_th_3", "wait_th_4", "wait_th_5", "wait_th_6", "wait_th_7", "wait_th_8")
)
_WAIT_CYCLE_EN = itertools.cycle(("wait_en_1", "wait_en_2", "wait_en_3"))


def _pick_wait_filler(lang: str | None) -> tuple[str, np.ndarray] | None:
    cid = call_id() if "call_id" in globals() else ""
    now_m = time.monotonic()
    # Cooldown 9s so we never repeat wait fillers back-to-back on rapid turns
    if cid and now_m - LAST_WAIT_AT.get(cid, 0.0) < 9.0:
        return None
    key = next(_WAIT_CYCLE_EN) if lang == "en" else next(_WAIT_CYCLE_TH)
    wav = FILLERS.get(key)
    if wav is None:
        return None
    if cid:
        LAST_WAIT_AT[cid] = now_m
    return FILLER_TEXT[key], wav


def _filler_or_speak(key: str, text: str, lang: str) -> np.ndarray:
    wav = FILLERS.get(key)
    return wav if wav is not None else speak(text, lang_override=lang)


# ---------------------------------------------------------------- a customer's message for the owner
LEAD_FIELDS = {"name": 80, "company": 120, "phone": 30, "message": 500}  # field -> characters kept
_GENERIC_CB_MSG_RE = re.compile(r"^(?:ขอให้โทรกลับ|ให้โทรกลับ|ติดต่อกลับ|ขอติดต่อกลับ|คุยกับพนักงาน(?:จริง)?|ฝากเรื่อง|ขอฝากเรื่อง)$")
_CB_STRIP_RE = re.compile(
    r"(?:สวัสดี|ฮัลโหล|ผม|ดิฉัน|หนู|ฉัน|คุณ|พี่|ครับ|ค่ะ|นะ|เลย|ได้|เอา|ใช้|ใช่|โอเค|ช่วย|ติดต่อ|ขอบคุณ|ไม่เป็นไร|ยังไม่มี|ไม่มี|"
    r"เบอร์นี้|เบอร์เดิม|เบอร์ที่โทร(?:เข้ามา(?:นี้)?)?|เบอร์อื่น|เบอร์โทร(?:กลับ)?|หมายเลข|ชื่อ\s*\S+|"
    r"อยาก|ต้องการ|ขอ|ให้|จะ|และ|กับ|ด้วย|หน่อย|คุยกับ|พนักงาน(?:จริง)?|โทรกลับ|ติดต่อกลับ|ฝากเรื่อง|[\d\-\s]+)"
)


def _has_continuous_topic(topic: str, history: list | None) -> bool:
    t = " ".join(str(topic or "").split())
    if len(t) >= 4 and not _GENERIC_CB_MSG_RE.fullmatch(t):
        return True
    for m in (history or [])[-6:]:
        if m.get("role") != "user":
            continue
        u = " ".join(str(m.get("content") or "").split())
        if len(u) < 12 or _GENERIC_CB_MSG_RE.fullmatch(u):
            continue
        if len(_CB_STRIP_RE.sub("", u).strip()) >= 6:
            return True
    return False


LEAD_LOCK, LEAD_COUNT = threading.Lock(), defaultdict(int)  # call id -> messages it left
LEAD_SPEC = tool_spec(
    # 2026-11-11 08:30: a caller gave a name and a number, the boss searched the brain for them and stored nothing
    "leave_message", f"บันทึกข้อความฝากติดต่อกลับ เรียกเมื่อลูกค้าต้องการคุยกับพนักงานจริง สนใจซื้อ หรือขอให้โทรกลับถึงฝ่ายขายหรือ{C.USER_NAME} "
    "ต้องเรียกหลังจากครบทั้งสามอย่างคือ (1) ได้ชื่อลูกค้า (2) ลูกค้ายืนยันแล้วว่าจะให้โทรกลับเบอร์ที่โทรเข้ามานี้หรือบอกเบอร์อื่น และ (3) มีเรื่องที่ต้องการให้โทรกลับ (สรุปจากเรื่องที่คุยต่อเนื่องอยู่ หรือถามลูกค้าถ้ายังไม่มีเรื่องคุยต่อเนื่อง) "
    "ห้ามบันทึกก่อนครบทั้งสามอย่าง ใส่เฉพาะข้อมูลจริงจากลูกค้าหรือข้อมูลสายที่ระบบให้ไว้ ห้ามแต่งเพิ่ม แล้วบอกลูกค้าตามผลที่ได้ "
    "ถ้ายังไม่ได้เรียกหรือเรียกไม่สำเร็จ ห้ามพูดว่าบันทึกหรือส่งเรื่องแล้ว",
    {"type": "object", "required": ["name", "phone", "message"], "properties": {
        "name": {"type": "string", "description": "ชื่อลูกค้าตามที่ลูกค้าบอกหรือจากข้อมูลลูกค้า"},
        "company": {"type": "string", "description": "ชื่อร้านหรือบริษัท ถ้าลูกค้าบอก"},
        "phone": {"type": "string", "description": "เบอร์โทรติดต่อกลับที่ลูกค้ายืนยันแล้ว (เบอร์ที่โทรเข้ามาหรือเบอร์อื่นที่ลูกค้าแจ้ง)"},
        "message": {"type": "string", "description": "เรื่องที่ลูกค้าต้องการให้โทรกลับ (เช่น ปัญหาของโปรแกรม สนใจโปรแกรม หรือเรื่องอื่นๆ โดยสรุปจากเรื่องที่คุยต่อเนื่องหรือถามลูกค้า)"}}})


def spoken_digits(text):  # "ศูนย์แปด 1-234" -> "081234": a number as the caller gave it, typed or read out
    for i, word in enumerate(grounding.DIGIT_WORDS):
        text = text.replace(word, str(i))
    return re.sub(r"\D", "", text)


def notify_owner():
    """ลุงจืด sees a new message at once: a Windows message box on this PC. No customer detail in it (the screen
    may be shared); the details are at /leads. ponytail: msg.exe only (Windows Pro), anything else just logs."""
    try:
        subprocess.Popen(["msg", "*", "/TIME:86400", f"J.A.R.V.I.S: มีลูกค้าฝากข้อความใหม่ เปิดดูที่ http://127.0.0.1:{C.SERVER_PORT}/leads"],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as e:
        log.warning("no message box for the new lead: %s", e)


def leave_message(name="", company="", phone="", message="", heard="", call=""):
    """ลุงจืด 2026-11-11: a caller left a name, a company and a number for a call back and nothing kept them (the
    log masks numbers). One JSON line per message in LEADS_PATH, shown at /leads on the owner line.
    `heard` and `call` come from reply(), not the boss: a number the caller never said is not stored (ห้ามแต่ง).
    ponytail: the name and company are stored as the boss wrote them; a number said in two parts with other
    digits between is refused and asked again; 3 messages per call, one message box per call."""
    lead = {k: " ".join(str(v).split())[:n] for (k, n), v in zip(LEAD_FIELDS.items(), (name, company, phone, message))}
    meta = CALL_CALLER_META.get(call) or {}
    caller_phone = spoken_digits(meta.get("phone") or "")
    lead["name"] = _clean_caller_name(lead["name"])[:LEAD_FIELDS["name"]]
    if not lead["name"] and meta.get("name"):
        lead["name"] = _clean_caller_name(meta.get("name"))[:LEAD_FIELDS["name"]]
    if not lead["company"] and meta.get("company"):
        lead["company"] = str(meta.get("company"))[:LEAD_FIELDS["company"]]
    if not lead["message"] and meta.get("lastTopic"):
        lead["message"] = " ".join(str(meta.get("lastTopic")).split())[:LEAD_FIELDS["message"]]
    digits = spoken_digits(lead["phone"])
    if not digits and 9 <= len(caller_phone) <= 15:
        lead["phone"] = caller_phone
        digits = caller_phone
    bad_name = (not lead["name"] or len(lead["name"]) < 2 or lead["name"].lower() in _BAD_CALLER_NAMES)
    if bad_name or not 9 <= len(digits) <= 15:
        return [], "ยังไม่ได้บันทึก ต้องมีชื่อลูกค้าจริงและเบอร์โทรติดต่อกลับที่ลูกค้ายืนยันแล้ว ให้ถามชื่อหรือถามยืนยันว่าจะให้โทรกลับเบอร์นี้หรือเบอร์อื่นก่อน"
    bad_msg = (not lead["message"] or len(lead["message"]) < 4 or bool(_GENERIC_CB_MSG_RE.fullmatch(lead["message"])))
    if bad_msg:
        return [], "ยังไม่ได้บันทึก ยังไม่ทราบเรื่องที่ต้องการให้โทรกลับ ให้ถามลูกค้าก่อนว่าต้องการให้โทรกลับเรื่องอะไร เช่น ปัญหาของโปรแกรม สนใจโปรแกรม หรือเรื่องอื่น"
    if digits not in spoken_digits(heard) and (not caller_phone or digits != caller_phone):
        return [], "ยังไม่ได้บันทึก เบอร์นี้ไม่ตรงกับที่ลูกค้าพูดหรือเบอร์ที่โทรเข้ามา ให้ถามเบอร์โทรอีกครั้ง"
    with LEAD_LOCK:
        if LEAD_COUNT[call] >= 3:
            return [], "ยังไม่ได้บันทึกเพิ่ม สายนี้ฝากข้อความครบ 3 ครั้งแล้ว"
        if call not in LEAD_COUNT and len(LEAD_COUNT) >= 512:
            LEAD_COUNT.pop(next(iter(LEAD_COUNT)))
        LEAD_COUNT[call] += 1
        first = LEAD_COUNT[call] == 1
        with open(C.LEADS_PATH, "a", encoding="utf-8") as f:
            at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
            f.write(json.dumps({"at": at, "call": call, **lead}, ensure_ascii=False) + "\n")
    if call and lead["name"] and not meta.get("name"):
        CALL_CALLER_META[call] = _sync_caller_profile(meta.get("phone") or lead["phone"], {**meta, "name": lead["name"]})
    if first:
        notify_owner()
    text = " ".join(f"{label} {lead[k]}" for k, label in (("name", "ชื่อ"), ("company", "บริษัท"), ("phone", "เบอร์"),
                                                         ("message", "เรื่อง")) if lead[k])
    card = {
        "source": "message",
        "title": "บันทึกข้อมูลติดต่อกลับแล้ว",
        "snippet": text[:300],
        "url": None,
        "lead": {
            "name": lead["name"],
            "company": lead["company"],
            "phone": lead["phone"],
            "callerPhone": caller_phone,
            "message": lead["message"],
            "at": at,
        },
    }
    return [card], f"บันทึกข้อความฝากติดต่อกลับแล้ว {text} จะประสานให้ติดต่อกลับ ยังไม่ได้นัดเวลาติดต่อกลับ"


def leads_page():
    """/leads: the messages customers left, newest first. Every field is the caller's text: escaped.
    ponytail: the newest 200, no delete or done mark; edit LEADS_PATH by hand."""
    rows = []
    try:
        with open(C.LEADS_PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except ValueError:  # a line cut short by a crash
                    pass
    except OSError:  # nobody left a message yet
        pass
    cells = []
    for r in reversed(rows[-200:]):
        try:
            at = datetime.datetime.fromisoformat(str(r.get("at"))).astimezone().strftime("%d/%m/%Y %H:%M")
        except ValueError:
            at = ""
        cells.append("<tr>" + "".join(f"<td>{html.escape(str(v))}</td>" for v in (
            at, *(r.get(k, "") for k in LEAD_FIELDS), str(r.get("call", "")).split("_")[0])) + "</tr>")
    return ("<!doctype html><html lang=th><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>ข้อความฝากจากลูกค้า</title><style>body{font:16px/1.5 system-ui,sans-serif;margin:24px;background:#0b1220;color:#e5e7eb}"
            "table{border-collapse:collapse;width:100%}th,td{border:1px solid #334155;padding:8px 12px;text-align:left;vertical-align:baseline}"
            "th{background:#1e293b}</style>"
            f"<h1>ข้อความฝากจากลูกค้า ({len(rows)} รายการ)</h1>"
            + ("<table><tr><th>เวลา</th><th>ชื่อ</th><th>ร้าน/บริษัท</th><th>เบอร์โทร</th><th>เรื่อง</th><th>รหัสสาย</th></tr>"
               + "".join(cells) + "</table>" if cells else "<p>ยังไม่มีลูกค้าฝากข้อความ</p>") + "</html>")


# ลุงจืด 2026-11-18: a customer call's prefetch reads the brain's "user" zone (plain-Thai guides), so notes of the code/SQL
# zone stay out of a call-center answer; asking for SQL, a query or a database table opens every zone
SQL_ASK = re.compile(r"(?i)(?<![a-z])(?:sql|quer(?:y|ies)|tables?|database|schema|api)(?![a-z])|เอสคิวแอล|คิวรี|เควรี|ฐานข้อมูล|ดาต้าเบส"
                     r"|เทเบิ้?ล|สคีมา|ฟิลด์|คอลัมน์|เอพีไอ|ตาราง(?:ข้อมูล|ไหน|อะไร)|(?:ชื่อ|โครงสร้าง)ตาราง")


def brain_audience(public, words):
    """The brain zone a prefetch search reads: caller lines ALWAYS stay strictly in 'user' zone (no SQL/code leaks)."""
    if public:
        return {"audience": "user"}
    return {"audience": "dev" if SQL_ASK.search(words) else "user"}


def brain_hits(server, text):
    """search_brain JSON -> only notes that really match, one card each (prefetch and the LLM's own calls alike)."""
    # ponytail: fixed score cut, calibrated on 11 real questions (relevant >= 52, off-topic <= 44 once news/prices
    # go to the web); keyword scores still let some off-topic notes through (POS 71 for a coffee-shop question),
    # so the prompt tells the boss to use only what matches. Upgrade: a reranker.
    try:
        hits = [h for h in json.loads(text) if isinstance(h, dict) and h.get("score", 0) >= C.BRAIN_MIN_SCORE][:3]
    except ValueError:
        return [{"source": server, "title": "search_brain", "snippet": text[:400], "url": None}], text
    cards = [{"source": server, "title": h.get("title", ""), "snippet": str(h.get("snippet", ""))[:300], "url": None, "slug": h.get("slug"), "media": hit_media(h)} for h in hits]
    found = [{k: h.get(k) for k in ("slug", "title", "snippet")} for h in hits]
    return cards, ("อ่านเต็มด้วย batch_read ตาม slugs: " + json.dumps(found, ensure_ascii=False)) if hits else "ไม่พบเรื่องที่ตรงกับคำนี้ในคลังความรู้ BC"


NOTE_META = re.compile(r"(?m)^(slug|sources):.*\n?")  # a BC note's metadata lines: its slug and the code paths it came from
# ลุงจืด 2026-11-17 "ระบบสมองเพิ่มภาพ และ youtube ถ้าเกี่ยวข้องให้เปิดมาด้วย": the brain ends a note with the videos and
# pictures that note has, "youtube_videos: title -> url (thumbnail: url) | title -> url" and "images: alt -> url | ..."
MEDIA_LINE = re.compile(r"(?m)^(youtube_videos|images): (.+)$")
MEDIA_ITEM = re.compile(r"(?:^| \| )(.+?) -> (\S+)(?: \(thumbnail: \S+\))?(?= \| |$)")  # a title may hold " | " itself
YT_ID = re.compile(r"(?:[?&]v=|youtu\.be/|/embed/)([\w-]{11})(?![\w-])")


def media_item(kind, title, url):
    """One picture or YouTube video the page shows under an answer, or None for anything else."""
    vid = YT_ID.search(url) if kind == "youtube" else None
    if kind not in ("youtube", "image") or not url.startswith("https://") or len(url) > 300 or (kind == "youtube" and not vid):
        return None  # ponytail: any https picture the brain names is shown (its own allow-list: bcaccount.com); a video plays by id
    return {"kind": kind, "title": title.strip()[:120], "url": url, **({"id": vid[1]} if vid else {})}


def note_media(text):
    """The videos and pictures the brain lists for a note it returned, in the brain's order."""
    found = (media_item("youtube" if kind == "youtube_videos" else "image", title, url)
             for kind, line in MEDIA_LINE.findall(text) for title, url in MEDIA_ITEM.findall(line.strip()))
    return [m for m in found if m]


def hit_media(hit):
    """The videos and pictures the brain attaches to a search hit: that note's own, the first three of each."""
    found = [*(media_item("youtube", str(v.get("title", "")), str(v.get("url", ""))) for v in hit.get("videos") or () if isinstance(v, dict)),
             *(media_item("image", str(i.get("alt", "")), str(i.get("url", ""))) for i in hit.get("images") or () if isinstance(i, dict))]
    return [m for m in found if m]


def brain_media(server, client, args, found):
    """find_media JSON -> one card of the pictures and videos of notes that really match the query, none otherwise."""
    # ponytail: the brain's find_media has no score cut (measured 2026-11-17: a nonsense query still got 6 items, all from
    # notes scoring under 30 or their neighbours), so an item counts only when the brain's search scores its note as
    # relevant: the same 10 hits in the same zone find_media starts from (bcbrain lib/vault.ts findMedia). An item of a
    # note outside those hits is a 1-hop neighbour of one of the first 4 (a sub-note's module picture); which one is not
    # told, so it counts only when all 4 are relevant. Costs one more brain call. Upgrade: a score per item from the brain.
    query = str(args.get("query", "")).strip()
    try:
        hits = json.loads(client.call("search_brain", {"query": query, "limit": 10, "audience": args.get("audience", "user")})) if query else []
    except ValueError:
        hits = []
    hits = [h for h in hits if isinstance(h, dict)]
    strong = {h.get("slug") for h in hits if h.get("score", 0) >= C.BRAIN_MIN_SCORE}
    near = bool(hits) and all(h.get("slug") in strong for h in hits[:4])
    weak = {h.get("slug") for h in hits} - strong
    items = [it for it in found.get("items", []) if isinstance(it, dict)
             and (it.get("noteSlug") in strong or near and it.get("noteSlug") not in weak)]
    if not items:
        return [], "ไม่พบภาพหรือวิดีโอที่ตรงกับเรื่องนี้"
    media = [m for it in items if (m := media_item(it.get("kind"), str(it.get("title", "")), str(it.get("url", ""))))]
    card = {"source": server, "title": f"ภาพและวิดีโอ: {query}", "snippet": " · ".join(m["title"] for m in media)[:300], "url": None, "media": media}
    return ([card] if media else []), json.dumps({**found, "count": len(items), "items": items}, ensure_ascii=False)


def _mcp_nearby_fallback(client, question: str, audience: str = "user") -> dict | None:
    """Fallback nearby note lookup via MCP search_brain + batch_read when RAM mirror is unavailable."""
    q = " ".join(str(question or "").split())[:200]
    if not q or client is None:
        return None
    aud = (audience or "user").strip() or "user"
    try:
        raw_hits = client.call("search_brain", {"query": q, "audience": aud, "limit": 3})
        hits = [h for h in json.loads(raw_hits) if isinstance(h, dict) and h.get("slug") and h.get("score", 0) >= 35]
        slugs = [str(h["slug"]) for h in hits[:2]]
        if not slugs:
            return None
        batch_txt = client.call(
            "batch_read",
            {"slugs": slugs, "audience": aud, "query": q, "max_chars_per_note": 650},
        )
        if not batch_txt or _is_brain_abstain(batch_txt):
            return None
        return {
            "text": (
                "[ข้อมูลใกล้เคียงจากคลังความรู้ BC (ไม่ใช่คำตอบตรงเป๊ะ — เล่าเฉพาะข้อเท็จจริงที่มีอยู่จริงด้านล่าง ห้ามเดา ห้ามแต่งเติม)]\n\n"
                + str(batch_txt)[:1600]
            ),
            "slugs": slugs,
            "menus": [],
            "top_score": float(hits[0].get("score", 0) or 0),
        }
    except Exception:
        return None


MENU_CODE_ASK = re.compile(r"(?:\b[1-9]\d{3}\b|\b\d+\.\d+(?:\.\d+)?\b|รหัสเมนู|เมนูไหน|เมนูอะไร|อยู่เมนู|หน้าจอไหน|เข้าตรงไหน)")


def mcp_tool(server, client, name):
    def run(**args):
        if name == "ask_brain" and "compact" not in args:
            args = {**args, "compact": True}
        mirror = globals().get("BRAIN_MIRROR") if server == "bcbrain" else None
        text = None
        if mirror is not None and mirror.ready:
            mirror.maybe_refresh()
            aud = (args.get("audience") or "user").strip() or "user"
            if name == "lookup_menu" and aud == "user":
                text = mirror.lookup_menu(args.get("query", ""), audience="user", limit=int(args.get("limit", 5)))
            elif name == "batch_read":
                text = mirror.batch_read(
                    args.get("slugs") or [],
                    audience=aud,
                    query=args.get("query", ""),
                    max_chars_per_note=int(args.get("max_chars_per_note", 2000)),
                )
            elif name == "read_note" and args.get("slug"):
                text = mirror.read_note(args["slug"], audience=aud)
        if text is None:
            try:
                text = client.call(name, args)
            except Exception:
                if mirror is not None and mirror.ready and name in ("quick_answer", "batch_ask"):
                    q_text = args.get("question") or " ".join(args.get("questions") or [])
                    fb = mirror.fallback_quick_answer(q_text, audience=(args.get("audience") or "user"), max_chars=int(args.get("max_chars", 700)))
                    if fb:
                        log.warning("bcbrain HTTP unavailable -> served mirror fallback for %s", name)
                        text = fb
                if text is None:
                    raise
        if _is_brain_abstain(text):
            nearby = None
            if server == "bcbrain" and name in ("quick_answer", "batch_ask"):
                q_text = str(args.get("question") or " ".join(args.get("questions") or [])).strip()
                aud = (args.get("audience") or "user").strip() or "user"
                if q_text:
                    if mirror is not None and mirror.ready:
                        nearby = mirror.search_nearby(
                            q_text,
                            audience=aud,
                            limit_notes=2,
                            limit_menus=4,
                            max_chars_per_note=1100,
                            min_score=12.0,
                        )
                    if nearby is None:
                        nearby = _mcp_nearby_fallback(client, q_text, audience=aud)
            if nearby and nearby.get("text"):
                log.info(
                    "BRAIN-NEARBY-FALLBACK (%s): slugs=%s menus=%d score=%.1f",
                    name,
                    nearby.get("slugs"),
                    len(nearby.get("menus") or ()),
                    float(nearby.get("top_score") or 0.0),
                )
                return [], (
                    f"{text}\n\n{nearby['text']}\n"
                    "(ระบบ: สมองไม่พบคำตอบตรงเป๊ะ [Abstain] แต่ค้นพบข้อมูลใกล้เคียงที่มีอยู่จริงด้านบน "
                    "ห้ามเดาและห้ามแต่งเติมเองเด็ดขาด ให้บอกลูกค้าสั้นๆ ตรงๆ ว่าส่วนที่ถามตรงๆ ยังไม่มีข้อมูลยืนยัน "
                    "แต่ในระบบมีส่วนที่ใกล้เคียงคือ ... แล้วเล่าเฉพาะข้อเท็จจริงที่มีในข้อมูลใกล้เคียงด้านบนเท่านั้น ห้ามโมเมว่าทำได้ในส่วนที่ไม่ได้ระบุไว้)"
                )
            return [], (
                f"{text}\n(ระบบ: สมองคืน Abstain และไม่พบข้อมูลใกล้เคียงในคลัง ห้ามเดาหรือแต่งคำตอบเองเด็ดขาด "
                "ให้แจ้งตรงๆ ว่าเรื่องนี้ยังไม่มีข้อมูลยืนยันในระบบ — แต่หากลูกค้าถามเรื่องประวัติการโทร จำนวนครั้งที่โทร "
                "ชื่อ หรือเบอร์โทรในสายนี้ ให้ตอบจากข้อมูลลูกค้าที่ระบบส่งให้ในรอบนี้ได้เลย)"
            )
        if name == "search_brain":
            return brain_hits(server, text)
        if name in ("read_note", "quick_answer", "batch_read", "batch_ask"):
            first_heading = next((ln.lstrip("# ").strip() for ln in text.splitlines() if ln.strip().startswith("#")), "")
            slug_m = re.search(r"(?m)^slug:\s*([\w.-]+)", text)
            body = NOTE_META.sub("", text).strip()
            title = first_heading or f"{name}: {next(iter(args.values()), '')}"
            return [{"source": server, "title": title[:140], "snippet": body[:300], "url": None,
                     "slug": args.get("slug") or (slug_m.group(1) if slug_m else None), "media": note_media(text)}], text
        if name == "lookup_menu":
            return [{"source": server, "title": f"เมนูระบบ: {args.get('query', '')}", "snippet": text[:300], "url": None}], text
        if name == "find_media" and text.lstrip().startswith("{"):
            return brain_media(server, client, args, json.loads(text))
        return [{"source": server, "title": f"{name}: {next(iter(args.values()), '')}", "snippet": text[:400], "url": None}], text
    return run


MCP_CONNECT_TRIES = 3  # tools are listed once at start-up: one 502 from bcbrain then left every call without it (2026-11-23)
MCP_RETRY_WAIT_SEC = 5


def connect_mcp(server, url):
    """The server's client and read-only tools, or None when it stays unreachable."""
    for attempt in range(1, MCP_CONNECT_TRIES + 1):
        try:
            client = MCPClient(url)
            return client, client.read_only_tools()
        except Exception as e:
            log.warning("MCP %s unavailable, try %d/%d (%s)", server, attempt, MCP_CONNECT_TRIES, e)
            if attempt < MCP_CONNECT_TRIES:
                time.sleep(MCP_RETRY_WAIT_SEC)
    return None


BRAIN_MIRROR = BrainVaultMirror(
    C.BRAIN_VAULT_PACK_URL,
    ttl_s=C.BRAIN_VAULT_PACK_TTL_S,
    vault_dir=getattr(C, "LOCAL_BRAIN_DIR", ""),
)
MCP_CLIENTS: dict[str, object] = {}
LEGACY_BRAIN_TOOLS = frozenset({"search_brain", "read_note", "ask_brain", "find_media"})


def _prefetch_mcp_batch(calls: list[tuple[str, dict]], tools: dict) -> None:
    """Warm remote MCPClient RAM cache in 1 POST /mcp batch array (skipped for in-process LocalBrainClient)."""
    if len(calls) < 2:
        return
    client = MCP_CLIENTS.get("bcbrain")
    if client is None or isinstance(client, LocalBrainClient) or not hasattr(client, "call_batch"):
        return
    mirror = globals().get("BRAIN_MIRROR")
    mirror_ok = bool(mirror is not None and mirror.ready)
    batch_items = [
        (n, ({**a, "compact": True} if n == "ask_brain" and "compact" not in a else dict(a)))
        for n, a in calls
        if WORKER.get(n) == "bcbrain" and n in tools
        and not (mirror_ok and ((n in ("batch_read", "read_note")) or (n == "lookup_menu" and (a.get("audience") or "user") == "user")))
    ]
    if len(batch_items) >= 2:
        try:
            client.call_batch(batch_items)
        except Exception as e:
            log.debug("MCP batch prefetch skipped (%s)", type(e).__name__)


def load_mcp_tools():
    def _register_brain_tools(server, client, tools):
        MCP_CLIENTS[server] = client
        tool_names = {t["name"] for t in tools}
        # When fast single-round-trip tools (quick_answer + batch_read) are available, hide slower legacy tools
        # from the LLM schema to prevent 3-4 round duplicate searches while keeping them in TOOLS for internal use.
        hide_from_llm = LEGACY_BRAIN_TOOLS if ("quick_answer" in tool_names and "batch_read" in tool_names) else frozenset()
        for t in tools:
            schema = {k: v for k, v in t["inputSchema"].items() if k != "$schema"}
            if t["name"] not in hide_from_llm:
                TOOL_SPECS.append(tool_spec(t["name"], t.get("description", ""), schema))
            TOOLS[t["name"]] = mcp_tool(server, client, t["name"])
            WORKER[t["name"]] = server
            STATUS[t["name"]] = f"กำลังค้นคลังความรู้ {server}"

    # 1. Embedded Local BC Brain (in-process RAM from d:\yalecom\bcbrain\vault — zero HTTP/MCP API calls)
    mirror = globals().get("BRAIN_MIRROR")
    local_dir = getattr(C, "LOCAL_BRAIN_DIR", "")
    if mirror is not None and local_dir and os.path.isdir(local_dir):
        st = mirror.load_from_dir(local_dir, force=True)
        if st.get("notes"):
            local_client = LocalBrainClient(mirror)
            local_tools = local_client.read_only_tools()
            _register_brain_tools("bcbrain", local_client, local_tools)
            log.info(
                "Local BC Brain (in-process): %d notes, %d menus, %d errors, tools=%s (dir=%s)",
                st["notes"], st["menus"], st.get("errors", 0), [t["name"] for t in local_tools], local_dir,
            )

            def _warm_pgvector():
                try:
                    pg_st = mirror.warmup_pgvector()
                    if pg_st.get("enabled"):
                        log.info(
                            "Local BC Brain pgvector: ready=%s chunks=%d model=%s warmupEmbedMs=%.1fms",
                            pg_st.get("ready"), pg_st.get("chunks", 0), pg_st.get("model"), float(pg_st.get("warmupEmbedMs") or 0.0),
                        )
                except Exception as e:
                    log.warning("Local BC Brain pgvector warmup skipped (%s)", type(e).__name__)

            threading.Thread(target=_warm_pgvector, name="bcbrain-pgvector-warm", daemon=True).start()

    # 2. Optional external MCP servers (only if explicitly configured and not already provided locally)
    for server, url in (getattr(C, "MCP_SERVERS", None) or {}).items():
        if server in MCP_CLIENTS:
            continue
        connected = connect_mcp(server, url)
        if not connected:
            log.warning("MCP %s unavailable -> its tools are skipped", server)
            continue
        client, tools = connected
        _register_brain_tools(server, client, tools)
        log.info("MCP %s: %s", server, [t["name"] for t in tools])

    # 3. Bootstrap pack (served directly from embedded local brain in 0ms; optional HTTP fallback)
    loader = globals().get("load_bootstrap_pack")
    if callable(loader):
        pack = loader("user", force=True)
        if pack:
            log.info("BC Brain bootstrap-pack (user): %d chars", len(pack))
    if mirror is not None and not mirror.ready and getattr(C, "BRAIN_VAULT_PACK_URL", ""):
        st = mirror.sync(force=True)
        if st.get("notes"):
            log.info("BC Brain vault-pack mirror: %d notes, %d menus (etag=%s)", st["notes"], st["menus"], st.get("etag"))


def run_tool(name, args, tools):
    try:  # `tools` is the caller's own table: on the public line a tool outside it is just an unknown tool
        cards, text = tools[name](**args)
    except Exception as e:  # unknown tool, bad args, network: tell the LLM instead of crashing the call
        log.warning("tool %s%s failed: %s", name, masked(str(args)), masked(str(e)))  # args may hold a phone read back
        return [], f"เรียก {name} ไม่สำเร็จหรือไม่พบข้อมูล ({e})"
    return cards, text[: C.TOOL_RESULT_CHARS] or "ไม่พบข้อมูล"


# webrtc_id -> what arrived beside the audio: typed chat, a system turn (greeting, reminder).
# ponytail: one small dict per call is never freed; clear it on hang-up if calls ever number in the hundred thousands
PENDING = defaultdict(lambda: {"text": "", "note": "", "carry": (0.0, "")})


def call_id():
    try:
        return get_current_context().webrtc_id
    except RuntimeError:  # no call (warm-up)
        return ""


def on_phone():  # a call bridged from BC Call Center: its id starts with C.SIP_PREFIX
    return call_id().startswith(C.SIP_PREFIX)


def tag_call(record):  # every log line names its call ("-" outside one), so watch_calls.py can tell calls apart;
    record.call = call_id().rpartition("_")[2] or "-"  # the call part only: the session part keys a caller's history
    return True


for h in logging.getLogger().handlers:  # lines logged before this point keep the plain format
    h.addFilter(tag_call)
    h.setFormatter(logging.Formatter("%(asctime)s %(call)s %(message)s"))


def take(key, empty):
    p = PENDING[call_id()]
    value, p[key] = p[key], empty
    if len(PENDING) > 256:
        for k in [k for k, v in list(PENDING.items())[:-128] if not v["text"] and not v["note"] and not v["carry"][1]]:
            PENDING.pop(k, None)
    return value


# ---------------------------------------------------------------- the call
SESSIONS = {}  # session id -> that caller's history (user/assistant pairs), loaded from SESSIONS_DIR on first use
SESSION_LOCK = threading.Lock()
SESSION_ID = re.compile(r"[0-9a-f]{8,32}")  # only these name a file: the id comes from the browser


def session(public=False):
    """The caller's own conversation. The browser keeps a session id (localStorage) in front of its webrtc_id
    ("<session>_<call>"), so a new call, a reload or a server restart goes on where the talk left off.
    Customer sessions live under their own "pub-" names: a public caller can never load an owner-line talk."""
    cid = call_id()
    sid = cid.split("_")[0]
    sid = (f"pub-{sid}" if public else sid) if SESSION_ID.fullmatch(sid) else ""  # "": no file
    # no session id (a BC Call Center phone call "sip-...", a test client): this call's own history, in memory only.
    # 2026-11-23: every such call shared the warm-up's "" history, so 5 phone calls in a row read one another's talk
    key = sid or (f"{'pub-' if public else ''}{cid}:" if cid else "")
    with SESSION_LOCK:
        if key not in SESSIONS:
            if key and len(SESSIONS) >= 256:
                for old in [k for k in SESSIONS if k][:64]:
                    SESSIONS.pop(old, None)
            try:
                if not sid:
                    raise OSError("no file")
                with open(os.path.join(C.SESSIONS_DIR, f"{sid}.json"), encoding="utf-8") as f:
                    SESSIONS[key] = [m for m in json.load(f) if isinstance(m, dict) and isinstance(m.get("content"), str)]
            except (OSError, ValueError, TypeError):  # new caller, or a file cut short by a crash
                SESSIONS[key] = []
        return sid, SESSIONS[key]


def save_session(sid, history):
    if sid:  # write then rename: a crash mid-write keeps the previous file
        path = os.path.join(C.SESSIONS_DIR, f"{sid}.json")
        tmp = f"{path}.{threading.get_ident()}.tmp"
        with SESSION_LOCK:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False)
            os.replace(tmp, path)


# one kept-alive connection: a fresh TLS handshake each call cost ~0.16 s before the first word (measured
# 2026-11-04, 12 s between calls); httpx drops idle ones after 5 s by default, turns come every 10-20 s
DEEPSEEK = httpx.Client(limits=httpx.Limits(keepalive_expiry=60))


# DeepSeek V4 sometimes writes a tool call as its own markup in the text instead of tool_calls (a known leak,
# e.g. github.com/anomalyco/opencode/pull/60575): <｜DSML｜tool_calls>, also with doubled bars or a "calls" wrapper,
# and in the last round with a space after the bars ("<｜｜DSML｜｜ invoke", replay 2026-11-08: 2 of 5 read no call)
DSML_BAR = r"[｜|]+\s*"
DSML_START = re.compile(rf"<{DSML_BAR}DSML")
DSML_INVOKE = re.compile(rf'<{DSML_BAR}DSML{DSML_BAR}invoke\s+name="([^"]+)"\s*>(.*?)</{DSML_BAR}DSML{DSML_BAR}invoke>', re.S)
DSML_PARAM = re.compile(rf'<{DSML_BAR}DSML{DSML_BAR}parameter\s+name="([^"]+)"(?:\s+string="(true|false)")?\s*>(.*?)'
                        rf'</{DSML_BAR}DSML{DSML_BAR}parameter>', re.S)


def dsml_calls(markup):
    """The tool calls in DeepSeek's DSML text, as deepseek_tokens builds them from tool_calls."""
    calls = []
    for n, (name, body) in enumerate(DSML_INVOKE.findall(markup)):
        args = {}
        for key, is_string, value in DSML_PARAM.findall(body):
            try:  # string="false": a JSON value (number, bool, list)
                args[key] = value if is_string != "false" else json.loads(value)
            except ValueError:
                args[key] = value
        calls.append({"id": f"dsml-{n}", "name": name, "args": args})
    return calls


def deepseek_tokens(messages, calls, tools, choice=None):
    """The boss on Ollama Cloud / DeepSeek (OpenAI-style SSE). A tool call arrives in pieces (id and name first,
    then bits of its JSON arguments), so calls are put together when the stream ends."""
    body = {"model": C.DEEPSEEK_MODEL, "messages": messages, "stream": True, "temperature": C.LLM_TEMPERATURE,
            "reasoning_effort": "none", **({"tools": tools} if tools else {}),  # disable reasoning: +seconds per turn
            **({"tool_choice": choice} if tools and choice else {})}  # "none", or a named tool
    parts, held, markup = {}, "", ""  # held: a tail that may be the start of DSML; markup: the DSML itself
    with DEEPSEEK.stream("POST", f"{C.DEEPSEEK_URL}/chat/completions", json=body, timeout=C.DEEPSEEK_TIMEOUT,
                      headers={"Authorization": f"Bearer {C.DEEPSEEK_API_KEY}"}) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            for choice in json.loads(line[6:]).get("choices") or []:
                delta = choice.get("delta") or {}
                for tc in delta.get("tool_calls") or []:
                    p = parts.setdefault(tc["index"], {"id": "", "name": "", "args": ""})
                    fn = tc.get("function") or {}
                    p["id"] += tc.get("id") or ""
                    p["name"] += fn.get("name") or ""
                    p["args"] += fn.get("arguments") or ""
                if markup:  # past the start of the markup: no more words for the caller
                    markup += delta.get("content") or ""
                    continue
                held += delta.get("content") or ""
                if m := DSML_START.search(held):
                    yield held[: m.start()]
                    held, markup = "", held[m.start() :]
                    continue
                cut = held.rfind("<")  # ponytail: a "<" waits up to 12 characters to see if DSML follows
                cut = len(held) if cut == -1 or len(held) - cut > 12 else cut
                yield held[:cut]
                held = held[cut:]
    yield held
    if markup and not parts and tools:  # with no tools offered there is nothing to run: the markup just goes
        log.info("DeepSeek wrote its tool call as text: %s", ", ".join(c["name"] for c in dsml_calls(markup)) or "none whole")
        calls.extend(dsml_calls(markup))
    for p in parts.values():
        try:
            args = json.loads(p["args"] or "{}")
        except ValueError:  # broken JSON: run_tool then tells the boss the call failed
            args = {}
        calls.append({"id": p["id"], "name": p["name"], "args": args if isinstance(args, dict) else {}})


def llm_tokens(messages, calls, tools, choice=None):
    """The boss on DeepSeek. If it fails before saying anything (network, quota, key) the caller hears BRAIN_DOWN,
    never a guess; half an answer already out just ends the turn."""
    spoke = False
    try:
        for tok in deepseek_tokens(messages, calls, tools, choice):
            spoke = spoke or bool(tok)
            yield tok
    except (httpx.HTTPError, ValueError, KeyError) as e:
        log.warning("DeepSeek failed (%s %s)", type(e).__name__, getattr(getattr(e, "response", None), "status_code", ""))
        calls.clear()
        if not spoke:
            yield BRAIN_DOWN


GEO, GEO_LOCK, GEO_LAST = {}, threading.Lock(), [0.0]  # place -> "lat,lon" (found ones only)
ROW_NO = re.compile(r"\s*\|\s*(\d{1,3})\s*\|")  # a table row's first cell, a plain count
MAP_LINE = re.compile(r"^\s*\[แผนที่\]\s*(.+?)\s*$")


def geocode(place):
    """Coordinates of a place the boss put on screen, from OpenStreetMap Nominatim; None if not found or offline.
    Their policy: at most 1 request a second for the whole app, an identifying User-Agent, cache the results."""
    if place not in GEO:
        with GEO_LOCK:  # ponytail: one global lock = the 1 req/s limit; a local Nominatim if maps get busy
            time.sleep(max(0.0, GEO_LAST[0] + 1 - time.monotonic()))
            try:
                r = httpx.get("https://nominatim.openstreetmap.org/search", timeout=5, headers={"User-Agent": C.GEO_USER_AGENT},
                              params={"q": place, "format": "jsonv2", "limit": 1, "accept-language": "th"})
                hit = r.json()[:1] if r.status_code == 200 else []
            except (httpx.HTTPError, ValueError):
                hit = []
            GEO_LAST[0] = time.monotonic()
        if hit:
            GEO[place] = f'{float(hit[0]["lat"]):.5f},{float(hit[0]["lon"]):.5f}'
    return GEO.get(place)


# SQL words a query may use that no note spells out; any other name in a code line must be in the sources
CODE_WORDS = set("select from where and or not in is null as on join left right inner outer full cross group by order "
                 "having sum count min max avg round abs coalesce nullif case when then else end between like ilike "
                 "distinct limit offset union all with asc desc cast numeric decimal integer int bigint smallint text "
                 "varchar char boolean date timestamp timestamptz now current_date current_timestamp interval filter "
                 "true false exists any lower upper trim extract over partition row_number rank dense_rank lag lead "
                 "greatest least concat replace substring split_part to_char to_date date_trunc string_agg array_agg "
                 "json jsonb jsonb_agg unnest generate_series lateral using values".split())
SQL_TYPES = re.compile(r"(?i)\b(?:numeric|decimal|varchar|char|character\s+varying|timestamp\w*)\s*\(\s*\d+(?:\s*,\s*\d+)?\s*\)")
SQL_OWN = re.compile(r"(?i)(?:\bas\s+([A-Za-z_]\w*)|\bwith\s+([A-Za-z_]\w*)\s+as\b|,\s*([A-Za-z_]\w*)\s+as\s*\()")


def code_unsourced(line, known, sources, own_names=None):
    """What a code line makes up: a table, column or number no source has. CTE/column aliases declared with AS
    carry across the code block (`own_names`), SQL type precisions like numeric(18,2) are stripped before number
    checking, and a source range "54-73" covers the flags between."""
    low = sources.lower()
    declared = {w.lower() for g in SQL_OWN.findall(line) for w in g if w} - CODE_WORDS
    if own_names is not None:
        own_names |= declared
    allowed = own_names if own_names is not None else declared
    names = {w for w in re.findall(r"[A-Za-z_]\w*", line)
             if len(w) > 2 and w.lower() not in CODE_WORDS and w.lower() not in low and w.lower() not in allowed}
    spans = {str(n) for a, b in re.findall(r"\b(\d{1,4})\s*-\s*(\d{1,4})\b", sources)
             if int(a) < int(b) <= int(a) + 100 for n in range(int(a), int(b) + 1)}
    clean_sql = SQL_TYPES.sub(" ", line)
    return names | grounding.unsourced(re.sub(r"[A-Za-z_]\w*|,", " | ", clean_sql), set(known) | spans)


# lines of a note the screen leaves out: its metadata, its media list and lone pictures or videos (the page shows those
# after the answer, ลุงจืด 2026-11-18), and the brain's links lines
# and its --- rules (the page has none: they showed as text)
NOTE_HIDE = re.compile(r"(?m)^(?:(?:slug|sources|youtube_videos|images|web_pages|links|backlinks):.*|[ \t]*!?\[[^\]]*\]\([^)\s]+\)[ \t]*|[ \t]*-{3,}[ \t]*)(?:\r?\n|$)")
EMPTY_SECTION = re.compile(r"(?m)^#{2,3} .*\n(?:[ \t]*\n)*(?=#{1,3} |\Z)")  # a heading left with nothing under it


def note_panel(note, part):
    """The top note's own words on the screen while the answer is made (ลุงจืด 2026-11-19 "จอขึ้นโน้ตทันที": a call center
    screen shows at once). Verbatim from the brain, so no judge; the answer's [จอ] takes its place (panel_events).
    ponytail: the search's top hit, which can be off the topic (BRAIN_MIN_SCORE); the first 1400 characters, cut at a line"""
    title, lines, heads = note.partition("\n")[0], NOTE_HIDE.sub("", part).strip().split("\n"), []
    while lines and (not lines[0].strip() or lines[0].startswith("# ")):  # the brain's title line, and the note's own
        heads.append(lines.pop(0))                                          # "# title" under it, worded its own way
    title = title if title.startswith("# ") else next((h for h in heads if h.strip()), "")
    text = re.sub(r"\n{3,}", "\n\n", EMPTY_SECTION.sub("", "\n".join([title, "", *lines]) + "\n")).strip()
    cut = text.rfind("\n", 0, 1400) if len(text) > 1400 else len(text)
    return text[:cut if cut > 0 else 1400]


def panel_events(tokens, screen, known, sources, turn):
    """The [จอ] part a line at a time: a line shows once complete, its numbers sourced and the judge backing it.
    Progress lives in `turn`, so a later tool round goes on from the lines already checked."""
    kept = turn.setdefault("kept", [])
    for tok in itertools.chain(tokens, ["\n"]):  # the extra "\n" closes the last line
        screen.append(tok)
        lines = "".join(screen).split("\n")[:-1]
        if len(lines) <= turn.get("lines", 0):
            continue
        for ln in lines[turn.get("lines", 0):]:
            # a table's first row is its column names, no claim: the judge cut "| ขั้น | สิ่งที่ระบบทำ | สูตร/เงื่อนไข |"
            # (2026-11-08 04:59) and the page, with no head row, ran the whole table into one paragraph
            head = ln.lstrip().startswith("|") and not (kept and kept[-1].lstrip().startswith("|"))
            # a table's own row numbers are no claim: rows 10, 11, 13 of a 13-step table were cut (call 2026-11-09
            # 06:38), 1-9 pass as lone digits. A first cell that counts on from the row above is that count.
            # ponytail: tables only; a numbered list past 9 items still has its 10. checked
            row = not head and ROW_NO.match(ln)
            if head or not ln.lstrip().startswith("|"):
                turn["row_no"] = 0
            facts = "|" + ln[row.end():] if row and int(row[1]) == turn.get("row_no", 0) + 1 else ln
            if row:
                turn["row_no"] = int(row[1])
            if fence := ln.lstrip().startswith("```"):  # only ```sql is checked as SQL code; plain fences still go through judge
                turn["code"] = False if turn.get("code") else ln.lstrip()[3:].strip().lower().startswith("sql")
                turn["sql_own"] = set()
            # a code line is checked by code: the judge cut true SQL lines one by one and left a broken query (eval 2026-11-08)
            code = turn.get("code") and not fence
            if fence or not re.search(r"\w", ln) or (not (bad := code_unsourced(ln, known, sources, turn.setdefault("sql_own", set())) if code else grounding.unsourced(facts, known))
                                                    and (head or code or is_label(ln) or judge(sources, ln, "\n".join(kept)))):
                m = MAP_LINE.match(ln)  # the page draws a map where the line carries "@lat,lon"
                kept.append(f"{ln} @{geo}" if m and (geo := geocode(m[1])) else ln)  # blank and |---| lines need no check
            else:  # logged like a spoken cut: javis 2026-11-07 showed a panel of only CUT_SHOW, with no trace why
                log.info("cut a panel line (%s): %s", f"{'numbers or names' if code else 'numbers'} {masked(' '.join(sorted(bad)))}" if bad else "judge", masked(ln))
                turn["panel_cut"] = True
        turn["lines"] = len(lines)
        # a note on the screen stays until the answer has a line of its own past its heading: a lone "# ..." or CUT_SHOW
        # would put less in its place
        shows = not turn.get("note") or any(re.search(r"\w", k) and not k.lstrip().startswith("#") for k in kept)
        if shows and turn.get("panel", "") != (text := "\n".join(kept + [grounding.CUT_SHOW] * turn.get("panel_cut", 0)).strip()):
            turn["panel"] = text
            yield AdditionalOutputs({"kind": "panel", "text": text})


SIDE = ThreadPoolExecutor(8)  # ponytail: a turn holds 2 at once (the boss's stream, one judge call); 8 is a few calls at a time


def aside(job, *args):
    """job(*args) on a side thread that still knows its call: the log's call id is a context variable."""
    return SIDE.submit(contextvars.copy_context().run, job, *args)


def beside(tokens, screen, known, sources, turn, stop):
    """The boss's stream read on a side thread: the spoken tokens come back as they arrive, the [จอ] lines after them
    are checked there and each panel event is left in `shown`. ลุงจืด 2026-11-19 "ให้แสดงข้อมูลเลย ไม่ต้องรอพูดเสร็จ":
    the screen used to start after every spoken sentence was judged and voiced (call 05:00:30: 2 sentences said, 2 more
    cut by the judge in 5.15 + 5.77 s, the first panel line 17 s into the turn, as the voice ended).
    ponytail: a line already at the judge when the turn is talked over is still judged, to no use"""
    words, shown = queue.SimpleQueue(), queue.SimpleQueue()

    def read():
        try:
            for tok in until_screen(tokens, screen):
                words.put(tok)
            words.put(None)  # the spoken part is over: its last phrase goes out while the screen still comes
            if not stop.is_set():  # a turn that ended or was talked over reads no more
                for ev in panel_events(itertools.takewhile(lambda _: not stop.is_set(), tokens), screen, known, sources, turn):
                    shown.put(ev)
        finally:
            words.put(None)  # an error here is raised where the turn waits for this thread, never a hang

    return iter(words.get, None), shown, aside(read)


def showing(shown, job):
    """The result of `job` (a future), handing out the panel events that come while it runs: no slow judge call holds the screen."""
    while not job.done() or not shown.empty():
        try:
            yield shown.get(timeout=0.05)
        except queue.Empty:
            pass
    return job.result()


BASE_PROMPT = C.SYSTEM_PROMPT  # load_workers appends what is true of this setup (team, Google, desktop) after it
# how it names a source, and the small talk a greeting may have ("เฮ้ ลุงจืด วันนี้สบายดีไหม" was cut as a new fact)
SAY_WORDS = (f"{C.USER_NAME} จากคลังความรู้บีซี จากเว็บ จากเอกสารในเครื่อง ไฟล์ แหล่งข้อมูล ลูกทีม สบายดี "
             "พนักงาน ตอบคำถาม คำถาม ผู้ใช้งาน ดูแล ประสาน ฝ่ายขาย เลขา รับสาย โทร ติดต่อกลับ แจ้ง รายละเอียด ชื่อ เบอร์ ช่องทางติดต่อ ร้าน บริษัท ธุรกิจ ทำบัญชี ความต้องการ สนใจ "
             "ราคา แพ็กเกจ โปรโมชัน "  # what the persona says when the brain has no price: "ให้ลุงจืดติดต่อกลับไปแจ้งราคา"
             "นักบัญชี ยืนยัน "  # config.py's own tax/accounting disclaimer: "ต้องให้ลุงจืดหรือนักบัญชียืนยัน"
             "ขั้นตอน แผนที่ การ์ด เคส กรณีตัวอย่าง "  # the panel's [ขั้นตอน], [แผนที่], [การ์ด] and > casebox marks
             # pronouns and polite talk: the judge still called ผม / เดี๋ยว new_info now and then and cut a whole line
             "ผม ดิฉัน คุณ ครับ ค่ะ คะ ยินดี ขอบคุณ ขอโทษ เดี๋ยว ขอ ถาม ทราบ รับทราบ เวลา ช่วย ตอนนี้ ก่อน "
             # log 2026-11-08 12:27 "รับสายแทนลุงจืดอยู่ครับ" (แทน, also 09:17) and the honest "ยังไม่แน่ใจครับ" (แน่ใจ)
             # were cut as new_info (judge replay 2 of 2 each)
             # log 2026-11-08 13:29 "มีอะไรให้ช่วยเรื่องโปรแกรมบัญชีไหมครับ" was cut over the question word ไหม (judge replay 2 of 2);
             # รึเปล่า is the one other question word still left as new (มั้ย หรือเปล่า หรือยัง บ้าง นะ are already filtered)
             "แทน แน่ใจ ไหม รึเปล่า ค้น ดู ตรวจสอบ สักครู่ "
             # what it sells and asks about (the prompt's sales lines are no source): the judge cut "ผมช่วยเรื่อง
             # โปรแกรมบีซีเอไอแอคเคาท์ครับ" over โปรแกรม, and a customer's "ช่วยอะไรได้บ้าง" got silence
             "โปรแกรมบีซีเอไอแอคเคาท์ บีซี เอไอ แอคเคาท์ BC Ai Account การใช้งาน")
# the judge sorts only the words code found in no source: asked "is this sentence backed?" the 8B model said yes to
# almost anything (weather, free parking, a made-up email), asked word by word it caught them (20/23 in a replay)
def is_label(ln):
    """The first line of a made-up example (ลุงจืด 2026-11-09 "ต้องแสดงตัวอย่างด้วยทุกครั้ง"): no claim, no judge.
    Not in SAY_WORDS: in the judge's sources it cut small talk, 4/5 -> 3/5 passed every run (eval 2026-11-09)."""
    # its words, not its exact text: the boss wrote "### ตัวอย่างสมมติ (ไม่ใช่ข้อมูลจริง)" (probe 2026-11-09)
    return C.SAMPLE_LABEL.split()[0] in ln and not grounding.new_words(ln, C.SAMPLE_LABEL)


class DeepSeekJudge:
    """The judge on DeepSeek (ลุงจืด 2026-11-19 "ทดลองตัวตรวจบน DeepSeek", then "ปรับแล้ววัดใหม่"). Judge lab, 2 runs each
    against the local qwen3-vl: true lines cut 8/35 (17/35), small talk passed 4/5 (3/5), lab made-up lines leaked 3/36
    (4/36, the same three and one more), real made-up 0, must-cut 10/10, a call median 0.74-0.78 s, max 1.28 s (local
    on calls: 1.6 s passed, 3.3 s cut). Ollama's chat shape (format = a JSON schema, the reply in .message.content), so
    judge(), not_talk() and the tests' fake llm keep it.
    ponytail: json_object mode takes no schema, so the schema is told in words (code still checks every source word);
    DeepSeek down or slower than DEEPSEEK_TIMEOUT -> no verdict -> the piece is cut"""

    def chat(self, model, messages, format):
        item = format["properties"]["words"]["items"]["properties"]
        keys = ", ".join(f'"{k}": ' + (" | ".join(json.dumps(x, ensure_ascii=False) for x in v["enum"]) if "enum" in v else "string")
                         for k, v in item.items())
        told = f'\n\nReply with JSON only: {{"words": [{{{keys}}}]}} with exactly one entry for each listed word.'
        body = {"model": model, "messages": [*messages[:-1], {**messages[-1], "content": messages[-1]["content"] + told}],
                "stream": False, "temperature": 0, "reasoning_effort": "none", "response_format": {"type": "json_object"}}
        r = DEEPSEEK.post(f"{C.DEEPSEEK_URL}/chat/completions", json=body, timeout=C.DEEPSEEK_TIMEOUT,
                          headers={"Authorization": f"Bearer {C.DEEPSEEK_API_KEY}"})
        r.raise_for_status()
        out = r.json()["choices"][0]
        return SimpleNamespace(message=SimpleNamespace(content=out["message"]["content"]), done_reason=out.get("finish_reason"))


llm = DeepSeekJudge()


def judge_format(new, types=("talk", "same_as_source", "new_info")):
    # one entry per new word, those words only: shorter output, none skipped or added
    props = {"word": {"type": "string", "enum": new}, "type": {"type": "string", "enum": list(types)}}
    if "same_as_source" in types:
        props["source_word"] = {"type": "string"}
    entry = {"type": "object", "required": list(props), "properties": props}
    return {"type": "object", "required": ["words"], "properties": {
        "words": {"type": "array", "items": entry, "minItems": len(new), "maxItems": len(new)}}}


# judge lab 2026-11-19 with the judge on DeepSeek (ลุงจืด "ปรับแล้ววัดใหม่"): "ลูกค้าของคุณคงหงุดหงิดกันแย่เลยครับ" passed
# both runs, its one new word แย่ called a kind reaction; with this rule 0 of 2. JUDGE_ASK only: TALK_ASK ran without it
FEELING_RULE = ("A feeling word (หงุดหงิด, เหนื่อย, แย่, สบาย) is talk only when it reacts to what the user said of themselves "
                "or asks how they feel; in a line that guesses how the user's customers or staff feel or fare, or says what the "
                "product will spare them, it is new_info (พนักงานคงดีใจกันใหญ่, ไม่ต้องปวดหัวกับบิลอีกต่อไป). ")
JUDGE_ASK = (
    "SOURCES:\n{src}\n\nA Thai voice assistant already said: {before}\nIt now wants to say: {piece}\n"
    "Words of it that appear nowhere in SOURCES: {new}\n\n"
    # ลุงจืด 2026-11-04 "ให้คุยเหมือนเพื่อน": a question about the caller's own side is talk (the judge cut
    # "สองสาขานี่สต็อกแยกกันหรือใช้ร่วมกันครับ"), a guess or offer is not. Eval on recorded calls: 2 of 19 wrong, was 5
    "Give each of those words a type. talk = a word about the conversation itself (a greeting, polite word, "
    "pronoun, asking, meaning, helping, finding, knowing, checking, being connected, or saying that details/a table/a "
    "query are shown on screen: ผม, หมายถึง, เช็ก, ไม่พบ, เขียนไว้บนจอ, ดูตารางบนจอ), "
    # log 2026-11-08 08:59 "สนใจเรื่องไหนเป็นพิเศษครับ", "อยากให้ช่วยเรื่องไหนเป็นพิเศษ" cut; replay 09:04 cut "…เป็นหลักครับ" on เป็นหลัก
    "a word that only says which part matters more (เป็นพิเศษ, เป็นหลัก), "
    "or a word the assistant uses to ask the user about the user's own side (their shop, work, way of doing "
    "things, stock, branches, bills, problem, wish or feeling) or to react kindly to what the user just said "
    "(เหนื่อยไหม, สาขาไหน, ติดปัญหาตรงไหน, สต็อกแยกกันหรือใช้ร่วมกัน, ช้าตอนพิมพ์, อยากให้ช่วยคำนวณให้เองใช่ไหม, "
    "เดินทางลำบาก, ยินดีที่ได้รู้จัก, ไม่ต้องรีบ), "
    # log 2026-11-08 10:12, 10:33: "ในคลังไม่มีตัวเลขจำนวนลูกค้าครับ" was cut and the caller heard CUT_ALL instead;
    # saying what is not there is the honest answer. A check of 6 lines (2 such, 4 made-up claims): 5 right, was 3
    "or a word naming what SOURCES do not have, in a sentence that only says SOURCES or the คลัง have no such "
    "thing and claims nothing else (ในคลังไม่มีตัวเลขจำนวนลูกค้า, ยังไม่มีคิวรี่สำเร็จรูปในคลัง), "
    "or a mock code, mock name or scenario word inside a hypothetical sample (such as WH01, A01, SKU01, BK001, INV001, "
    "คลังหน้าร้าน, ชั้นวาง, โซน, บิลขาย, ชื่อลูกค้าผิด, คู่ค้า, ร้านตัวอย่าง) when 'already said' contains 'ตัวอย่างสมมติ' "
    "and the line claims no new product feature, price, tax rate or rule. "
    "same_as_source = a Thai reading, other spelling or plain "
    "synonym of a word that IS in SOURCES; put that SOURCES word in source_word (แฟล็ก -> flag). "
    # log 2026-11-07 22:02 replays: true panel lines cut for "(Ctrl+S)", "(IA)", "**2)" though the note has Ctrl+S, IA
    "A SOURCES word with brackets, stars or other marks stuck to it is same_as_source too ((Ctrl+S) -> Ctrl+S). new_info = "
    "anything the assistant states or offers about itself, its product, a service or the world that SOURCES do "
    "not say, even inside a question, and any guess or advice it states (not asks) about the user, their shop or "
    "their customers (น่าจะชอบ, ต้องระวัง): a feature, service, price, free thing, discount, trial, visit, "
    "promise, place, day, weather, person, team, event, step or condition (ส่งฟรี, ไปติดตั้งให้, ทดลองใช้ฟรี, "
    "รองรับหลายสาขา, วันเสาร์, ต้องเซ็นชื่อ). " + FEELING_RULE +
    # eval 2026-11-08: "แถวบ้านผมก็ตกหนักเหมือนกันครับ" passed as talk; the assistant has no home or life
    "A life of its own is new_info too: its home, family, the weather where it is, what it did or felt "
    "(แถวบ้านผม, ที่บ้านผม, ผมก็เคย). source_word is \"\" unless same_as_source. Answer JSON."
)
# A sentence that asks the caller something first gets JUDGE_ASK's talk part on its own, with no SOURCES: the words it
# calls talk are done, the rest go to JUDGE_ASK. Beside 4500 chars of sources the 8B judge called nearly every new word
# new_info, its own talk examples too (48 judged pieces of the eval calls, 2026-11-18: 22 of 35 true ones cut; 17 with
# this ask first, and no made-up line more gets through).
# Questions only: asked for every sentence it let 4 of 42 made-up lines through that JUDGE_ASK cuts, each a statement
# whose new word was a feeling or a place ("เรื่องภาษีไม่ต้องปวดหัวเลยครับ ระบบยื่นให้เองหมด", "ที่ออฟฟิศผมก็ใช้ระบบนี้อยู่ครับ").
# ponytail: a marker list; a question with no marker stays on JUDGE_ASK alone, a statement holding one gets this ask too
ASKS = re.compile("ไหม|มั้ย|หรือเปล่า|รึเปล่า|หรือยัง|ยังไง|อย่างไร|อะไร|ไหน|เท่าไหร่|เท่าไร|กี่")
TALK_ASK = (
    "A Thai voice assistant already said: {before}\nIt now wants to say: {piece}\nWords of it to sort: {new}\n\n"
    "Give each of those words a type. talk = " + JUDGE_ASK.partition("talk = ")[2].partition("same_as_source = ")[0]
    + "claim = every other word: " + JUDGE_ASK.partition("new_info = ")[2].partition(" source_word is")[0]
    .replace(FEELING_RULE, "").replace("that SOURCES do not say, ", "").replace("is new_info too", "is claim too") + " Answer JSON.")
# said only when every phrase of an answer was cut. ลุงจืด 2026-11-08 "ผมไม่เจอในแหล่งข้อมูล ขอไม่เดานะครับ อันนี้พูดบ่อยไป":
# an answer with some phrases kept now just ends on them (the panel still notes the cut), and this asks for more words
CUT_ALL = "เรื่องนี้ผมยังหาข้อมูลที่แน่ใจไม่เจอครับ ช่วยเล่าเพิ่มอีกนิดได้ไหมครับ"
BARE_ACK = re.compile(r"(?:ได้|ได้เลย|ครับ|ค่ะ|ครับผม|ใช่|เข้าใจ|รับทราบ|โอเค|อ๋อ)(?:\s*(?:ครับ|ค่ะ|เลยครับ|นะครับ))?")
BARE_ADDRESS = re.compile(r"^(?:คุณ|พี่|ลุง|ป้า|น้า|อา|น้อง|เฮีย|เจ๊)[ก-๙a-zA-Z]{1,20}(?:\s*(?:ครับผม|นะครับ|นะคะ|ครับ|ค่ะ|คะ))?$")
SCREEN_ONLY = "รายละเอียดผมแสดงไว้บนจอแล้วครับ"
# DeepSeek at times ends a round on a promise ("ขอผมอ่านรายละเอียดก่อนนะครับ") with no tool call, and the turn went
# quiet (javis 2026-11-07: 5 turns of one call, the first with no history). ponytail: a phrase list; a new wording
# slips past until it is added here
STALL = re.compile(r"(ขอ|เดี๋ยว)(ผม)?(ให้(ลูก)?ทีม)?(อ่าน|เปิด|ค้น|ดู|เช็ก|ตรวจ)")
STALL_NUDGE = "(ระบบ: คุณบอกว่าจะอ่านหรือค้นเพิ่ม แต่ยังไม่ได้สั่งลูกทีม ให้เรียกเครื่องมือตอนนี้เลย หรือตอบจากผลที่ลูกทีมค้นให้แล้ว ห้ามบอกว่าจะอ่านอีก)"
LEAD_PROMISE = re.compile(r"ติดต่อกลับ|ประสาน|ส่งเรื่อง|ฝากเรื่อง|บันทึกเรื่องฝาก|บันทึกไว้ให้แล้ว")  # the boss telling a customer the message will be passed on
LEAD_NUDGE = ("(ระบบ: ลูกค้าขอให้พนักงานจริงติดต่อกลับและยืนยันเบอร์แล้ว แต่ยังไม่ได้เรียก leave_message ข้อความฝากยังไม่ถูกบันทึก "
              "ถ้ามีชื่อลูกค้าและลูกค้ายืนยันเบอร์โทรกลับแล้วให้เรียก leave_message ตอนนี้เลย ถ้ายังไม่มีชื่อให้ถามชื่อให้สำเร็จ หรือถ้ายังไม่ได้ถามว่าจะให้โทรกลับเบอร์นี้หรือเบอร์อื่นให้ถามยืนยันก่อน "
              "ถ้ายังไม่มีเรื่องที่คุยต่อเนื่องและลูกค้ายังไม่บอกว่าให้โทรกลับเรื่องอะไร ให้ถามก่อนว่าต้องการให้โทรกลับเรื่องอะไร (ปัญหาของโปรแกรม หรือเรื่องอื่น))")
LAST_ROUND = {"role": "user", "content": "(ระบบ: ค้นครบแล้ว ไม่มีเครื่องมือให้เรียกอีก ตอบผู้โทรจากผลที่ลูกทีมค้นให้แล้วตอนนี้เลย "
                                         "ถ้าไม่มีส่วนที่ตรง ให้บอกสั้นๆ ว่ายังไม่มีข้อมูลเรื่องนี้)"}
def plain_turn(messages):
    """The last round's view of the turn: tool calls dropped and their results put in the user's words, so DeepSeek has
    no call to copy. With the calls kept it wrote one more as DSML text, offered tools or not (tool_choice "none"
    too), and with nothing to run the turn ended on CUT_ALL: replay 2026-11-08, 3 of 10 POS asks."""
    out = []
    for m in messages:
        if m["role"] == "tool":
            m = {"role": "user", "content": f"(ผลที่ลูกทีมค้นให้: {m['content']})"}
        elif m.get("tool_calls"):
            if not m["content"]:  # a bare call says nothing: the results follow the question itself
                continue
            m = {"role": "assistant", "content": m["content"]}
        if out and out[-1]["role"] == m["role"] == "user":  # one user message, as the chat template expects
            m = {"role": "user", "content": f"{out.pop()['content']}\n{m['content']}"}
        out.append(m)
    return out


BRAIN_DOWN_BODY = "ตอนนี้ระบบตอบไม่ได้ชั่วคราว รบกวนถามใหม่อีกครั้งนะครับ"
BRAIN_DOWN = f"ขอโทษครับ {BRAIN_DOWN_BODY}"
# the secretary's own fixed lines: words from its greeting or status talk are no new claim
OWN_WORDS = "\n".join([
    SAY_WORDS,
    BRAIN_DOWN,
    "เวอร์ชันล่าสุดคือบีซีเอไอแอคเคาท์ ถามเจาร์วิสได้ในเวอร์ชันใหม่ ถ้าเป็นบีซีแอคเคาท์เวอร์ชันเก่าให้ฝากเบอร์โทรกลับ เดี๋ยวเจ้าหน้าที่ติดต่อกลับ",
    "เพื่อความปลอดภัย เจาร์วิสไม่ได้เก็บหรือเข้าถึงรหัสผ่านส่วนตัวครับ เรื่องดูดวงผมดูไม่เป็นครับ แต่ถ้าดูภาพรวมธุรกิจหรือกระแสเงินสดในบีซีเอไอแอคเคาท์ช่วยดูให้ได้เต็มที่เลยครับ",
    *FILLER_TEXT.values(),
])
# talk about the talk itself (log 2026-11-09..10: 44 sentences cut by the judge, half of them "ช่วยสะกดหรือบอกอีกนิด
# ได้ไหมครับ", "ขอโทษที่เข้าใจคลาดไป", "ได้ยินชัดครับ"): no product, offer, person, place or time word may go in here.
# These shrink the new-word list only: in the judge's SOURCES they flipped small talk (A/B 2026-11-10: 3/5 -> 2/5)
TALK_WORDS = ("หมายถึง ความหมาย เข้าใจ คลาด อธิบาย สะกด อีกครั้ง ซ้ำ เล่าเพิ่ม อีกนิด สั้นๆ ได้ยิน ชัด อ๋อ งั้น ไม่เจอ ไม่พบ ไม่มีข้อมูล รู้เรื่อง "
              "ใกล้เคียง ประเด็น ส่วน "
              "เขียน บน จอ หน้าจอ ตาราง แสดง สรุป เปรียบเทียบ เทียบ ไล่ ทีละ บรรทัด แถว หัวข้อ รายการ ตรงๆ โดยตรง เฉพาะ ทั้งก้อน")
ABOUT_ASK = "\nถ้าถูกถามว่าเป็นใครหรือทำอะไรได้ ให้ตอบว่า "  # the public prompt's last line: its self-description


def setup_facts(prompt):
    """What load_workers said is true of this line, for the judge's sources: the who-line stays out (as a source it
    flipped small talk 3/5 -> 2/5, A/B 2026-11-10); judge() uses it for the new-word list only."""
    return prompt.removeprefix(BASE_PROMPT).partition(ABOUT_ASK)[0]


def not_talk(piece, tail, new):
    """The new words TALK_ASK does not call talk. No answer keeps them all: every word then goes to the sourced ask."""
    try:
        r = llm.chat(model=C.JUDGE_MODEL, messages=[{"role": "user", "content": TALK_ASK.format(before=tail, piece=piece, new=", ".join(new))}],
                     format=judge_format(new, ("talk", "claim")))
        typed = json.loads(r.message.content)["words"]
    except Exception as e:
        log.warning("talk ask failed (%s)", type(e).__name__)
        return new
    claim = {w["word"] for w in typed if w["type"] != "talk"}
    return [w for w in new if w in claim or all(t["word"] != w for t in typed)]  # a word the answer skipped stays


def judge(sources, piece, before):
    """ห้ามแต่งเรื่อง: True only if `piece` states nothing the sources lack (numbers have their own check).
    On phone calls (Ultra-Fast mode): skip expensive extra LLM judge when translating to non-Thai or for short conversational turns without numbers."""
    if "on_phone" in globals() and on_phone():
        cid = call_id() if "call_id" in globals() else ""
        if (CALL_LANG.get(cid) and CALL_LANG.get(cid) != "th") or (not re.search(r"\d", piece) and len(piece) <= 120):
            return True
    if not (new := grounding.new_words(piece, f"{sources}\n{before}\n{TALK_WORDS}\n{PUBLIC['prompt'].partition(ABOUT_ASK)[2]}")):
        return True  # every word is a source word: no model call, so most phrases cost nothing
    low = sources.lower()  # a claimed source word must really be there (code checks the model)
    tail = (f"{C.SAMPLE_LABEL}\n" if C.SAMPLE_LABEL.split()[0] in before and C.SAMPLE_LABEL.split()[0] not in before[-600:] else "") + (before[-600:] or "-")
    if ASKS.search(piece) and not (new := not_talk(piece, tail, new)):
        return True

    def verdict(src):
        # 13 JSONDecodeErrors in the call log, 4 of 8 eval runs (run 7 cut a true ถัวเฉลี่ย line). Cause (spy 2026-11-10,
        # 4 of 4): done_reason=length, prompt 4061-4091 of the judge's 4096-token context, 5-35 tokens left for the JSON;
        # 6000 chars of dense Thai (JUDGE_MAX_CHARS) can fill it, and the same prompt again broke the same way 4/4.
        # So the retry reads the nearer half of the source. ponytail: the far half is unread on the retry (a would-be
        # cut either way). On DeepSeek (2026-11-19) the context holds it all; the retry stays for a broken or failed reply
        for attempt in (1, 2):
            ask = JUDGE_ASK.format(src=src if attempt == 1 else src[len(src) // 2:], before=tail,
                                   piece=piece, new=", ".join(new))
            r = None
            try:
                r = llm.chat(model=C.JUDGE_MODEL, messages=[{"role": "user", "content": ask}], format=judge_format(new))
                words = json.loads(r.message.content)["words"]
                break
            except Exception as e:  # no verdict is no check: "ไม่เจอ" beats a guess
                out = getattr(getattr(r, "message", None), "content", None)
                log.warning("judge failed (%s) try %d/2, done_reason=%s content=%r", type(e).__name__, attempt,
                            getattr(r, "done_reason", None), masked(out[:80]) if out else None)
        else:
            return False  # both tries failed: treating the piece as unsourced
        return bool(words) and all(w["type"] == "talk" or (w["source_word"].strip() and w["source_word"].strip().lower() in low)
                                   for w in words)
    # a long turn's right note can sit before the tail: a would-be cut gets one more look at the source lines nearest
    # the piece. ponytail: cut candidates only, so the common path keeps DeepSeek's cached prefix (one more judge call per cut)
    return verdict(sources[-C.JUDGE_MAX_CHARS:]) or (
        len(sources) > C.JUDGE_MAX_CHARS and verdict(grounding.near_sources(piece, sources, C.JUDGE_MAX_CHARS)))


def worker_chat(messages, tools):
    """A worker's own brain call, on DeepSeek like the boss: (text, calls)."""
    calls = []
    return "".join(deepseek_tokens(messages, calls, tools)), calls


# ลุงจืด 2026-11-08 "ต้องจัดคำถามให้เหมาะก่อนค้นหา เพราะบางครั้งก็สะกดผิด หรือคนถามสับสน": the caller's raw words found
# little (STT wrote FIFO as "ไฟโฟ"; "การคำนวณต้นทุนล่ะ" glued to the question before it found depreciation notes)
# first wording (2026-11-08 check) dragged the last topic into a new question and added terms not asked (FIFO to a lot question)
SEARCH_ASK = (f"{C.EXPERT}เขียนคำค้นคลังความรู้โปรแกรมบัญชี BC สำหรับคำพูดล่าสุดของผู้โทร บรรทัดแรกเป็นคำค้น 2-6 คำ (ถ้าคำถามมีหลายแง่มุมในเรื่องเดียวกัน คั่นคำค้นเพิ่มอีก 1-2 มุมด้วยเครื่องหมาย ; เช่น ลูกหนี้เจ้าหนี้คนเดียวกัน หักกลบ ; รายงานสถานะลูกหนี้เจ้าหนี้) ไม่ต้องอธิบาย "
              "ดูบริบทที่คุยต่อเนื่องเสมอและเดาแก้คำที่ระบบฟังเสียงเพี้ยนตามเสียงใกล้เคียงให้ตรงเรื่องเดิม (เช่น รอบก่อนเพิ่งคุยเรื่องรายงาน แล้วได้ยิน แรงงานอยู่ไหน -> รายงานจัดซื้อ อยู่เมนูไหน ; เพิ่งคุยเรื่องรับของเข้าคลัง แล้วได้ยิน ฟังสินค้า/ต้อนรับของ -> รับของเข้าคลัง ; ไฟโฟ -> FIFO, พีโอเอส -> POS, หลอด/รถ -> LOT ล็อต, คิวรี่/Q -> query SQL) คำที่คนไทยพูดทับศัพท์ให้ใส่ทั้งคำไทยและคำอังกฤษ "
              "ถ้าคำพูดล่าสุดยังถามต่อในเรื่องเดิมหรือพูดคำสั้นๆ ที่อยู่ในหัวข้อที่กำลังคุยได้ (เช่น คุยเรื่องต้นทุนสินค้าอยู่แล้วถามว่า \"FIFO?\", \"แล้วยกเลิกล่ะ\", \"รายงานอยู่ไหน\", \"ขอ query\") ให้คงหัวข้อเรื่องที่กำลังคุยอยู่ไว้ในคำค้นด้วย "
              "ถ้าเปลี่ยนไปถามระบบหรือเรื่องอื่นชัดเจน ใช้เรื่องใหม่อย่างเดียว ห้ามเอาเรื่องก่อนมาปน และห้ามใส่คำอื่นที่ผู้โทรไม่ได้พูดถึง "
              "ถ้าคำพูดล่าสุดถามหรือขอเรื่องอื่นที่ไม่ใช่เรื่องที่คุยอยู่ (ระบบอื่นของโปรแกรมเดียวกันก็นับเป็นเรื่องอื่น เช่น คุยเรื่องต้นทุนสินค้าอยู่แล้วถามเรื่องระบบเงินเดือนพนักงาน) "
              "และผู้โทรไม่ได้พูดเองว่าขอเปลี่ยนเรื่อง (อาจเพราะระบบฟังเสียงผิด) ต้องเพิ่มบรรทัดที่สองว่า เปลี่ยนเรื่อง: เรื่องที่คุยอยู่ | เรื่องใหม่ที่ได้ยิน "
              "ไม่ต้องเพิ่มเมื่อเป็นคำทัก คุยเล่น ขอบคุณ คำตอบที่เลขาถาม หรือคำถามต่อในเรื่องเดิม")


# the topic ask is a fixed line (reply), so the next turn reads its two topics back
TOPIC_ASK = re.compile(r"จะเปลี่ยนไปคุยเรื่อง(.+?) หรือยังคุยเรื่อง(.+?)ต่อครับ")
CALL_START = "(ระบบ: มีสายเข้า"  # how greet() opens the turn of a new call in the history


def swarm_queries(query, heard, history):
    """Split a Swarm planner line ('q1 | q2 | q3') into distinct parallel search angles (up to 3)."""
    if query:
        parts = [" ".join(p.split())[:120] for p in re.split(r"[;|]", query)]
        if out := list(dict.fromkeys(p for p in parts if p))[:3]:
            return out
    last = next((m["content"].splitlines()[0] for m in reversed(history) if m["role"] == "user" and m["content"]), "")
    raw = f"{last} {heard}" if len(heard) < 20 and last and not last.startswith("(") else heard
    return [" ".join([raw, *grounding.acronyms(raw)]).strip()[:150]]


_FOLLOWUP_START_RE = re.compile(r"^(?:แล้ว|อันนี้|อันนั้น|ตัวนี้|ตัวนั้น|มัน|แบบนั้น|ถ้าอย่างนั้น|ถ้าแบบ|งั้น|ส่วน)")
_VAGUE_FOLLOWUP_RE = re.compile(
    r"^(?:คำนวณ|คิด|ทำ|ใช้|ดู|เข้า|ตั้ง|แก้|เพิ่ม|ลบ)(?:ยังไง|อย่างไร|ตรงไหน|เมนูไหน|ได้ไหม|ได้มั้ย)"
    r"|(?:อยู่ไหน|อยู่ที่ไหน|เมนูไหน|อยู่ตรงไหน|ทำยังไง|คิดยังไง)[\s.!?…]*$"
)


def _stt_ears_diverge(heard: str, alt_heard: list[str]) -> bool:
    """True when primary STT and secondary OpenThai ear heard meaningfully different words."""
    if not heard or not alt_heard:
        return False
    a = re.sub(r"[\s.,!?…]+", "", heard).lower()
    b = re.sub(r"[\s.,!?…]+", "", alt_heard[0]).lower()
    if not a or not b or a == b or a in b or b in a:
        return False
    # Character bigram Jaccard similarity: < 0.35 means the ears heard genuinely different phrases
    sa = {a[i : i + 2] for i in range(max(len(a) - 1, 1))}
    sb = {b[i : i + 2] for i in range(max(len(b) - 1, 1))}
    return len(sa & sb) / max(len(sa | sb), 1) < 0.35


_PHONE_INTENT_HINTS = (
    (re.compile(r"(?:ขนส่ง|เดลิเวอรี|ช้อปปี้|ส่งของ|รถส่ง|จัดส่ง)"), "ระบบบริหารขนส่ง TMS e-POD ใบส่งของ จัดส่งสินค้า"),
    (re.compile(r"(?:หลัง(?:จาก(?:ที่)?)?(?:ได้)?(?:ตกลง)?(?:รับ|ซื้อ|ใช้|สมัคร)โปรแกรม|เริ่ม(?:ต้น)?ใช้งาน|เตรียม(?:ตัว|ข้อมูล).*(?:บริษัท|ร้าน|โปรแกรม)|วันแรก)"), "วันแรกที่เริ่มใช้ BC Ai Account เช็กลิสต์ตั้งค่า เตรียมข้อมูล"),
    (re.compile(r"(?:ฟรี(?:ศูนย์|0)?บาท.*(?:ได้เงิน|กำไร|รายได้|ยังไง|ทำไม|ค่าใช้จ่าย)|Starter.*(?:ฟรี|จำกัด|ต่าง))", re.I), "ตารางราคาและแพ็กเกจ BC Ai Account 4 รุ่น Starter ฟรี 0 บาท เงื่อนไข"),
    (re.compile(r"(?:ส่วนลดรับ|ปัดเศษ|เศษสตางค์|ช่องส่วนลด.*จ่าย|จ่าย(?:ชำระ)?เจ้าหนี้.*ส่วนลด|ค้างไม่ตรงบิล)"), "จ่ายชำระหนี้ เจ้าหนี้ ส่วนลดรับ ปัดเศษ ยอดค้างไม่ตรงบิล วิธีบันทึก"),
    (re.compile(r"(?:สต(?:็อก|๊อก|อก|็อค|อค).*(?:ผิด|ไม่ตรง|เพี้ยน|ติดลบ)|ต้นทุน.*(?:ผิด|ไม่ตรง|เพี้ยน))"), "สต็อกไม่ตรง ต้นทุนผิด คำนวณยอดคงเหลือใหม่ เช็กสต็อก"),
    (re.compile(r"(?:แจ้งเตือน|เตือน).*(?:ถึงกำหนด|ครบกำหนด|ถึกดีล|ถึงดีล|ถึงดิว|เก็บเงิน|จ่ายเงิน|ลูกหนี้|เจ้าหนี้)|(?:ถึกดีล|ถึงดีล|ถึงดิว).*(?:เก็บเงิน|จ่ายเงิน)"), "ศูนย์แจ้งเตือนยามเช้า 1001 ลูกหนี้เกินกำหนดชำระ เจ้าหนี้ครบกำหนด อายุหนี้"),
    (re.compile(r"(?:(?:มี|หลาย|อีก)\s*สาขา|แยกสาขา).*(?:คลาวด์|คราว|คาว|cloud|นิติ|ยอดขาย|บิล|ออกบิน)|(?:คลาวด์|คราวก|บนคราว|เรื่องคราว|on.?cloud).*(?:สาขา|นิติ)", re.I), "ตั้งค่าสาขา 1007 แยกยอดขาย กำไรขาดทุนแยกสาขา 9063 Private Cloud หลายนิติบุคคล"),
    (re.compile(r"(?:สรุป|แยก).*ยอดขาย.*สาขา|(?:ออกบิล|ออกบิน).*สรุป.*ยอดขาย"), "รายงานสรุปยอดขาย 4052 รายงานการขาย ตั้งค่าสาขา 1007 แยกยอดขายตามสาขา"),
)


def _expand_phone_intent_queries(heard: str) -> list[str]:
    """0ms regex intent expander mapping conversational phrasings to canonical BC Brain search keywords."""
    s = (heard or "").strip()
    if not s:
        return []
    hints: list[str] = []
    for pattern, expanded in _PHONE_INTENT_HINTS:
        if pattern.search(s) and expanded not in hints:
            hints.append(expanded)
    return hints[:2]


def _needs_search_planner(heard: str, alt_heard: list[str], history: list[dict]) -> bool:
    """Tier-2 Fast-Path vs Tier-3 AI Search Planner:
    Run search_words before quick_answer when ears strongly disagree, utterance is vague/follow-up, or long/rambling."""
    s = (heard or "").strip()
    if not s:
        return False
    if _stt_ears_diverge(s, alt_heard):
        return True
    if len(s) > 78:
        return True
    if history and (len(s) < 13 or bool(_FOLLOWUP_START_RE.match(s)) or (len(s) <= 18 and bool(_VAGUE_FOLLOWUP_RE.search(s)))):
        return True
    return False


def words_ask(history, heard, alt_heard=None):
    """(the talk so far, the messages) of search_words: built in one place, so an ask made early is the very same one."""
    talk = [f"{'ผู้โทร' if m['role'] == 'user' else 'เลขา'}: {m['content'].partition(C.SCREEN_MARK)[0].strip()[:150]}"
            for m in history[-4:] if m["content"] and not m["content"].startswith(("(", C.SILENT_MARK))]
    alts = [t.strip() for t in (alt_heard or ()) if t and t.strip()]
    latest = f"ผู้โทร (ล่าสุด): {heard}" + (f" (อีกหูฟังได้ยิน: {' | '.join(alts[:2])})" if alts else "")
    return talk, [{"role": "system", "content": SEARCH_ASK},
                  {"role": "user", "content": "\n".join([*talk, latest])}]


_INCOMPLETE_TAIL_RE = re.compile(
    r"(?:อยากจะรู้ว่า|อยากรู้ว่า|หมายถึงว่า|แต่ว่า|หรือว่า|ถ้าเกิด|พอดี|เพราะว่า|ถ้ามี|ถ้าเป็น|แล้วถ้า|"
    r"ว่า|แล้ว|ที่|และ|กับ|ถ้า|คือ|แต่|หรือ|เป็น|จะ|ก็|แบบ|ซึ่ง|เรื่อง|ของ|ใน|จาก|เพื่อ)[\s.!?…]*$"
)


def _is_incomplete_thai_tail(text: str) -> bool:
    """True when a live Thai partial clearly ends mid-clause on a trailing connector/conditional opener."""
    s = (text or "").strip()
    if len(s) < 4:
        return False
    if re.search(r"(?:ครับ|ค่ะ|คะ|ไหม|มั้ย|เปล่า|ไหน|เท่าไหร่|ยังไง|อย่างไร|บ้าง)[\s.!?…]*$", s):
        return False
    if re.match(r"^(?:แล้ว)?ถ้า(?:เป็น|มี)\s*", s) and len(s) <= 38:
        if not re.search(r"(?:ครับ|ค่ะ|คะ|ไหม|มั้ย|เปล่า|ไหน|เท่าไหร่|ยังไง|อย่างไร|บ้าง|อะไร|ได้|หรือ)[\s.!?…]*$", s):
            return True
    return bool(_INCOMPLETE_TAIL_RE.search(s))


_STANDALONE_TOPIC_RE = re.compile(
    r"(?:เบอร์โทร|ติดต่อ|เว็บไซต์|ไลน์|ราคา|แพ็กเกจ|ติดตั้ง|คลาวด์|ออฟฟิศ|เวอร์ชัน|เวอร์ชั่น|รุ่น|"
    r"มาคุยเรื่อง|เปลี่ยนเรื่อง|ถามเรื่อง|ถ้าเป็นธุรกิจ|ธุรกิจ|ร้านผม|ร้านยาง|รายงานสินค้า|เงินสด|ธนาคาร|คลังสินค้า|สต็อก|สต๊อก|โลเคชั่น|"
    r"โปรโมชั[น่]น?|promotion|พักบิล|พักบิน|บาร์โค้ด|ต้นทุน|fifo|lifo|fefo|pos|พีโอเอส|สาขา|ส่วนลด|คูปอง|พนักงานโทร|โทรหาผม|ติดต่อกลับ)",
    re.I,
)


def _phone_grounding_numbers(phone: str) -> set[str]:
    """Return full, local-0, and standard Thai hyphenated chunk digit strings for a caller phone so grounding never cuts it."""
    digits = re.sub(r"\D", "", str(phone or ""))
    if not digits:
        return set()
    forms = {digits}
    if digits.startswith("66") and len(digits) >= 10:
        forms.add("0" + digits[2:])
    out = set()
    for f in forms:
        if len(f) >= 2:
            out.add(f)
        if len(f) == 10 and f.startswith("0"):
            out.update((f[:3], f[3:6], f[6:], f[3:], f[:6], f[:4], f[4:7], f[7:]))
        elif len(f) == 9 and f.startswith("02"):
            out.update((f[:2], f[2:5], f[5:], f[:5], f[2:]))
        elif len(f) == 9:
            out.update((f[:3], f[3:6], f[6:], f[2:5], f[5:]))
    return {x for x in out if len(x) >= 2}


_NON_SEARCH_STRIP_RE = re.compile(
    r"(?:สวัสดี|หวัดดี|ฮัลโหล|ครับผม|นะครับ|นะคะ|ครับ|ค่ะ|คะ|คับ|ผมชื่อว่า|ดิฉันชื่อว่า|หนูชื่อว่า|ฉันชื่อว่า|พี่ชื่อว่า|"
    r"ผมชื่อ|ดิฉันชื่อ|หนูชื่อ|ฉันชื่อ|พี่ชื่อ|เรียกผมว่า|เรียกพี่ว่า|เรียกว่า|ชื่อเล่นว่า|ชื่อเล่น|ชื่อว่า|ชื่อ|คุณ|พี่|ท่าน)"
)
_CB_INTENT_ONLY_RE = re.compile(r"(?:ฝากเบอร์|โทรกลับ|ติดต่อกลับ|คุยกับพนักงาน|ขอพบพนักงาน|พนักงานโทร(?:หา|กลับ)|ให้พนักงานโทร|โทรหาผม|เบอร์นี้|เบอร์เดิม|ใช่เบอร์นี้)")
_REAL_Q_OR_PRODUCT_RE = re.compile(
    r"(?:ราคาเท่าไหร่|ราคา|กี่บาท|แพ็กเกจ|รุ่น|เวอร์ชัน|เวอร์ชั่น|ทำได้ไหม|ทำได้มั้ย|ยังไง|อย่างไร|เมนูไหน|คืออะไร|ต่างกัน|"
    r"เว็บไซต์|เว็บไหน|เบอร์อะไร|ติดต่อที่ไหน|สินค้า|สต็อก|สต๊อก|บัญชี|ภาษี|รายงาน|ติดตั้ง|คลาวด์|ออฟฟิศ|ลองใช้|เดโม|โปรแกรม|ระบบ)"
)
_CALL_META_ASK_RE = re.compile(
    r"(?:โทร(?:หา|มา|เข้า).*กี่(?:รอบ|ครั้ง)|รวมแล้วโทรกี่ครั้ง|จำได้(?:ไหม|หรือเปล่า|รึเปล่า).*(?:กี่รอบ|กี่ครั้ง|คุยอะไร)|"
    r"(?:เคย)?คุยอะไรกัน(?:มา)?บ้าง|เบอร์(?:ที่)?(?:ผม|ฉัน|หนู)?โทร(?:เข้า)?มา)"
)
_CLOSING_ONLY_RE = re.compile(r"(?:ไม่มีอะไรแล้ว|แค่นี้(?:แหละ|ก่อน)|ขอบคุณ(?:มาก)?(?:นะ)?(?:ครับ|ค่ะ|คะ).*ไม่มีอะไร)")
_BOILERPLATE_CTX_RE = re.compile(
    r"(?:เจาร์วิสครับ|เวอร์ชันล่าสุดคือบีซีเอไอแอคเคาท์|ผู้ช่วยเอไอของบีซีคอลเซ็นเตอร์|ยินดีที่ได้คุยกันอีกครั้ง|"
    r"ยังไม่มีข้อมูลยืนยัน|ไม่มีข้อมูลยืนยัน|สัญญาณไม่ค่อยชัด|ขอฟังอีกครั้ง|มีอะไรอยากรู้เพิ่ม|ขอบคุณที่โทรมานะครับ)"
)


def _extract_recent_topic(history: list[dict]) -> str:
    """Return the most recent substantive user topic snippet (preferring caller turns over verbose bot replies)."""
    recent = list(reversed((history or [])[-8:]))
    for prefer_user in (True, False):
        for m in recent:
            if prefer_user and m.get("role") != "user":
                continue
            if not prefer_user and m.get("role") == "user":
                continue
            c = (m.get("content") or "").partition(C.SCREEN_MARK)[0].strip()
            if not c or c.startswith(("(", C.SILENT_MARK)) or len(c) < 6:
                continue
            if _BOILERPLATE_CTX_RE.search(c):
                continue
            if re.fullmatch(r"(?:สวัสดี|ครับผม|นะครับ|นะคะ|ครับ|ค่ะ|คะ|อืม|ใช่|ใช่ค่ะ|ใช่ครับ|โอเค|คุยต่อ|อธิบายต่อเลย|ไม่เป็นอย่างงั้น|ไม่เป็นอย่างนั้น)[\s.,!?…]*", c):
                continue
            if prefer_user and re.fullmatch(r"(?:ไม่เป็น|อย่างงั้น|อย่างนั้น|คุยต่อ|อธิบายต่อ|ทำไม|สรุป|อ๋อ|ครับ|ค่ะ|\s)+", c):
                continue
            return c[:60]
    return ""


def _is_non_search_phone_turn(heard: str, detected_name: str, history: list[dict]) -> bool:
    """True when phone turn is pure name intro, callback confirm/request, caller-meta question, or closing."""
    s = (heard or "").strip()
    if not s:
        return False
    if detected_name:
        rem = s.replace(detected_name, " ")
        rem = _NON_SEARCH_STRIP_RE.sub(" ", rem)
        rem = re.sub(r"[\s.,!?…]+", "", rem)
        if len(rem) < 4:
            return True
    prev_bot = history[-1].get("content", "") if history and history[-1].get("role") == "assistant" else ""
    prev_asked_cb = bool(re.search(r"เบอร์นี้|เบอร์อื่น|ติดต่อกลับเรื่องอะไร", prev_bot))
    if prev_asked_cb and re.search(r"(?:ใช่|เบอร์นี้|เบอร์เดิม|ฝากเบอร์|โทรกลับ|ติดต่อกลับ)", s):
        if not re.search(r"(?:ราคาเท่าไหร่|กี่บาท|ทำได้ไหม|ยังไง|อย่างไร|เมนูไหน|คืออะไร|ต่างกัน|เว็บไหน|เบอร์อะไร)", s):
            return True
    if _CALL_META_ASK_RE.search(s) and not re.search(r"(?:ติดต่อ|บริษัท|ออฟฟิศ|ราคา|แพ็กเกจ|รุ่น|เวอร์ชัน|ทำได้ไหม|เมนูไหน|โปรแกรม|ระบบ|สต็อก|บัญชี|ภาษี|รายงาน|คลาวด์)", s):
        return True
    if _CLOSING_ONLY_RE.search(s) and not _REAL_Q_OR_PRODUCT_RE.search(s):
        return True
    if _CB_INTENT_ONLY_RE.search(s) and not _REAL_Q_OR_PRODUCT_RE.search(s):
        return True
    return False


_EXPLICIT_TOPIC_SWITCH_RE = re.compile(
    r"(?:เปลี่ยนเรื่อง|เรื่องอื่น|ถามเรื่องอื่น|อีกเรื่อง(?:นึง|หนึ่ง)?|ขอถามอีกเรื่อง|มาคุยเรื่อง|หันมาคุยเรื่อง|ย้ายไปเรื่อง)"
)
_CONTINUATION_HINT_RE = re.compile(
    r"(?:ต่อเลย|เล่าต่อ|อธิบายต่อ|คุยต่อ|แล้วยังไง|แล้วต้อง|แล้วถ้า|แล้วมัน|มันทำ|อันนั้น|อันนี้|เรื่องนี้|เรื่องเดิม|"
    r"กี่แบบ|กี่สูตร|กี่วิธี|ตั้งค่ายังไง|ตั้งค่าตรงไหน|ทำยังไง|กดตรงไหน|เข้าเมนูไหน|รายละเอียด|ตัวอย่าง|เพิ่มเติม)"
)
_BRAIN_MISS_LOG_PATH = os.path.join(os.path.dirname(C.BASE_DIR), "data", "brain-miss-log.jsonl")
_BRAIN_MISS_LOCK = threading.Lock()


def _record_brain_miss(
    query: str,
    *,
    ctx_topic: str = "",
    reason: str = "abstain",
    top_slug: str = "",
    top_cosine: float = 0.0,
    lang: str = "th",
) -> None:
    """Append sanitized missed/abstained customer query to data/brain-miss-log.jsonl for continuous brain improvement."""
    q_clean = re.sub(r"\b0[689]\d[- ]?\d{3}[- ]?\d{4}\b", "[phone]", str(query or "")).strip()
    q_clean = LONG_NUMBER.sub("[num]", q_clean).strip()
    if len(q_clean) < 5:
        return
    if re.fullmatch(r"(?:สวัสดี|ครับ|ค่ะ|คะ|อืม|ใช่|โอเค|ขอบคุณ|ไม่เป็นอย่างงั้น|คุยต่อ|\s)+", q_clean):
        return
    ctx_clean = re.sub(r"\b0[689]\d[- ]?\d{3}[- ]?\d{4}\b", "[phone]", str(ctx_topic or "")).strip()[:80]
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "query": q_clean[:180],
        "ctxTopic": ctx_clean,
        "reason": str(reason or "abstain")[:40],
        "topSlug": str(top_slug or "")[:80],
        "topCosine": round(float(top_cosine or 0.0), 3),
        "lang": str(lang or "th")[:8],
    }
    try:
        os.makedirs(os.path.dirname(_BRAIN_MISS_LOG_PATH), exist_ok=True)
        with _BRAIN_MISS_LOCK:
            with open(_BRAIN_MISS_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _is_explicit_topic_switch(heard: str) -> bool:
    s = (heard or "").strip()
    if not s:
        return False
    if _EXPLICIT_TOPIC_SWITCH_RE.search(s):
        return True
    return len(s) >= 14 and bool(_STANDALONE_TOPIC_RE.search(s))


def _build_topic_pin_hint(heard: str, history: list[dict]) -> str:
    """Build an explicit system pin hint so the LLM stays strictly on the current topic until the caller switches."""
    if not history:
        return ""
    s = (heard or "").strip()
    if not s or _is_explicit_topic_switch(s):
        return ""
    ctx = _extract_recent_topic(history)
    if not ctx:
        return ""
    return (
        f"กฎต่อเนื่องหัวข้อเดิม: ตอนนี้กำลังคุยต่อเนื่องเรื่อง \"{ctx}\" อยู่ "
        "ห้ามตอบข้ามไปเรื่องอื่นหรือโมดูลอื่นที่ลูกค้าไม่ได้ถาม ให้ตอบต่อเนื่องในเรื่องเดิมนี้จนกว่าลูกค้าจะเปลี่ยนเรื่องชัดเจน "
    )


def _build_phone_fallback_queries(heard: str, alt_heard: list[str], history: list[dict]) -> list[str]:
    """Zero-latency (0ms) multi-ear + topic-pinned context query builder when early search_words is absent or skipped."""
    base = (heard or "").strip()
    if not base:
        return []
    # Pin recent topic context unless the caller explicitly switched to a standalone new topic
    ctx_anchor = ""
    sw_fn = globals().get("_is_explicit_topic_switch")
    topic_switched = bool(sw_fn(base)) if callable(sw_fn) else (len(base) >= 14 and bool(_STANDALONE_TOPIC_RE.search(base)))
    cont_re = globals().get("_CONTINUATION_HINT_RE")
    wants_continuity = (
        len(base) <= 36
        or bool(_FOLLOWUP_START_RE.match(base))
        or bool(_VAGUE_FOLLOWUP_RE.search(base))
        or bool(cont_re and cont_re.search(base))
    )
    if history and not topic_switched and wants_continuity:
        ctx_anchor = _extract_recent_topic(history)
    queries: list[str] = []
    q_primary = f"{base} {ctx_anchor}".strip()[:120] if ctx_anchor and ctx_anchor not in base else base[:120]
    queries.append(q_primary)
    # If secondary OpenThai ear heard a different word (e.g. e1="แรงงาน" vs e2="รายงาน"), search it in parallel (~52ms)
    for alt in (alt_heard or ())[:1]:
        a = (alt or "").strip()
        if a and a != base and _stt_ears_diverge(base, [a]):
            q_alt = f"{a} {ctx_anchor}".strip()[:120] if ctx_anchor and ctx_anchor not in a else a[:120]
            if q_alt not in queries:
                queries.append(q_alt)
    for hint in _expand_phone_intent_queries(base):
        if hint not in queries:
            queries.append(hint)
    return queries[:3]


def search_words(history, heard, alt_heard=None, allow_sync=True):
    """The caller's words made into a search: misheard words fixed, Thai and English terms, a follow-up made whole.
    Returns (words, jump): jump is (the topic so far, the new one heard) when the words leave the topic unasked.
    ponytail: one more DeepSeek call before the first word; "" on failure, and the raw words are searched"""
    talk, ask = words_ask(history, heard, alt_heard=alt_heard)
    try:
        early, t = WORDS.pop(ask[1]["content"], None), time.perf_counter()  # words_early asked this already, before the turn ended
        if early is None and not allow_sync:
            return "", None
        phone_now = "on_phone" in globals() and on_phone()
        job = early or (WORDS_POOL.submit(worker_chat, ask, None) if phone_now else None)
        if early:
            text, _ = early.result(timeout=0.05 if phone_now else None)
        elif job is not None:
            text, _ = job.result(timeout=1.15)
        else:
            text, _ = worker_chat(ask, None)
        log.info("search words: %s, waited %.2fs", "asked early" if early else "asked now", time.perf_counter() - t)
    except (httpx.HTTPError, ValueError, KeyError, TimeoutError) as e:
        log.warning("search words skipped (%s)", type(e).__name__)
        return "", None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    # real calls (log 2026-11-09): the jump line came first, or a reply sentence came instead of a query, and that
    # was searched. A line that ends like speech is no query: "" and the raw words are searched
    first = next((line for line in lines if not line.startswith("เปลี่ยนเรื่อง")), "")
    first = "" if re.search(r"(?:ครับ|ค่ะ|คะ)[\s.!?…]*$", first) else first
    jump = next(([t.strip() for t in line.partition(":")[2].split("|")] for line in lines if line.startswith("เปลี่ยนเรื่อง")), [])
    # a first question has no topic to leave, and neither have the first words of a later call: the history keeps the
    # call before, and its topic was asked about on 13 first questions (log to 2026-11-18). All 13 callers went on
    # with what they had asked, e.g. the price right after the hello had offered the old topic (2026-11-17 11:05)
    hello = max((i for i, m in enumerate(history) if (m["content"] or "").startswith(CALL_START)), default=-1)
    spoke = any(t.startswith("ผู้โทร:") for t in words_ask(history[hello + 1:], heard)[0])
    jump = tuple(jump) if len(jump) == 2 and all(jump) and spoke else None
    return " ".join(first.split())[:200], jump


# ลุงจืด 2026-11-18 "หาวิธีให้ตอบเร็ว แต่คุณภาพในคำตอบสูง": a searched turn waited PAUSE_SILENCE_S for the turn to end and
# only then asked for its search words (0.81 s median before the first search, log 2026-11-16..17). The early
# transcription is done 0.69 s after the last word (median of 18 real-time runs on the test audio, when the turn ended
# at 1.0 s), so the very same ask goes out then and search_words takes its answer; nothing else is done early, and the
# boss gets what it got before. With the turn ending at 0.7 s (ลุงจืด 2026-11-19) the ask is mostly still out when the
# turn ends: the log's "search words" line says how long search_words waited for it.
# ponytail: only while the secretary is silent (a speech is a turn then, and its words go to DeepSeek anyway), so a
# caller who cuts in gains nothing; a caller who stops and goes on costs one unused ask per stop
WORDS = {}  # the caller part of an ask made early -> its DeepSeek job
WORDS_POOL = ThreadPoolExecutor(8)


def words_early(heard, history, cid="", phone=False):
    """Asks for a speech's search words before its turn ends, unless these words make no searched turn."""
    bare = re.sub(r"[\s.,!?…]+", "", heard)
    if not bare or bare in BargeInReplyOnPause.HESITATIONS or FAST_CHAT.match(heard.strip()):
        return
    alt_heard = list(LAST_ALT_HEARD.get(cid) or ()) if cid else []
    if phone and not _needs_search_planner(heard, alt_heard, history):
        return  # clear phone turn uses Fast-Path (~52ms quick_answer directly)
    ask = words_ask(history, heard, alt_heard=alt_heard if phone else None)[1]
    if ask[1]["content"] not in WORDS:  # the same words again (the speech end moved a little): that ask is on its way
        WORDS[ask[1]["content"]] = WORDS_POOL.submit(worker_chat, ask, None)
    for old in list(WORDS)[:-8]:  # asks nobody took: the caller went on talking
        WORDS.pop(old, None)


def words_transcribed(job, history, cid="", phone=False):
    """Done-callback of an early transcription."""
    if not job.cancelled() and not job.exception():
        words_early(job.result(), history, cid=cid, phone=phone)


def load_workers():
    """Workers beyond search/docs/bcbrain: Google (once google_login.py has run) and the computer."""
    if google_tools.connected():
        TOOLS.update(google_tools.TOOLS)
        TOOL_SPECS.extend(google_tools.TOOL_SPECS)
        STATUS.update(google_tools.STATUS)
        WORKER.update({n: n.split("_")[0] for n in google_tools.TOOLS})  # gmail_*, calendar_*, drive_*
        C.SYSTEM_PROMPT += "\nอีเมล นัดหมาย ไฟล์ใน Drive ใช้เครื่องมือ gmail calendar drive "
    else:
        log.info("ยังไม่ได้เชื่อม Google (รัน google_login.py) -> ข้าม Gmail/Calendar/Drive")
        # set before warm_up, so the cached prompt prefix stays the same for every call
        C.SYSTEM_PROMPT += "\nตอนนี้ยังไม่ได้เชื่อม Gmail ปฏิทิน และ Google Drive ถ้าถูกถามเรื่องเหล่านี้ให้บอกว่ายังไม่ได้เชื่อม"
    if os.path.exists(desktop_agent.AD):
        TOOLS["desktop_task"] = lambda task: desktop_agent.desktop_task(task, worker_chat)
        TOOL_SPECS.append(tool_spec("desktop_task", "ส่งงานให้ลูกทีมดูหรือควบคุมคอมพิวเตอร์ เช่น ดูว่าเปิดโปรแกรมอะไรอยู่ "
                                    "อ่านข้อความในหน้าต่าง เปิดโปรแกรม กดปุ่ม (พิมพ์ข้อความไม่ได้)",
                                    one_arg("task", "งานที่ต้องทำบนคอมพิวเตอร์ เขียนให้ชัด")))
        STATUS["desktop_task"] = "ลูกทีมกำลังดูคอมพิวเตอร์"
        WORKER["desktop_task"] = "desktop"
        C.SYSTEM_PROMPT += "\nงานบนหน้าจอคอมพิวเตอร์ เช่น ดูว่าเปิดโปรแกรมอะไรอยู่ อ่านหน้าต่าง เปิดโปรแกรม กดปุ่ม ให้ส่ง desktop_task ถ้าลูกทีมรายงานว่ารอยืนยัน ให้บอกสั้นๆ ว่าจะทำอะไร แล้วถามว่ายืนยันไหม ให้พูดว่ายืนยัน "
    # the boss once listed an email worker that was not connected: tell it exactly who is on the team
    names = {"web": "ค้นเว็บ", "docs": "ค้นเอกสารในเครื่อง", "bcbrain": "คลังความรู้บีซี", "gmail": "อ่านอีเมล",
             "calendar": "ดูปฏิทิน", "drive": "อ่านไฟล์ใน Google Drive", "desktop": "ดูและกดปุ่มบนคอมพิวเตอร์",
             "calc": "เครื่องคิดเลข", "deepseek": "ความรู้ทั่วไป"}
    team = sorted({names.get(WORKER.get(n, n), WORKER.get(n, n)) for n in TOOLS})
    C.SYSTEM_PROMPT += f"\nลูกทีมที่มีจริงตอนนี้ {len(team)} ตัว: {', '.join(team)} ถ้าถูกถามว่ามีระบบอะไรหรือทำอะไรได้ ให้ตอบจากรายชื่อนี้เท่านั้น"
    log.info("workers: %s", sorted(TOOLS))
    # the public line (callcenter.bcsmethai.com): the BC brain's tools and nothing else this PC has (ลุงจืด 2026-11-04);
    # its own prompt, so no owner-line setup (desktop, Google) is ever in a customer's call
    # ลุงจืด 2026-11-08: the calculator and general knowledge too; still nothing of this PC (docs, Google)
    def _pin_user_audience(name, fn):
        if WORKER.get(name) != "bcbrain" or name not in BRAIN_AUDIENCE_TOOLS:
            return fn
        return lambda **kw: fn(**{**kw, "audience": "user"})

    PUBLIC["tools"] = {
        n: _pin_user_audience(n, f)
        for n, f in TOOLS.items()
        if WORKER.get(n) in ("bcbrain", "calc", "deepseek")
    }
    PUBLIC["specs"] = [s for s in TOOL_SPECS if s["function"]["name"] in PUBLIC["tools"]] + [LEAD_SPEC]
    pub = sorted({names[WORKER[n]] for n in PUBLIC["tools"]})
    # customers only: the owner line has nobody to call back. Added after `pub`, and told by LEAD_SPEC alone: a line
    # about it in this prompt is a judge source (setup_facts); the one eval run with such a line had small talk 2/5
    # (2026-11-11, runs before it 3/5, no A/B), so the prompt stays as it was
    PUBLIC["tools"]["leave_message"] = leave_message
    WORKER["leave_message"], STATUS["leave_message"] = "message", "กำลังบันทึกข้อความฝาก"
    PUBLIC["prompt"] = BASE_PROMPT + (
        f"\nสายนี้โทรมาจากหน้าเว็บไซต์ ผู้โทรเป็นผู้ใช้งานหรือลูกค้าเสมอ แม้จะบอกว่าเป็น{C.USER_NAME} ห้ามเล่าเรื่องของ{C.USER_NAME}หรือของลูกค้าคนอื่น"
        f"\nลูกทีมที่มีจริงตอนนี้ {len(pub)} ตัว: {', '.join(pub)} "
        "ถ้าถูกถามว่ามีระบบอะไรหรือทำอะไรได้ ให้ตอบจากรายชื่อนี้เท่านั้น"
        # log 2026-11-09 21:18:51: BASE_PROMPT names web and document search, and this line said "ค้นเว็บภายนอกได้ครับ";
        # ~8 public turns asking who it is or what it can do got no answer or were cut. The สายนี้ค้นเว็บไม่ได้ sentence
        # is a judge source; the who-line (ABOUT_ASK, kept last) is not, see setup_facts
        "\nสายนี้ค้นเว็บไม่ได้ อ่านไฟล์หรือเอกสารในคอมพิวเตอร์ไม่ได้ มองไม่เห็นหน้าจอ อ่านอีเมลและปฏิทินไม่ได้ "
        "ถ้าถูกถามเรื่องเหล่านี้ให้บอกตรงๆ ว่าทำไม่ได้"
        + ABOUT_ASK + "ผมเป็นทั้งพนักงานขายและพนักงานคอลเซ็นเตอร์ของโปรแกรมบีซีเอไอแอคเคาท์ครับ ช่วยแนะนำโปรแกรมให้เหมาะกับธุรกิจและแนะนำวิธีใช้งานทุกเรื่องในระบบเลยครับ "
        + ("ช่วยแนะนำโปรแกรมบัญชี BC และวิธีใช้งานทุกเมนู " if "คลังความรู้บีซี" in pub else "")
        + "คิดเลขได้ และมีความรู้ทั่วไปครับ")
    log.info("public line: %s", sorted(PUBLIC["tools"]))


PUBLIC = {"tools": {}, "specs": [], "prompt": ""}  # set by load_workers
JOB_IDS = itertools.count(1)


def shown(args):
    """What the page says a worker looks for: its words, not a search's switches."""
    return " ".join(str(v) for k, v in args.items() if k not in ("audience", "kind", "limit"))


def team_start(calls):
    """Team panel: a worker lights up when the boss hands it a job. Returns the job ids and their events."""
    ids = [next(JOB_IDS) for _ in calls]
    return ids, [AdditionalOutputs({"kind": "team", "id": i, "state": "run", "worker": WORKER.get(n, "other"),
                                    "task": shown(a)[:80]}) for i, (n, a) in zip(ids, calls)]


def team_done(job_id, cards, t0):  # "none": the worker came back without anything to show (not found / failed)
    return AdditionalOutputs({"kind": "team", "id": job_id, "state": "done" if cards else "none",
                              "secs": round(time.perf_counter() - t0, 1)})


def dispatch(calls, said, turn, tools):
    """The boss hands this round's jobs to the workers at once (searches and Google calls really overlap; LLM
    workers share the one GPU). Status, team panel, info cards; returns texts in call order."""
    if not calls:  # bcbrain down at startup: no search tools, nothing to prefetch (ThreadPoolExecutor(0) raises)
        return []
    log.info("TOOLS %s", masked(str(calls)))  # a phone read back is a tool argument too
    t0, results = time.perf_counter(), [None] * len(calls)
    ids, started = team_start(calls)
    pool = ThreadPoolExecutor(len(calls))
    try:
        futures = {pool.submit(run_tool, *c, tools): k for k, c in enumerate(calls)}  # start all workers at once
        jobs = " · ".join(f"{STATUS.get(n, 'กำลังตรวจสอบ')}: {shown(a)}" for n, a in calls)
        yield AdditionalOutputs({"kind": "status", "text": jobs[:200]})
        yield from started
        for f in as_completed(futures):  # each worker reports the moment it finishes
            k = futures[f]
            results[k] = f.result()
            yield team_done(ids[k], results[k][0], t0)
            for card in results[k][0]:
                turn["media"] = turn.get("media", 0) + len(card.get("media") or ())  # the prefetch asks find_media when none came
                yield AdditionalOutputs({"kind": "info", "job": ids[k], **card})  # job: the page shows each worker's finds
                if card.get("source") == "message" and isinstance(card.get("lead"), dict):
                    yield AdditionalOutputs({"kind": "callback_request", **card["lead"]})
    finally:  # a barge-in closes this generator: the with-block waited for every worker (MCP up to 20 s)
        pool.shutdown(wait=False, cancel_futures=True)
    return [text for _, text in results]


def reply(audio, public=False, hello=False):  # hello: greet() already cleared the screen and said it
    t0 = time.perf_counter()
    tools, specs, prompt = (PUBLIC["tools"], PUBLIC["specs"], PUBLIC["prompt"]) if public else (TOOLS, TOOL_SPECS, C.SYSTEM_PROMPT)
    sr, data = audio
    heard = early_words(data) if data.size > sr * 0.3 else ""  # chat/system turns carry no audio
    if heard is None:  # no early transcription of this speech
        heard = transcribe_final(audio)
    heard = "" if heard == "เสียงดนตรี" else heard  # Whisper's music/noise caption is no words (just_filler drops it too)
    heard = " ".join(x for x in (heard, take("text", "")) if x)  # typed chat joins (or replaces) the speech
    note = take("note", "")
    if not heard and not note:  # fastrtc already sent response_starting: without "done" the page stays on กำลังพูด…
        yield AdditionalOutputs({"kind": "done"})
        return
    if heard and not hello:
        carry_ts, carry_txt = take("carry", (0.0, ""))
        # the window counts from when this speech started: a phone call's answer stops at its first word (cut_off)
        if carry_txt and time.monotonic() - carry_ts < C.CARRY_MERGE_S + data.size / max(sr, 1):
            heard = f"{carry_txt} {heard}".strip()
    sid, history = session(public)
    if "leave_message" in tools:  # the tool gets the caller's own words and call id from here, never from the boss
        caller, call = " ".join([*(m["content"] for m in history if m["role"] == "user"), heard]), call_id()  # read here: a worker thread has no call
        tools = {**tools, "leave_message": lambda **a: leave_message(**{**a, "heard": caller, "call": call})}
    log.info("USER (%.2fs): %s", time.perf_counter() - t0, masked(heard or note))
    asked = f"{heard}\n{note}".strip()
    # time rides on this turn only: keeps the system prompt + history a stable, cached KV prefix
    # date/time only when asked: the boss kept quoting the clock as a web price's "updated at" date and time, and
    # the number guard can't tell (the clock is a source); no clock in the turn = nothing to misquote
    now = asked + clock_note(heard)
    # One language per call: only the caller's yes to the confirm question (or the operator's language button) changes it
    cid = call_id()
    cur_lang = CALL_LANG.get(cid)
    if cid and heard and not hello:
        stt = CALL_LAST_STT_LANG.get(cid, (None, 0.0))
        # the question lives for one turn: left waiting, a later misheard phrase in that language once counted as the yes
        action, lang = _lang_turn(heard, cur_lang, PENDING_LANG_CONFIRM.pop(cid, None), stt)
        if action in ("switch", "adopt"):
            CALL_LANG[cid] = lang
            log.info("LANG-SWITCH (%s): %s -> %s", cid, cur_lang or "-", lang)
            yield AdditionalOutputs({"kind": "lang", "language": lang})
            if action == "switch" and len(heard.strip()) <= 24 and _clearly_spoken_lang(heard, *stt) != lang:
                # a bare yes: nothing in this turn to answer yet
                yield AdditionalOutputs({"kind": "user", "text": heard})
                ack = LANG_SWITCHED_ACK.get(lang, LANG_SWITCHED_ACK["en"])
                yield bot_out(ack, _filler_or_speak(f"lang_ack_{lang}", ack, lang), lead=0.08)
                yield AdditionalOutputs({"kind": "done"})
                return
        elif action == "ask":
            PENDING_LANG_CONFIRM[cid] = lang
            log.info("LANG-ASK (%s): %s -> %s?", cid, cur_lang or "-", lang)
            yield AdditionalOutputs({"kind": "user", "text": heard})
            q_text = LANG_CONFIRM_PROMPTS.get(lang, LANG_CONFIRM_PROMPTS["en"])
            yield bot_out(q_text, _filler_or_speak(f"lang_confirm_{lang}", q_text, lang), lead=0.08)
            yield AdditionalOutputs({"kind": "done"})
            return
        elif action == "declined":
            now += f"\n(ระบบภาษา: ลูกค้าปฏิเสธการเปลี่ยนภาษา ให้คุยภาษาเดิม {LANG_NAMES.get(cur_lang or 'th', 'ภาษาไทย')} ต่อตามปกติ)"

    detected_name = ""
    if cid and heard and not hello:
        prev_bot_msg = history[-1]["content"] if history and history[-1].get("role") == "assistant" else ""
        detected_name, explicit_name = _extract_caller_name_py(heard, prev_bot_msg)
        if detected_name:
            cur_meta = dict(CALL_CALLER_META.get(cid) or {})
            if not cur_meta.get("name") or explicit_name:
                cur_meta["name"] = detected_name
                CALL_CALLER_META[cid] = _sync_caller_profile(cur_meta.get("phone") or "", cur_meta)
                log.info("CALLER-NAME-CAPTURED (%s): name=%s phone=%s", cid, detected_name, masked(cur_meta.get("phone") or ""))
                yield AdditionalOutputs({"kind": "caller_name", "name": detected_name})

    if cid and CALL_CONTEXT.get(cid):
        now += f"\n{CALL_CONTEXT.pop(cid)}"
    if cid and on_phone() and not hello and cid in CALL_GREET_SENT_AT:
        greet_sent_at = CALL_GREET_SENT_AT.pop(cid, 0.0)
        if greet_sent_at and (time.monotonic() - greet_sent_at) < 5.0:
            now += (
                "\n(ระบบ: คำทักทายต้นสายถูกพูดแทรกยังไม่จบ ให้บอกลูกค้าสั้นๆ 1 ประโยคด้วยว่า "
                "สำหรับลูกค้าถ้าเป็นบีซีแอคเคาท์เวอร์ชันเก่าให้ฝากเบอร์โทรกลับ เดี๋ยวเจ้าหน้าที่ติดต่อกลับ "
                "ตอนนี้เวอร์ชันล่าสุดคือบีซีเอไอแอคเคาท์ ถามเจาร์วิสได้ในเวอร์ชันใหม่ครับ)"
            )
    c_name = ""
    has_prior_topic = _has_continuous_topic("", history)
    if cid and (meta := CALL_CALLER_META.get(cid)):
        c_phone = _normalize_phone(meta.get("phone"))
        c_name = _clean_caller_name(meta.get("name"))
        c_comp = str(meta.get("company") or "").strip()
        c_calls = int(meta.get("totalCalls") or 0)
        c_topic = str(meta.get("lastTopic") or "").strip()
        has_prior_topic = _has_continuous_topic(c_topic, history)
        phone_note = ""
        if c_phone:
            dashed_p, spoken_p = _format_phone_spoken(c_phone)
            topic_cb_rule = (
                "(3) เรื่องที่ต้องการให้โทรกลับให้สรุปจากเรื่องที่คุยต่อเนื่องอยู่ได้เลยไม่ต้องถามซ้ำ"
                if has_prior_topic else
                "(3) สายนี้ยังไม่มีหัวข้อคุยต่อเนื่อง ต้องถามด้วยว่าต้องการให้โทรกลับเรื่องอะไร เช่น ปัญหาของโปรแกรม สนใจโปรแกรม หรือเรื่องอื่น"
            )
            prior_assistant_msgs = [
                (m.get("content") or "")
                for idx_m, m in enumerate(history)
                if m.get("role") == "assistant" and not (idx_m == 0 and FILLER_TEXT["phone_intro"][:25] in (m.get("content") or ""))
            ]
            cb_already_saved = LEAD_COUNT.get(cid, 0) > 0
            cb_offered_recently = any(
                re.search(r"โทรกลับ|ฝ่ายขายโทร|พนักงานโทร|ติดต่อกลับ", msg) for msg in prior_assistant_msgs[-4:]
            )
            if cb_already_saved:
                cb_restraint = " (บันทึกเรื่องโทรกลับในสายนี้ไปแล้ว ห้ามถามเรื่องโทรกลับซ้ำอีก ให้คุยช่วยเรื่องอื่นต่อไปเรื่อยๆ)"
            elif cb_offered_recently:
                cb_restraint = " (เพิ่งพูดเรื่องโทรกลับไปแล้วในรอบก่อนหน้า ห้ามถามเรื่องโทรกลับซ้ำในรอบนี้ ให้คุยตอบคำถามไปเรื่อยๆ อย่างเป็นธรรมชาติ)"
            else:
                cb_restraint = " (ห้ามถามหรือเสนอโทรกลับบ่อยเกินไป ให้คุยตอบคำถามไปเรื่อยๆ ถามเรื่องโทรกลับบ้างเฉพาะเมื่อจำเป็นจริงๆ ลูกค้าขอเอง หรือเป็นบีซีแอคเคาท์เวอร์ชันเก่าเท่านั้น)"
            phone_note = (
                f" เบอร์โทรที่ลูกค้าโทรเข้ามาคือ {c_phone} ({dashed_p} อ่านว่า {spoken_p}) "
                f"— ถ้าลูกค้าถามเบอร์ที่โทรเข้ามาให้บอกเบอร์นี้ได้เลยเป็นคำอ่านไทย "
                f"และถ้าลูกค้าต้องการคุยกับพนักงานจริงหรือขอให้โทรกลับ ต้องถามให้ครบสามอย่างก่อนบันทึกคือ "
                f"(1) ชื่อลูกค้า (ถ้ายังไม่มีในระบบต้องถามให้สำเร็จ) "
                f"(2) ถามยืนยันเสมอว่าจะให้โทรกลับเบอร์ที่โทรเข้ามานี้ ({spoken_p}) หรือให้โทรกลับเบอร์อื่น และ "
                f"{topic_cb_rule} "
                f"เมื่อครบทั้งสามอย่างแล้วจึงเรียก leave_message ทันที{cb_restraint}"
            )
        topic_note = f" เรื่องที่คุยไว้ครั้งก่อน: {c_topic}" if c_topic else ""
        if c_name:
            addressed_n = _format_addressed_name(c_name)
            now += (
                f"\n(ข้อมูลลูกค้าจากระบบสายเข้า: ชื่อ {addressed_n}{f' ({c_comp})' if c_comp else ''} "
                f"โทรมาครั้งที่ {max(c_calls, 1)}{topic_note}.{phone_note} "
                f"— เรียกชื่อลูกค้าว่า {addressed_n} อย่างเป็นกันเองได้เลย ห้ามถามชื่อซ้ำ)"
            )
        elif c_calls > 1:
            now += (
                f"\n(ข้อมูลลูกค้าจากระบบสายเข้า: ลูกค้าเก่าโทรมาครั้งที่ {c_calls}{f' ({c_comp})' if c_comp else ''}{topic_note} "
                f"— ยังไม่มีชื่อในระบบ.{phone_note} ต้องถามขอชื่อลูกค้าให้สำเร็จอย่างสุภาพ (ตอบเรื่องที่ลูกค้าถามก่อน แล้วขอทราบชื่อต่อท้ายจนกว่าจะได้ชื่อ ห้ามปล่อยผ่านโดยยังไม่ได้ชื่อ))"
            )
        else:
            now += (
                f"\n(ข้อมูลลูกค้าจากระบบสายเข้า: ลูกค้าใหม่ยังไม่มีชื่อในระบบ.{phone_note} "
                f"— ยังไม่มีชื่อในระบบ ต้องถามขอชื่อลูกค้าให้สำเร็จอย่างสุภาพ (ตอบเรื่องที่ลูกค้าถามก่อน แล้วขอทราบชื่อต่อท้ายจนกว่าจะได้ชื่อ ห้ามปล่อยผ่านโดยยังไม่ได้ชื่อ))"
            )
    alt_heard = LAST_ALT_HEARD.pop(cid, []) if cid else []
    if alt_heard:
        now += f"\n(ระบบฟัง 3 หู — คำจากหูสำรองเผื่อหูหลักเพี้ยน: {' | '.join(alt_heard[:2])})"

    active_lang = CALL_LANG.get(cid)
    if active_lang and active_lang != "th":
        lang_label = LANG_NAMES.get(active_lang, active_lang)
        now += (
            f"\n(ระบบภาษา [สำคัญมาก — ทับกติกาเขียนภาษาไทยด้านบน]: ลูกค้ากำลังคุยเป็น {lang_label} "
            f"ระบบเสียงของคุณรองรับการพูดเป็น {lang_label} ได้จริง 100% ห้ามพูดว่าพูดได้แค่ภาษาไทยหรือแปลไม่ได้เด็ดขาด! "
            f"ให้แปลข้อมูลคำตอบจากฐานความรู้ภาษาไทยและตอบกลับเป็นอักษรและภาษา {lang_label} แท้ๆ อย่างเป็นธรรมชาติ สั้นกระชับ 1-2 ประโยค)"
        )
    elif active_lang == "th" and heard and _detect_script_lang(heard) != "th":
        # the code above owns the language question; left to the LLM, a misheard English phrase got an English answer
        now += (
            "\n(ระบบภาษา: สายนี้คุยภาษาไทย คำที่ได้ยินรอบนี้ไม่ใช่ภาษาไทยซึ่งอาจฟังเพี้ยน ให้ตอบเป็นภาษาไทยเท่านั้น "
            "ห้ามเปลี่ยนภาษาหรือถามเรื่องเปลี่ยนภาษาเอง ระบบจะถามยืนยันกับลูกค้าเองเมื่อได้ยินภาษาอื่นชัดเจน)"
        )

    said, screen, turn = ([FILLER_TEXT["hello"]] if hello else []), [], {}  # the history keeps the hello too
    interrupted, answered = False, False
    stop = threading.Event()  # set when the turn is over: beside()'s thread reads no more
    try:
        if not hello:
            yield AdditionalOutputs({"kind": "user", "text": heard})  # "" for a system turn: clears the screen, no caller line
        # Local FAQ / DeepSeek QA Cache lookup: saves DeepSeek LLM + Judge tokens & answers in ~10ms (0ms hold filler)
        can_qa_cache = (
            bool(heard)
            and not hello
            and not note
            and not LATEST_ASK.search(heard)
            and not TIME_ASK.search(heard)
            and not DATE_ASK.search(heard)
            and not SUM_ASK.search(masked(heard))
            and not (history and TOPIC_ASK.search(history[-1]["content"]))
        )
        if can_qa_cache and (cached_qa := QA_CACHE.lookup(heard, lang=active_lang or "th", phone_mode=on_phone())):
            cached_sents = cached_qa["sentences"]
            log.info("QA-CACHE HIT (0 DeepSeek tokens, %.3fs): %s -> %d sentences", time.perf_counter() - t0, masked(heard), len(cached_sents))
            for idx_s, sent in enumerate(cached_sents):
                turn["voiced"] = True
                said.append(sent)
                yield from stream_bot_out(sent, lead=0.05 if idx_s == 0 else C.SENTENCE_GAP_S)
            answered = True
            yield AdditionalOutputs({"kind": "done"})
            return
        # the caller answers the topic ask: "ไม่ใช่ครับ ยังเรื่องเดิม" got the new topic back (eval 2026-11-08 r18); and no second ask in a row
        stay_topic = None
        if was := history and TOPIC_ASK.search(history[-1]["content"]):
            if REFUSE.search(heard) or "เรื่องเดิม" in heard:
                stay_topic = was[2]
                now += (f"\n(ระบบ: ผู้โทรยืนยันว่าไม่เปลี่ยนเรื่อง คือคุยเรื่อง{was[2]}ต่อ "
                        f"ห้ามเอ่ยถึงหรือพูดคำว่า{was[1]}ในคำตอบอีกเด็ดขาด ให้คุยต่อเฉพาะเรื่อง{was[2]}เท่านั้น)")
            else:
                now += (f"\n(ระบบ: เลขาเพิ่งถามว่าจะเปลี่ยนไปคุยเรื่อง{was[1]} หรือยังคุยเรื่อง{was[2]}ต่อ "
                        f"ถ้าผู้โทรตอบว่าเรื่องเดิม ไม่ใช่ หรือไม่เปลี่ยน คือคุยเรื่อง{was[2]}ต่อ)")
        if "desktop_task" in tools and desktop_agent.state["pending"] and heard:  # a held click/launch runs only on the caller's own yes
            if CONFIRM.search(heard) and not REFUSE.search(heard):
                now += f"\n(ระบบ: {C.USER_NAME}ยืนยันแล้ว ผลการทำงาน: {desktop_agent.confirm() or 'หมดเวลายืนยัน ต้องสั่งใหม่'})"
            else:
                desktop_agent.state["pending"] = None
                now += f"\n(ระบบ: {C.USER_NAME}ไม่ได้ยืนยัน งานที่รอไว้ถูกยกเลิก)"
        # a BC note read for this question: the answer explains it, so it ends with an example and starts with calc
        # (ลุงจืด 2026-11-09 "ต้องแสดงตัวอย่างด้วยทุกครั้ง"). ponytail: one more boss round before the first word on
        # every BC answer; asked by how-words only, "แล้วต้นทุนแบบไฟโฟล่ะ" got no calc and every example row was cut
        read, query = False, ""
        used_nearby_only = False
        if "web_search" in tools and LATEST_ASK.search(heard):  # changes daily: only a fresh search may answer it
            if on_phone() and (wait := _pick_wait_filler(active_lang)):
                yield bot_out(wait[0], wait[1], lead=0.05, filler=True)
            now += f"\n(ผลจาก web_search ตอนนี้: {(yield from dispatch([('web_search', {'query': heard})], said, turn, tools))[0]})"
        elif heard and (FAST_CHAT.match(heard.strip()) or MULTILANG_FAST_CHAT.match(heard.strip())):  # bare hello/thanks/hold: answer right away (~0.9 s faster, no wasted search)
            pass
        elif heard and on_phone() and _is_non_search_phone_turn(heard, detected_name, history):
            pass  # name intro, caller-meta question, closing, or pure callback confirm/request: 0ms wait filler / 0 MCP call
        elif heard and not on_phone() and not stay_topic and (wording := search_words(history, heard))[1] and not was:  # off the topic, maybe misheard: ask, search nothing
            topic, new = wording[1]
            log.info("topic jump? %s -> %s", topic, new)  # the log watcher learns from these
            # a fixed line, no boss: it once added "เพย์โรล" and the judge cut the whole ask (eval 2026-11-08 r17)
            turn["ask"] = f"ขอถามให้ชัดก่อนครับ จะเปลี่ยนไปคุยเรื่อง{new} หรือยังคุยเรื่อง{topic}ต่อครับ"
        elif heard:  # Auto Swarm: fan out multi-angle BC brain + local docs searches in parallel, then read top notes in parallel
            phone_mode = on_phone()
            if phone_mode:
                # Play instant pre-rendered "please hold" cue (0ms) so the caller immediately knows the line is live
                # while search planner / quick_answer + DeepSeek + TTS run behind the filler audio!
                if wait := _pick_wait_filler(active_lang):
                    yield bot_out(wait[0], wait[1], lead=0.05, filler=True)
                if stay_topic:
                    qs = [stay_topic]
                elif _needs_search_planner(heard, alt_heard, history):
                    planned_q, _ = search_words(history, heard, alt_heard=alt_heard, allow_sync=False)
                    if planned_q:
                        log.info("SEARCH-PLANNER: %s -> %s", masked(heard), masked(planned_q))
                        qs = list(dict.fromkeys([planned_q, *_expand_phone_intent_queries(heard)]))[:2]
                    else:
                        qs = _build_phone_fallback_queries(heard, alt_heard, history)
                else:
                    qs = list(dict.fromkeys([heard, *_expand_phone_intent_queries(heard)]))[:2]
            else:
                wording = search_words(history, heard) if not stay_topic else (heard, None)
                qs = [stay_topic] if stay_topic else swarm_queries(wording[0], heard, history)
            query = " | ".join(qs)
            sql_ctx = " SQL" if history and "```sql" in history[-1]["content"].lower() else ""
            zone = brain_audience(public, f"{heard} {query}{sql_ctx}")  # customer lines stay in "user" zone; owner dev stays "dev"
            unique_qs = list(dict.fromkeys((LONG_NUMBER.sub("", q).strip() or q.strip()) for q in qs if q and q.strip()))[:5]
            clean_q0 = unique_qs[0] if unique_qs else ""
            if "quick_answer" in tools and unique_qs:
                if phone_mode and len(unique_qs) >= 2 and "batch_ask" in tools:
                    # Single round-trip batch_ask with generous local RAM budget (no images/YouTube on phone)
                    calls = [("batch_ask", {
                        "questions": unique_qs,
                        **zone,
                        "mode": "ultra",
                        "max_chars_per_answer": 1400,
                        "include_media": False,
                    })]
                else:
                    # Big Voice Brain: local RAM quick_answer with 2,200-char grounded context (0ms network, media-free)
                    calls = [("quick_answer", {
                        "question": clean_q0,
                        **zone,
                        "mode": "ultra" if phone_mode else "normal",
                        "max_chars": 2200 if phone_mode else 3000,
                        "include_media": False,
                    })]
                    if phone_mode and len(unique_qs) > 1:
                        calls.append(("quick_answer", {
                            "question": unique_qs[1],
                            **zone,
                            "mode": "ultra",
                            "max_chars": 1400,
                            "include_media": False,
                        }))
                if "lookup_menu" in tools and MENU_CODE_ASK.search(f"{heard} {query} {' '.join(alt_heard)}"):
                    calls.append(("lookup_menu", {"query": clean_q0, **zone, "limit": 5}))
                if "search_docs" in tools and not phone_mode:
                    calls.append(("search_docs", {"query": qs[0]}))
                _prefetch_mcp_batch(calls, tools)
                texts = yield from dispatch(calls, said, turn, tools)
                qa_text = texts[0] if texts else ""
                abstained = any(_is_brain_abstain(t) for t in texts)
                read = bool(qa_text and not abstained and "ไม่พบ" not in qa_text[:120] and "no matching" not in qa_text[:120].lower())
                if read and not phone_mode:
                    slug_m = re.search(r"(?m)^slug:\s*([\w.-]+)", qa_text)
                    if slug_m:
                        turn["note"] = slug_m.group(1)
                    yield AdditionalOutputs({"kind": "panel", "text": note_panel(qa_text, NOTE_META.sub("", qa_text).strip()[:2000])})
            else:
                abstained = False
                # Legacy fallback when bcbrain has not exposed quick_answer
                calls = [*(("search_brain", {"query": q2, **zone}) for q in qs if "search_brain" in tools and (q2 := LONG_NUMBER.sub("", q).strip())),
                         *(("search_docs", {"query": qs[0]}) for _ in (1,) if "search_docs" in tools and not phone_mode)]
                texts = yield from dispatch(calls, said, turn, tools)
                max_slugs = 1 if phone_mode else 4
                slugs = list(dict.fromkeys(s for (n, _), t in zip(calls, texts) if n == "search_brain"
                                           for s in re.findall(r'"slug": "([^"]+)"', t)))[:max_slugs]
                if slugs and "batch_read" in tools:
                    batch_args = {"slugs": slugs, **zone, "query": clean_q0, "max_chars_per_note": 1100 if phone_mode else 1500, "strip_links": phone_mode}
                    b_calls = [("batch_read", batch_args)]
                    if "find_media" in tools and not turn.get("media") and not phone_mode:
                        b_calls.append(("find_media", {"query": clean_q0, "kind": "all", "limit": 20, "audience": zone.get("audience", "all")}))
                    b_texts = yield from dispatch(b_calls, said, turn, tools)
                    if b_texts and b_texts[0]:
                        calls.append(("batch_read", {}))
                        texts.append(b_texts[0])
                elif "read_note" in tools and slugs:
                    media = [("find_media", {"query": calls[0][1]["query"], "kind": "all", "limit": 20, "audience": zone.get("audience", "all")})
                             for _ in (1,) if "find_media" in tools and not turn.get("media") and not phone_mode]
                    notes = (yield from dispatch([*(("read_note", {"slug": s}) for s in slugs), *media], said, turn, tools))[:len(slugs)]
                    for k, note in enumerate(notes):
                        part = grounding.note_sections(note, f"{heard} {' '.join(qs)}{sql_ctx}")  # query: FIFO for a heard "ไฟโฟ"
                        if not part and k == 0:  # top hit whose headings used other words: pass its lead body so round 0 answers at once
                            part = NOTE_META.sub("", note).strip()[:1400]
                        if part:
                            calls.append(("read_note", {}))
                            texts.append(part if k == 0 else part[:900 if phone_mode else 1200])
                            if k == 0 and not phone_mode:
                                turn["note"] = slugs[0]
                                log.info("note on screen: %s", slugs[0])
                                yield AdditionalOutputs({"kind": "panel", "text": note_panel(note, part)})
                read = any(n in ("read_note", "batch_read") for n, _ in calls)
            has_nearby = "[ข้อมูลใกล้เคียงจากคลังความรู้ BC" in " ".join(texts)
            ctx_topic = _extract_recent_topic(history)
            topic_switched = _is_explicit_topic_switch(heard)
            if abstained and phone_mode:
                m_cos = re.search(r"cosine=([0-9.]+)", qa_text or "")
                m_slug = re.search(r"-\s+([a-z0-9._-]+)\s+\(", qa_text or "")
                _record_brain_miss(
                    heard,
                    ctx_topic="" if topic_switched else ctx_topic,
                    reason="abstain",
                    top_slug=m_slug.group(1) if m_slug else "",
                    top_cosine=float(m_cos.group(1)) if m_cos else 0.0,
                    lang=active_lang,
                )
            if abstained and not has_nearby and BRAIN_MIRROR.ready:
                nearby_q = " ".join(x for x in (clean_q0 or heard, "" if topic_switched else ctx_topic) if x).strip()
                nearby = BRAIN_MIRROR.search_nearby(
                    nearby_q,
                    audience=zone.get("audience", "user"),
                    limit_notes=2,
                    limit_menus=4,
                    max_chars_per_note=1100,
                    min_score=13.5 if (ctx_topic and not topic_switched) else 12.0,
                )
                if nearby and nearby.get("text"):
                    log.info(
                        "BRAIN-NEARBY-FALLBACK (ctx): slugs=%s menus=%d score=%.1f",
                        nearby.get("slugs"),
                        len(nearby.get("menus") or ()),
                        float(nearby.get("top_score") or 0.0),
                    )
                    calls.append(("nearby_mirror", {}))
                    texts.append(nearby["text"])
                    has_nearby = True
            elif not abstained and BRAIN_MIRROR.ready and zone.get("audience") == "user" and "confidence=high" not in (qa_text or "").lower():
                returned_slugs = set(re.findall(r"slug:\s*([\w.-]+)", " ".join(texts)))
                need_sup_ctx = bool(
                    ctx_topic
                    and not topic_switched
                    and not _STANDALONE_TOPIC_RE.search(heard)
                    and (len(heard) <= 36 or bool(_FOLLOWUP_START_RE.match(heard)) or bool(_VAGUE_FOLLOWUP_RE.search(heard)) or bool(_CONTINUATION_HINT_RE.search(heard)))
                )
                sup_q = " ".join(x for x in (heard, ctx_topic if need_sup_ctx else "") if x).strip()
                nearby = BRAIN_MIRROR.search_nearby(
                    sup_q,
                    audience="user",
                    limit_notes=2,
                    limit_menus=3,
                    max_chars_per_note=900,
                    exclude_slugs=returned_slugs,
                    min_score=14.0,
                )
                if nearby and nearby.get("text"):
                    log.info(
                        "BRAIN-NEARBY-SUPPLEMENT: slugs=%s menus=%d score=%.1f",
                        nearby.get("slugs"),
                        len(nearby.get("menus") or ()),
                        float(nearby.get("top_score") or 0.0),
                    )
                    calls.append(("nearby_mirror", {}))
                    texts.append(nearby["text"])
                    has_nearby = True
            used_nearby_only = abstained and has_nearby
            found = " ".join(f"[{n}] {t}" for (n, _), t in zip(calls, texts))
            topic_pin_hint = _build_topic_pin_hint(heard, history)
            if topic_pin_hint:
                found = f"{topic_pin_hint}\n{found}"
            prev_kb = any(m["role"] == "assistant" and C.SCREEN_MARK in m["content"] for m in history[-2:])
            sample_hint = (
                "สายนี้เป็นโทรศัพท์ ห้ามเขียน [จอ] ตาราง หรือรายละเอียดต่อท้ายเด็ดขาด ให้พูดสั้นกระชับ ประโยคแรกไม่เกินสิบห้าคำแล้วจบด้วยครับทันที)"
                if phone_mode else
                ("รอบนี้เป็นการถามต่อสั้นๆ จากเรื่องเดิม ตอบตรงจุดที่ถามเพิ่มบน [จอ] ได้เลยโดยไม่ต้องวางตัวอย่างสมมติซ้ำ)"
                 if prev_kb and not EXAMPLE_ASK.search(heard) and not CALC_ASK.search(heard) else
                 f"คำตอบที่อธิบายเรื่องใหม่ ต่อท้าย [จอ] ด้วยบรรทัด {C.SAMPLE_LABEL} แล้วเลือกวิธีแสดงตัวอย่างให้เหมาะกับบริบท "
                 "(ตารางคำนวณ แถบ [การ์ด]+กล่องเคส > ผังโครงสร้างย่อหน้า - /   - กล่องเคส >+[ขั้นตอน] หรือกรอบ ```sql+> พารามิเตอร์) "
                 "ห้ามครอบผังย่อหน้าด้วย ``` และห้ามใส่ตัวเลขสองหลักขึ้นไปหรือวันที่สมมติเองโดยไม่ผ่าน calc ถ้าไม่ได้เรียก calc ให้ใช้รหัสติดตัวอักษรเช่น WH01, A01, SKU01)")
            )
            if abstained and has_nearby:
                concise_nearby = (
                    "ให้ตอบสั้นกระชับไม่เกิน 2-3 ประโยคสั้นๆ ประโยคแรกแจ้งตรงๆ ว่าส่วนที่ถามตรงๆ ยังไม่มีข้อมูลยืนยัน แล้วเล่าเฉพาะจุดใกล้เคียงที่สำคัญที่สุด 1-2 จุดจากผลค้น ห้ามร่ายยาว "
                    if phone_mode else ""
                )
                reuse_hint = (
                    "สมองไม่พบคำตอบตรงเป๊ะ [Abstain] แต่มี [ข้อมูลใกล้เคียงจากคลังความรู้ BC] ที่มีอยู่จริงด้านบนแล้ว "
                    + concise_nearby +
                    "ห้ามเดาและห้ามแต่งเติมเองเด็ดขาด ให้บอกลูกค้าสั้นๆ ตรงๆ ว่าส่วนที่ถามตรงๆ ยังไม่มีข้อมูลยืนยัน "
                    "แล้วเล่าเฉพาะข้อเท็จจริงจากข้อมูลใกล้เคียงด้านบน (ชื่อฟีเจอร์ รายงาน หรือรหัสเมนู) เพื่อช่วยลูกค้าต่อ "
                    "ห้ามโมเมว่าทำได้ในส่วนที่ไม่ได้เขียนไว้ และไม่ต้องรีบถามเรื่องโทรกลับทุกครั้ง "
                )
            elif abstained:
                reuse_hint = (
                    "สมองคืนค่า Abstain (ไม่มีข้อมูลยืนยันในระบบ) ห้ามเดาหรือแต่งคำตอบเองเด็ดขาด ให้แจ้งลูกค้าตรงๆ ว่ายังไม่มีข้อมูลยืนยันในระบบ แล้วชวนคุยต่ออย่างเป็นธรรมชาติ ไม่ต้องรีบถามเรื่องโทรกลับทุกครั้ง (เสนอให้ฝากเบอร์โทรกลับเฉพาะเมื่อจำเป็นจริงๆ ลูกค้าขอเอง หรือเป็นบีซีแอคเคาท์เวอร์ชันเก่าเท่านั้น) "
                )
            elif read:
                reuse_hint = (
                    "ผลค้นจาก quick_answer/batch_ask ด้านบนมีข้อมูลพร้อมแล้ว ให้ใช้ตอบทันทีได้เลย ห้ามเรียก quick_answer ซ้ำด้วยคำถามเดิม "
                    "และหากผลค้นหลักยังไม่ตอบตรงคำถามเป๊ะแต่มีส่วน [ข้อมูลใกล้เคียงจากคลังความรู้ BC ...] ให้บอกตรงๆ ว่าประเด็นตรงๆ ยังไม่มีข้อมูลยืนยัน แล้วเล่าเฉพาะข้อมูลใกล้เคียงที่มีจริงด้านบน ห้ามเดา ห้ามแต่งเติม "
                    "เรียก lookup_menu หรือ batch_read เพิ่มเฉพาะเมื่อข้อมูลด้านบนยังขาดรหัสเมนูหรือรายละเอียดสำคัญจริงๆ เท่านั้น "
                )
            else:
                reuse_hint = (
                    "ถ้าไม่มีส่วนไหนตรง ให้สั่งลูกทีมค้นสมองในรอบเดียวก่อน (quick_answer / batch_ask / lookup_menu / batch_read หรือ web_search) "
                )
            now += (f"\n(ระบบ: คำพูดนี้น่าจะหมายถึง {query} ลูกทีม Swarm ค้นสมองให้แล้ว {found} "
                    "ใช้เฉพาะข้อมูลที่มีอยู่จริงในผลค้นสมองนี้เท่านั้น ห้ามเดาหรือแต่งเติมเองเด็ดขาด เวลาพูดให้คุยเป็นกันเองเหมือนเพื่อนโดยไม่ต้องพูดคำว่า จากคลังความรู้บีซี "
                    + reuse_hint +
                    # 2026-11-08 05:16: asked for a worked example, it said "ยังไม่มีในคลัง" twice
                    "ถ้ายังไม่เจอและเป็นความรู้ทั่วไปที่ไม่ใช่เรื่อง BC ให้ถาม general_knowledge ถ้าเป็นเรื่อง BC จึงบอกสั้นๆ ว่ายังไม่มีข้อมูล "
                    "แล้วถามว่าหมายถึงเรื่องไหน ถ้าขอตัวอย่างหรือให้คิดเลข ให้ตั้งตัวเลขสมมติตามสูตรในผลค้นแล้วคิดด้วย calc ห้ามตอบจากความจำ "
                    + sample_hint)
        bp_aud = "user" if public else ("dev" if SQL_ASK.search(f"{heard} {now}") else "user")
        bootstrap_pack = load_bootstrap_pack(bp_aud) if tools and any(n in tools for n in BRAIN_AUDIENCE_TOOLS) else ""
        sys_msg = prompt + (C.PHONE_PROMPT if on_phone() else "") + (
            f"\n\n# ข้อมูลอ้างอิงพื้นฐาน BC Brain ({bp_aud})\n{bootstrap_pack}" if bootstrap_pack else ""
        )
        messages = [{"role": "system", "content": sys_msg}, *history,
                    {"role": "user", "content": now.strip()}]
        known = grounding.numbers(" ".join(m["content"] for m in messages[1:]))  # every number a source gave this turn
        if cid and (meta_p := (CALL_CALLER_META.get(cid) or {}).get("phone")):
            known |= _phone_grounding_numbers(meta_p)
        if bootstrap_pack:
            known |= grounding.numbers(bootstrap_pack)
        answered = True
        if ask := turn.get("ask"):
            log.info("BOT  (topic ask): %s", masked(ask))
            said.append(ask)
            turn["voiced"] = True
            yield bot_out(ask, speak(ask), lead=0.18)
        max_rounds = C.SIP_MAX_TOOL_ROUNDS if on_phone() else C.MAX_TOOL_ROUNDS
        for rnd in range(0 if ask else max_rounds + 1):
            calls = []
            # what the judge may accept: this turn's question, prefetch and tool results, earlier turns, and what
            # load_workers said is true of this setup (Google not connected, the team); fixed within a round so
            # the judge's calls share DeepSeek's cached prefix
            sources = "\n".join([setup_facts(prompt), OWN_WORDS, bootstrap_pack, *(m["content"] for m in messages[1:])])
            # force calc only when the turn asks for/needs math; on phone calls (no screen table), only force on explicit math sums (~1.5s faster)
            prev_kb = any(m["role"] == "assistant" and C.SCREEN_MARK in m["content"] for m in history[-2:])
            phone_mode_turn = on_phone()
            need_calc = (
                bool(SUM_ASK.search(masked(heard)))
                if phone_mode_turn
                else bool(EXAMPLE_ASK.search(heard) or SUM_ASK.search(masked(heard))
                          or (read and CALC_ASK.search(heard if prev_kb else f"{heard} {query}")))
            )
            choice = {"type": "function", "function": {"name": "calc"}} if rnd == 0 and "calc" in tools and need_calc else None
            last = rnd == max_rounds  # the note rides on this call only, never kept in the history
            tokens = llm_tokens(plain_turn(messages + [LAST_ROUND]) if last else messages, calls, None if last else specs, choice)
            said_before, mark = len(said), time.perf_counter()  # mark: where each phrase's own time starts
            words, shown, side = beside(tokens, screen, known, sources, turn, stop)
            for chunk in chunk_sentences(words, min_chars=18 if phone_mode_turn else 20, max_chars=75 if phone_mode_turn else 140):
                if C.SILENT_MARK in chunk:  # the words were for someone else in the room: no answer at all
                    log.info("not said to the secretary: no answer")
                    turn["silent"] = True
                    break
                closed = bool(SENTENCE_TAIL.search(chunk))
                if turn.get("cut_open"):  # log 2026-11-14 15:15:57: head of an unfinished sentence was cut, never speak its headless tail
                    log.info("dropped tail of a cut sentence: %s", masked(clean(chunk)))
                    turn["cut_open"] = not closed
                    continue
                if text := clean(chunk):
                    # log 2026-11-08 13:16: 28 of 92 lines were a bare "ขอโทษครับ" opener, all a talked-over caller
                    # heard, and the history kept it as the whole reply, so the boss copied it (replay with 18 history
                    # messages: 3 of 3). ลุงจืด OK'd dropping it; the rest is said, nothing said falls to CUT_ALL
                    if not said and text == "ขอโทษครับ":
                        log.info("dropped a bare opener: ขอโทษครับ")
                        continue
                    # told the hello was said, the boss still opened with it 9 of 9 calls (eval 2026-11-09), or with a bare ครับ
                    if said == [FILLER_TEXT["hello"]] and not (text := re.sub(r"^(?:สวัสดี)?\s*(?:ครับ|ค่ะ)?\s*", "", text)):
                        continue
                    # ลุงจืด 2026-11-23 "ตอนตอบ มีคำว่า อ๋อ บ่อยเกินไป น่าเบื่อ": strip repetitive leading "อ๋อ/อ้อ" opener on phone calls
                    if not said and phone_mode_turn:
                        stripped_oh = re.sub(r"^(?:อ๋อ|อ้อ)\s+", "", text).strip()
                        if len(stripped_oh) >= 6:
                            text = stripped_oh
                    # log 2026-11-14 05:44..06:08: "ได้ครับ" went out alone, then the real sentence was cut and the
                    # turn ended on a bare "ได้ครับ" 3 times. Hold a lone ack or bare caller address ("คุณ...ครับ")
                    # to join the real sentence smoothly instead of speaking it alone between tool rounds.
                    if not said and (BARE_ACK.fullmatch(text) or BARE_ADDRESS.fullmatch(text)):
                        turn["ack"] = text
                        continue
                    if not said and turn.get("cut") and DANGLING_HEAD.search(text):  # log 2026-11-14 11:06:55: opener cut, "แต่ที่มีคือ..." spoke mid-thought
                        log.info("dropped dangling continuation after cut opener: %s", masked(text))
                        turn["cut_open"] = not closed
                        turn.pop("ack", None)
                        continue
                    candidate = text if not turn.get("ack") or text.startswith(turn["ack"]) else f"{turn['ack']} {text}"
                    got = time.perf_counter()
                    # ห้ามแต่ง: a phrase with an unsourced number or fact is dropped; one line at the end says so
                    if bad := grounding.unsourced(candidate, known):
                        log.info("cut a sentence: numbers %s are in no source: %s", masked(" ".join(sorted(bad))), masked(candidate))
                        turn["cut"], turn["cut_open"] = True, not closed
                        turn.pop("ack", None)
                        continue
                    if not (yield from showing(shown, aside(judge, sources, candidate, " ".join(said)))):
                        log.info("cut a sentence (judge %.2fs): the judge found no source for it: %s", time.perf_counter() - got, masked(candidate))
                        turn["cut"], turn["cut_open"] = True, not closed
                        turn.pop("ack", None)
                        continue
                    text = candidate
                    turn.pop("ack", None)
                    judged = time.perf_counter()
                    # speak() trims each phrase to its speech: without a gap the next sentence started 0.08 s after the last
                    lead = C.SENTENCE_GAP_S if turn.get("voiced") else 0.08
                    turn["voiced"] = True
                    said.append(text)
                    emo = _detect_reply_emotion(heard, text) if on_phone() else "friendly"
                    yield from stream_bot_out(text, lead=lead, emotion=emo)
                    # which part is slow, per phrase: the boss writing it, the judge, the voice
                    log.info("BOT  (%.2fs | boss %.2f judge %.2f voice %.2f): %s", time.perf_counter() - t0, got - mark,
                             judged - got, time.perf_counter() - judged, masked(text))
                    mark = time.perf_counter()
            if turn.get("silent"):
                break
            yield from showing(shown, side)  # the rest of the screen, each line as it passes
            if not calls and rnd < max_rounds and not turn.get("nudged") and STALL.search(" ".join(said[said_before:])):
                turn["nudged"] = True  # once: a second bare promise just ends the turn
                messages += [{"role": "assistant", "content": " ".join(said[said_before:])}, {"role": "user", "content": STALL_NUDGE}]
                continue
            # 2026-11-17: with a prompt that lets the boss explain longer, the callback call lost the caller's number
            # 3 times in 14 replays (0 in 14 before): the boss took the number, promised a call back and told the
            # prefetched notes instead of calling leave_message (the 2026-11-11 08:30 miss again). A number heard, a
            # promise said and no leave_message this turn: told once to store it.
            # ponytail: words, not meaning; a promise in other words ends the turn unsaved as before
            prev_asked_cb = bool(history and re.search(r"เบอร์นี้|เบอร์อื่น|โทรกลับเบอร์", history[-1].get("content", "")))
            confirmed_same_phone = prev_asked_cb and bool(re.search(r"เบอร์นี้|เบอร์เดิม|เบอร์ที่โทร|ใช้เบอร์นี้|เอาเบอร์นี้|ใช่|ครับ|ค่ะ|ได้|โอเค", heard))
            caller_phone_digits = spoken_digits((CALL_CALLER_META.get(cid) or {}).get("phone") or "")
            caller_phone_ok = 9 <= len(caller_phone_digits) <= 15
            bot_claimed_saved = bool(re.search(r"บันทึก(?:เรื่อง)?(?:ฝาก|ไว้ให้แล้ว)|ฝากเรื่อง(?:ไว้)?ให้แล้ว|ส่งเรื่องให้พนักงาน", " ".join(said)))
            caller_asked_cb = bool(re.search(r"ฝากเบอร์|โทรกลับ|ติดต่อกลับ|คุยกับพนักงาน", heard))
            has_callback_phone = (
                9 <= len(spoken_digits(heard)) <= 15
                or (confirmed_same_phone and caller_phone_ok)
                or ((bot_claimed_saved or (caller_asked_cb and bool(re.search(r"เบอร์เดิม|เบอร์นี้|เบอร์ที่โทร", " ".join(said) + " " + heard)))) and caller_phone_ok)
            )
            if (not calls and rnd < max_rounds and not turn.get("lead_nudged") and "leave_message" in tools
                    and LEAD_COUNT.get(cid, 0) == 0
                    and has_callback_phone and c_name
                    and (has_prior_topic or _has_continuous_topic("", [{"role": "user", "content": heard}]))
                    and LEAD_PROMISE.search(" ".join(said))
                    and not any(c["function"]["name"] == "leave_message" for m in messages for c in m.get("tool_calls", ()))):
                turn["lead_nudged"] = True
                log.info("lead nudge: a number heard and a call back promised, leave_message not called")
                messages += [{"role": "assistant", "content": " ".join(said[said_before:])}, {"role": "user", "content": LEAD_NUDGE}]
                continue
            # ลุงจืด 2026-11-17 "ถามเรื่องทั่วไป ก็ต้องตอบได้": 38 of 960 turns (log 2026-11-14..17) ended on a fallback line
            # with every spoken sentence cut, and the boss asked general_knowledge in 7. When nothing it said stood,
            # code asks that worker once: the answer is a source for the next round, said as general knowledge.
            # ponytail: asked with the caller's bare words, so a "แล้วอันนี้ล่ะ" gets a vague answer and the old fallback
            if (not calls and heard and not said and turn.get("cut") and rnd < max_rounds and "general_knowledge" in tools
                    and not any(c["function"]["name"] == "general_knowledge" for m in messages for c in m.get("tool_calls", ()))):
                calls, turn["cut_open"] = [{"id": "general", "name": "general_knowledge", "args": {"question": heard}}], False
            if not calls:
                break
            messages.append({"role": "assistant", "content": " ".join(said[said_before:]), "tool_calls": [
                {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["args"], ensure_ascii=False)}}
                for c in calls]})
            texts = yield from dispatch([(c["name"], c["args"]) for c in calls], said, turn, tools)
            messages += [{"role": "tool", "tool_call_id": c["id"], "content": text} for c, text in zip(calls, texts)]
            known |= grounding.numbers(" ".join(texts))
            for c in calls:
                if c.get("name") == "leave_message":
                    known |= _phone_grounding_numbers(c.get("args", {}).get("phone") or "")
        # a greeting or reminder just drops the phrase: "not found" fits no hello. Nothing said at all went silent
        # (eval 2026-11-08, 2 of 12 runs, no cut, no error): that too gets the ask-for-more line
        if heard and not said and not turn.get("silent"):
            if any(re.search(r"\w", s) for s in turn.get("kept", ())):  # screen details passed, only spoken summary was cut
                text = SCREEN_ONLY
                log.info("BOT  (screen fallback): %s", text)
            elif turn.get("ack") and not turn.get("cut"):  # the boss meant a short acknowledgement only
                text = turn["ack"]
                log.info("BOT  (ack): %s", text)
            else:
                if not turn.get("cut"):
                    log.warning("the boss said nothing (%d rounds)", rnd + 1)
                text = CUT_ALL
                log.info("BOT  (all cut): %s", text)  # logged, so watch_calls.py sees how often it is said
            turn["voiced"] = True
            said.append(text)
            yield bot_out(text, speak(text), lead=0.18)
    except GeneratorExit:
        interrupted = True
        raise
    finally:  # also runs after barge-in; history may hold a chunk that was queued but cut before playback
        stop.set()
        if interrupted and heard and not hello and not said and not turn.get("voiced") and not turn.get("kept"):
            carry_cid = cid or call_id()
            PENDING[carry_cid]["carry"] = (time.monotonic(), heard)
        elif answered:
            details = "\n".join(turn.get("kept", []))  # only lines that passed both checks, never the CUT_SHOW note; an unchecked rest is no memory
            answer = C.SILENT_MARK if turn.get("silent") else " ".join(said) + (f"\n{C.SCREEN_MARK}\n{details[:800]}" if details else "")
            history.extend([{"role": "user", "content": asked}, {"role": "assistant", "content": answer}])
            del history[: -2 * C.HISTORY_TURNS]
            save_session(sid, history)
            # Persist verified, uninterrupted FAQ answers into local QA cache to save future DeepSeek tokens
            used_lead = any(
                c.get("function", {}).get("name") == "leave_message"
                for m in (locals().get("messages") or ())
                for c in m.get("tool_calls", ())
            )
            if (
                locals().get("can_qa_cache")
                and not interrupted
                and not turn.get("silent")
                and not turn.get("cut")
                and not used_lead
                and not locals().get("abstained")
                and not locals().get("used_nearby_only")
                and not _FOLLOWUP_START_RE.match((heard or "").strip())
                and said
                and said[0] not in (CUT_ALL, SCREEN_ONLY, BRAIN_DOWN, BRAIN_DOWN_BODY)
                and BRAIN_DOWN_BODY not in " ".join(said)
                and not locals().get("cached_qa")
            ):
                if QA_CACHE.store(heard, said, lang=active_lang or "th", phone_mode=on_phone(), caller_name=c_name):
                    log.info("QA-CACHE SAVED (%d sentences): %s", len(said), masked(heard))
    yield AdditionalOutputs({"kind": "done"})


brief = {"day": None}  # today's brief (calendar + unread mail) is given on the first call of the day


def greet(*_, public=False):
    """startup_fn: runs when a call connects. For new phone callers, default to Thai and play instant
    pre-rendered greeting stating that J.A.R.V.I.S can speak any language (0ms LLM/TTS delay)."""
    yield AdditionalOutputs({"kind": "user", "text": ""})  # clears the screen, no caller line
    cid = call_id()
    if cid and cid not in CALL_LANG:
        CALL_LANG[cid] = "th"
    cur_lang = CALL_LANG.get(cid) or "th"

    # Returning phone caller: greet warmly by name & last topic from phone/CRM lookup!
    meta = CALL_CALLER_META.get(cid) or {}
    c_phone = _normalize_phone(meta.get("phone"))
    c_name = _clean_caller_name(meta.get("name"))
    c_calls = int(meta.get("totalCalls") or 0)
    last_topic = str(meta.get("lastTopic") or "").strip()
    log.info("GREET (%s): lang=%s phone=%s name=%s calls=%d", cid, cur_lang, masked(c_phone), c_name or "-", c_calls)

    if on_phone():
        if c_name:
            if cur_lang == "en":
                greeting = f"Hello {c_name}, this is Jarvis from BC Call Center. Great to talk with you again! How can I help you today?"
                yield bot_out(greeting, speak(greeting, lang_override="en", emotion="upbeat"), lead=0.14)
                yield AdditionalOutputs({"kind": "done"})
                return
            if cur_lang == "th":
                sid_g, hist_g = session(public)
                addressed = _format_addressed_name(c_name)
                name_greet = f"{addressed} เจาร์วิสครับ"
                full_greet = f"{FILLER_TEXT['hello']} {name_greet} {FILLER_TEXT['version_notice']} {FILLER_TEXT['returning_ask']}"
                hist_g.append({"role": "assistant", "content": full_greet})
                del hist_g[: -2 * C.HISTORY_TURNS]
                save_session(sid_g, hist_g)
                CALL_GREET_SENT_AT[cid] = time.monotonic()
                yield bot_out(FILLER_TEXT["hello"], FILLERS["hello"], lead=0.12)
                yield bot_out(name_greet, speak(name_greet, lang_override="th", emotion="upbeat"), lead=0.06)
                yield bot_out(FILLER_TEXT["version_notice"], _filler_or_speak("version_notice", FILLER_TEXT["version_notice"], "th"), lead=0.08)
                yield bot_out(FILLER_TEXT["returning_ask"], _filler_or_speak("returning_ask", FILLER_TEXT["returning_ask"], "th"), lead=0.08)
                yield AdditionalOutputs({"kind": "done"})
                return
        if cur_lang == "th":
            sid_g, hist_g = session(public)
            if c_calls > 1:
                ask_name = "ไม่ทราบว่าขอเรียกคุณชื่ออะไรดีครับ"
                full_greet = f"{FILLER_TEXT['hello']} {FILLER_TEXT['version_notice']} {ask_name}"
                hist_g.append({"role": "assistant", "content": full_greet})
                del hist_g[: -2 * C.HISTORY_TURNS]
                save_session(sid_g, hist_g)
                CALL_GREET_SENT_AT[cid] = time.monotonic()
                yield bot_out(FILLER_TEXT["hello"], FILLERS["hello"], lead=0.12)
                yield bot_out(FILLER_TEXT["version_notice"], _filler_or_speak("version_notice", FILLER_TEXT["version_notice"], "th"), lead=0.08)
                yield bot_out(ask_name, speak(ask_name, lang_override="th"), lead=0.08)
                yield AdditionalOutputs({"kind": "done"})
                return
            hist_g.append({"role": "assistant", "content": f"{FILLER_TEXT['hello']} {FILLER_TEXT['phone_intro']}"})
            del hist_g[: -2 * C.HISTORY_TURNS]
            save_session(sid_g, hist_g)
            CALL_GREET_SENT_AT[cid] = time.monotonic()
            yield bot_out(FILLER_TEXT["hello"], FILLERS["hello"], lead=0.18)
            yield bot_out(FILLER_TEXT["phone_intro"], FILLERS["phone_intro"], lead=0.12)
            yield AdditionalOutputs({"kind": "done"})
            return

    yield bot_out(FILLER_TEXT["hello"], FILLERS["hello"], lead=0.25)
    back = "ผู้โทรคนนี้เคยคุยกันแล้ว ทักต่อแบบคนคุ้นเคยตามเรื่องก่อนหน้าได้เลย " if session(public)[1] else ""
    note = (f"{CALL_START} พูดว่า{FILLER_TEXT['hello']}ไปแล้ว ห้ามทักซ้ำ "
            f"แนะนำตัวสั้นๆ ว่าผมคือเจาร์วิส ผู้ช่วยเอไออัตโนมัติของบีซีคอลเซ็นเตอร์ คุยได้ทุกภาษา ตอนนี้เวอร์ชันล่าสุดคือบีซีเอไอแอคเคาท์ ถามเจาร์วิสได้ทันทีทุกเรื่อง ส่วนถ้าใช้บีซีแอคเคาท์เวอร์ชันเก่าฝากเบอร์โทรกลับไว้ในระบบได้ "
            f"แล้วถามขอชื่อลูกค้าอย่างสุภาพและถามว่าจะให้ช่วยดูแลเรื่องไหน {back}"
            "ห้ามพูดเรื่องอากาศ ข่าว หรือข้อเท็จจริงใดที่ไม่ได้มาจากผลค้น")  # it once invented sunny weather in a hello
    # ponytail: on the owner line the brief goes to whoever calls first that day (127.0.0.1 only)
    if not public and "calendar_events" in TOOLS and brief["day"] != datetime.date.today():
        brief["day"] = datetime.date.today()
        cal, mail = yield from dispatch([("calendar_events", {"days": 1}), ("gmail_search", {"query": "is:unread newer_than:1d"})],
                                        [], {}, TOOLS)
        note += f" แล้วสรุปงานวันนี้สั้นๆ จากนัดหมาย: {cal} และอีเมลที่ยังไม่อ่าน: {mail} รายละเอียดใส่หลัง {C.SCREEN_MARK}"
    PENDING[call_id()]["note"] = note + ")"
    yield from reply((16000, np.zeros(0, dtype=np.float32)), public, hello=True)


# fastrtc waits for extra UI inputs when the reply fn takes more than one parameter (reply_on_pause.py
# _needs_additional_inputs) and the call then hangs silent: each line gets a one-parameter handler
def reply_owner(audio):
    yield from reply(audio)


def reply_public(audio):  # same call, the customer table and prompt
    yield from reply(audio, public=True)


def greet_public(*_):
    yield from greet(public=True)


async def remind_loop(stream):
    """Once a minute: an event starting within REMIND_MIN minutes is said once, while the line is quiet."""
    told = set()
    while True:
        await asyncio.sleep(60)
        try:
            call, handler = next(iter(stream.handlers.items()), (None, None))  # the owner line takes one call
            if "calendar_events" not in TOOLS or not handler or handler.state.responding or handler.state.started_talking:
                continue
            for ev in await asyncio.to_thread(google_tools.upcoming_events, C.REMIND_MIN):
                if ev["id"] not in told:
                    told.add(ev["id"])
                    mins = max(1, round((ev["start"] - datetime.datetime.now(datetime.timezone.utc)).total_seconds() / 60))
                    PENDING[call]["note"] = f"(ระบบ: อีกประมาณ {mins} นาทีมีนัด {ev['summary']} เตือน{C.USER_NAME}สั้นๆ)"
                    handler.push_turn(interrupt=False)
                    break
        except Exception as e:  # never let one bad minute stop reminders
            log.warning("reminder check failed: %s", type(e).__name__)


def warm_up():
    t0 = time.perf_counter()
    stt.transcribe(np.zeros(16000, dtype=np.float32))
    if final_stt is not None:  # no VAD filter: the model itself runs once
        list(final_stt.transcribe(np.zeros(16000, dtype=np.float32), language="th", beam_size=1)[0])
    # same system prompt + tools as real calls: DeepSeek caches that prefix and the connection stays open
    hello = [{"role": "system", "content": C.SYSTEM_PROMPT}, {"role": "user", "content": "สวัสดี"}]
    list(llm_tokens(hello, [], TOOL_SPECS))
    FILLERS.update({
        name: speak(text, lang_override="en" if "_en" in name else "th")
        for name, text in FILLER_TEXT.items()
    })
    log.info("warm-up done in %.1fs (%d pre-rendered fillers)", time.perf_counter() - t0, len(FILLERS))


class BargeInReplyOnPause(ReplyOnPause):
    """ReplyOnPause.reset() runs when a reply finishes *generating* (playback may go on for seconds) and wipes
    speech the caller already started over the bot, so that interruption and the caller's words were lost."""

    def copy(self):
        handler = super().copy()  # upstream copy() hard-codes ReplyOnPause
        handler.__class__ = BargeInReplyOnPause
        return handler

    def reset(self):
        old = self.state
        super().reset()
        self._checked_state_id = 0
        self._checked_stream_len = 0
        for field in ("stream", "buffer", "sampling_rate", "started_talking"):
            setattr(self.state, field, getattr(old, field))
        if not self.state.started_talking:
            LIVE.pop(id(self), None)

    _BARGE_KEYWORDS = ("หยุด", "เดี๋ยว", "แป๊บ", "ขอถาม", "สอบถาม", "เอาใหม่", "เปลี่ยนภาษา", "stop", "wait", "excuse")
    _is_phone = False
    _live_error_at = 0.0  # monotonic time of the last logged live_words() error

    def live_words(self, sr, snap, state, is_phone=False):
        """Stream live karaoke words over the WebRTC data channel while the caller is still speaking,
        and trigger Word-Verified Barge-in on phone calls so coughs/ambient noise never cut off the agent
        while real interruptions stop playback within ~200ms."""
        live = LIVE.get(id(self))
        try:
            if not state.started_talking:
                return
            snap16 = to16k((sr, snap))
            if transcribe is not _DEFAULT_TRANSCRIBE or not live or not live.get("stream"):
                txt = transcribe((sr, snap))
            else:
                txt = live["stream"].accept(snap16)
            bare = re.sub(r"[\s.,!?…]+", "", txt)
            if not bare or bare in self.HESITATIONS or bare in self.BACKCHANNELS or not state.started_talking:
                return
            now_m = time.monotonic()
            turn_s = max(snap.size, live.get("fed", 0) if live else 0) / sr
            phone = is_phone or self._is_phone
            spoken = " ".join(t for s, e, t, _ in self.playing if e + 0.5 > now_m - turn_s and s < now_m)
            if spoken and not phone and grounding.is_echo(txt, spoken):  # a phone line brings none back: hears_itself()
                return
            # Word-Verified Barge-in on phone lines: stop immediately as soon as foreground human words are heard
            # Note: is_phone is passed explicitly because ThreadPoolExecutor threads do not inherit FastRTC contextvars!
            bot_active = state.responding or any(e > now_m and not is_f for _, e, _, is_f in self.playing)
            if phone and not self.cut and bot_active:
                stream = state.stream  # the handler's thread drops it once the sound turns out to be no turn
                # Measure peak foreground level and confirmed voiced duration to prevent 0.03s-0.13s clicks/noise
                # from triggering false OpenThai barge-ins that silence an active answer (log 2026-11-25 07:43-07:46).
                recent_tail = stream[-int(sr * 0.5):] if stream is not None else snap
                dbfs = max(_audio_dbfs(snap16), _audio_dbfs(to16k((sr, recent_tail))))
                speech_window = voiced((sr, stream[-int(sr * C.SIP_BARGE_LEVEL_SPAN_S):])) if stream is not None else voiced((sr, snap))
                voiced_s = speech_window.size / 16000
                if dbfs < C.SIP_BARGE_MIN_DBFS and speech_window.size:
                    dbfs = max(dbfs, _audio_dbfs(speech_window))
                low_bare = bare.lower()
                base_chars = len(re.sub(r"[\u0e31\u0e34-\u0e3a\u0e47-\u0e4e]", "", bare))
                is_keyword_or_cut = bare in self.SIP_CUT_INS or any(k in low_bare for k in self._BARGE_KEYWORDS)
                min_voiced_s = 0.12 if is_keyword_or_cut else 0.22
                is_real_barge = (
                    dbfs >= C.SIP_BARGE_MIN_DBFS
                    and voiced_s >= min_voiced_s
                    and not bare.isdigit()
                    and (
                        is_keyword_or_cut
                        or (len(bare) >= C.SIP_BARGE_MIN_CHARS and base_chars >= 3)
                    )
                )
                if is_real_barge:
                    self.cut_off(f"word-verified barge-in: {masked(bare)} ({dbfs:.0f} dBFS, {turn_s:.1f}s in)")
                    state.responding = False
                elif live and not live.get("refused"):  # once a turn: why words over the answer did not stop it
                    live["refused"] = True
                    log.info("words over the answer too quiet to cut in: %s (%.0f dBFS)", masked(bare), dbfs)
            if live:
                new, live["text"] = txt != live["text"], txt
                # Only display OpenThai live partial on screen when caller is speaking Thai (avoid showing Thai transliteration during EN/ZH/JA/KO/LO turns)
                cur_call_lang = CALL_LANG.get(call_id()) or "th"
                if new and cur_call_lang == "th":
                    self.send_message_sync(json.dumps({"type": "partial", "data": txt}, ensure_ascii=False))
        except Exception:  # was silent: a cut-in lost to an error here left no trace
            if time.monotonic() - self._live_error_at > LIVE_ERROR_LOG_GAP_S:
                self._live_error_at = time.monotonic()
                log.warning("live words failed, no cut-in check for this speech", exc_info=True)
        finally:
            if live:
                live["busy"] = False

    def determine_pause(self, audio, sampling_rate, state):
        """LiveKit-style endpointing. fastrtc looked for silence in fixed 0.6 s chunks, so it saw the end of a turn
        0.6-1.2 s late; this checks the last second every PAUSE_STEP_S, and the turn ends PAUSE_SILENCE_S after the
        last word. At EARLY_STT_SILENCE_S the speech so far is transcribed already; reply() takes those words if the
        caller said nothing more (LiveKit's preemptive generation, the STT part only: the boss's tools can act)."""
        if self._checked_state_id == id(state):
            return True  # pause already verified for this state; keep pause_detected=True until emit() consumes it
        is_phone = on_phone()
        if is_phone:
            self._is_phone = True
        pause = C.SIP_PAUSE_SILENCE_S if is_phone else C.PAUSE_SILENCE_S
        if is_phone and state.started_talking:
            live_txt = (LIVE.get(id(self)) or {}).get("text", "")
            if live_txt and _is_incomplete_thai_tail(live_txt):
                pause = getattr(C, "SIP_INCOMPLETE_PAUSE_S", 0.95)
        step, window = int(sampling_rate * C.PAUSE_STEP_S), int(sampling_rate * (pause + 0.4))
        if len(audio) < (step if state.started_talking else window):
            return False
        if state.started_talking:
            state.stream, state.buffer = np.concatenate((state.stream, audio)), None
            recent = state.stream[-window:]
        else:  # waiting for speech: the last second carries over, so the turn starts with its first word
            recent, state.buffer = audio[-window:], audio[-(window - step):]
        _, chunks = self.model.vad((sampling_rate, recent), self.model_options)
        silence = len(recent) / sampling_rate - (chunks[-1]["end"] / 16000 if chunks else 0)  # vad() works at 16 kHz
        if not state.started_talking:
            if not chunks:
                return False
            if is_phone:
                # Option A (2026-11-23): FastRTC's 100ms Silero VAD fires on phone line noise that Whisper's VAD later
                # drops 100% (22.1% empty turns), clearing state.buffer and adding up to 0.8s lag + GPU contention.
                # Confirm real foreground speech with STT's own VAD + dBFS gate before starting a turn, keeping
                # state.buffer warm so genuine speech starts on the very next 100ms step without losing word 1.
                try:
                    onset_speech = voiced((sampling_rate, recent))
                    if not onset_speech.size or _audio_dbfs(onset_speech) < C.SIP_MIN_SPEECH_DBFS:
                        return False
                except Exception:
                    log.warning("phone turn-onset VAD check failed, allowing turn", exc_info=True)
            state.started_talking, state.stream, state.buffer = True, recent, None
            LIVE[id(self)] = {"t": 0.0, "fed": 0, "text": "", "busy": False,
                              "stream": stt.stream() if hasattr(stt, "stream") else None}
            # On phone calls, do NOT cut off at 100ms VAD onset (that lets coughs/ambient noise kill replies).
            # Instead, live_words() / process_audio() verify real foreground words and emit "barge_in_confirmed".
            self.send_message_sync(create_message("log", "started_talking"))
        live = LIVE.setdefault(id(self), {"t": 0.0, "fed": 0, "text": "", "busy": False,
                                          "stream": stt.stream() if hasattr(stt, "stream") else None})
        now_m = time.monotonic()
        min_new = int(sampling_rate * C.LIVE_STEP_S)
        early = EARLY.get(id(self))
        if (silence < pause and not live["busy"]
                and now_m - live["t"] >= C.LIVE_STEP_S and state.stream.size - live.get("fed", 0) >= min_new):
            fed = live.get("fed", 0)
            delta = state.stream[fed:].copy() if live.get("stream") is not None else state.stream[-15 * sampling_rate:].copy()
            live["busy"], live["t"], live["fed"] = True, now_m, state.stream.size
            ctx = contextvars.copy_context()
            LIVE_STT_POOL.submit(ctx.run, self.live_words, sampling_rate, delta, state, is_phone)
        # speech past what was transcribed early makes it out of date; a speech end the sliding VAD merely moves a
        # little (through Opus it does) inside the transcribed audio does not. No speech in the window: the speech
        # end did not move, `silence` is just capped at the window. Without `chunks` every 0.9 s of quiet counted as
        # new words and re-ran the STT on the whole, growing turn (2026-11-04 19:46: 36 s of audio once a second
        # while the reply was busy, 20 s before the caller's words came out)
        early_silence = getattr(C, "SIP_EARLY_STT_SILENCE_S", C.EARLY_STT_SILENCE_S) if is_phone else C.EARLY_STT_SILENCE_S
        # Add 300ms VAD jitter tolerance before discarding completed EARLY_STT (prevents 3x redundant TRIPLE-EAR loops & 13s dead air)
        if early and chunks and state.stream.size - int(silence * sampling_rate) > early[0].size + int(sampling_rate * 0.30):
            EARLY.pop(id(self), None)[1].cancel()  # still queued: it never runs
            early = None
        prev_job = live.get("early_job")
        prev_busy = prev_job is not None and callable(getattr(prev_job, "done", None)) and not prev_job.done()
        fresh = not early and not prev_busy and silence >= early_silence
        if fresh:
            ctx_stt = contextvars.copy_context()
            job = EARLY_STT.submit(ctx_stt.run, transcribe_final, (sampling_rate, state.stream))
            live["early_job"] = job
            early = EARLY[id(self)] = (state.stream, job)
        # the secretary neither thinks nor had a phrase on the caller's speaker during this speech (as just_filler counts it)
        if fresh and not state.responding and not any(e + 0.5 > now_m - state.stream.size / sampling_rate for _, e, _, _ in self.playing):
            history = session(self.fn is reply_public)[1]
            cur_cid = call_id() if "call_id" in globals() else ""
            early[1].add_done_callback(lambda job, h=history, c=cur_cid, p=is_phone: words_transcribed(job, h, cid=c, phone=p))
        if is_phone and state.started_talking and state.stream.size >= int(sampling_rate * 9.0) and silence >= 1.05:
            return True
        return silence >= pause

    cut = False  # cut_off() stopped the answer for the speech now being heard; process_audio's turn check clears it
    _checked_stream_len = 0
    _checked_state_id = 0

    def cut_off(self, reason: str = "the caller started talking"):
        """Stop the active answer and flush outbound RTP audio only after foreground speech is confirmed."""
        log.info("%s: the answer stops (phone line)", reason)
        self._close_generator()
        self.generator, self.cut = None, True
        self.event.clear()
        self.clear_queue()
        self.send_message_sync(create_message("log", "barge_in_confirmed"))

    def process_audio(self, audio, state):
        if self._checked_state_id == id(state):
            # Log 2026-11-23 20:43:12: queued 20ms frames after a pause caused 23 duplicate voiced()+STT checks
            # in 300ms before emit() consumed state.stream and created state.new().
            return
        checked = state.pause_detected  # 2026-11-08 23:03: this pause was checked on the frame that found it; every frame
        super().process_audio(audio, state)  # until the reply starts finds it again (3-8 times a turn)
        # 2026-11-04 15:51:55: 1.2 s of echo or noise passed fastrtc's VAD (100 ms of speech is enough there), its
        # pause killed a four-tool answer mid-search, and the STT then found no speech in it at all: 34 s of silence.
        # The STT's own VAD decides now: what it would not transcribe is no turn and cuts nothing. ~10 ms a pause
        if not (state.pause_detected and not checked and state.stream is not None):
            return
        if state.stream.size == self._checked_stream_len:
            return
        self._checked_stream_len = state.stream.size
        phone = on_phone()
        try:  # an STT/GPU error raised here ended listening for the rest of the call
            speech = voiced((state.sampling_rate, state.stream))
            speech_s = speech.size / 16000
            dbfs = _audio_dbfs(speech) if speech.size else -100.0
            too_quiet = phone and speech.size > 0 and dbfs < C.SIP_MIN_SPEECH_DBFS
            too_short_turn = False
            min_turn_s = C.SIP_MIN_TURN_SPEECH_S if phone else 0.08
            if 0 < speech_s < min_turn_s:
                # Log 2026-11-23 20:43:10 & 2026-11-24 20:38:22: 0.03s-0.06s glitches ("และ", "ครับ") must never trigger a turn.
                early = EARLY.get(id(self))
                short_heard = re.sub(r"[\s.,!?…]+", "", early[1].result()) if early else ""
                if speech_s < 0.08 or short_heard not in self.SIP_CUT_INS:
                    too_short_turn = True
            noise = (
                not speech.size
                or too_quiet
                or too_short_turn
                or self.hears_itself(state)
                or self.just_filler(state)
                or self.no_cut_in(state, speech_s)
            )
            if speech.size:  # how loud each turn is: the numbers a gate for far-off voices (TV, other people) needs
                reason_tag = ", background noise" if too_quiet else (", too short" if too_short_turn else (", not a turn" if noise else ""))
                log.info("heard %.2fs of speech at %.0f dBFS%s", speech_s, dbfs, reason_tag)
        except Exception:
            log.exception("turn check failed, taking the speech as a turn")
            noise = False
        if noise:
            state.pause_detected = state.started_talking = False
            state.stream = None
            self._checked_stream_len = 0
            self._checked_state_id = 0
            EARLY.pop(id(self), None)
            LIVE.pop(id(self), None)
            self.send_message_sync(create_message("log", "noise"))  # the page turns the secretary back up
            if self.cut:  # the answer was stopped for this sound and no reply runs now
                state.responding = False
                log.info("the answer stopped for a sound that is no turn (phone line)")
            if PENDING[call_id()]["text"]:  # typed while this noise played: /chat left it to this turn, so start it now
                self.push_turn(interrupt=False)
        else:
            self._checked_state_id = id(state)
            if phone and not self.cut and any(e > time.monotonic() and not is_f for _, e, _, is_f in self.playing):
                # Fallback barge-in for non-Thai foreground speech verified at pause end
                self.cut_off("confirmed turn barge-in")
                state.responding = False
        self.cut = False

    playing = ()  # (start, end, text, is_filler) of the secretary's phrases on the caller's speaker, as emit() hands them out

    def emit(self):
        running = self.generator
        out = super().emit()
        if running is not None and self.generator is None and out is not None:  # closed while it made this phrase
            return None
        if isinstance(out, tuple) and len(out) == 2 and isinstance(out[1], AdditionalOutputs) and isinstance(out[0], tuple):
            card = out[1].args[0]
            if isinstance(card, dict) and card.get("kind") == "bot":  # every spoken phrase carries its text
                start = max(time.monotonic(), self.playing[-1][1] if self.playing else 0)
                dur = out[0][1].shape[-1] / out[0][0]
                self.playing = (*self.playing[-5:], (start, start + dur, card["text"], bool(card.get("filler"))))
        elif isinstance(out, tuple) and len(out) == 2 and isinstance(out[0], int) and isinstance(out[1], np.ndarray):
            # Continuation chunk of a streaming TTS phrase: extend the latest playing window end time
            if self.playing:
                s, e, txt, is_f = self.playing[-1]
                dur = out[1].shape[-1] / max(out[0], 1)
                self.playing = (*self.playing[:-1], (s, max(e, time.monotonic()) + dur, txt, is_f))
        return out

    def clear_queue(self):
        self.playing = ()  # what was queued will not play
        super().clear_queue()

    def hears_itself(self, state):
        """No headphones (ลุงจืด 2026-11-04): the secretary's voice can leak from the speaker into the mic past the
        browser's echo canceller (it misses a speaker picked with setSinkId), and the turn that made would cut the
        answer off and answer the secretary's own words. A turn heard while it talked, mostly its words, is that.
        A phone line brings none of its voice back (recordings of 2026-11-23: the caller's channel stays at its noise
        floor, -72 dBFS, while it talks), so there the word match only dropped real answers made of its own words:
        17:00:00 the caller's "ใช่" to "...ใช่ไหมครับ", and cut-ins waited until the caller said a word it had not."""
        if on_phone():
            return False
        now, length = time.monotonic(), state.stream.size / state.sampling_rate
        spoken = " ".join(t for s, e, t, _ in self.playing if e + 0.5 > now - length and s < now)  # 0.5 s: network + jitter
        if not spoken:
            return False
        early = EARLY.get(id(self))  # started 0.25 s ago: usually done
        heard = early[1].result() if early else transcribe_final((state.sampling_rate, state.stream))
        if echo := grounding.is_echo(heard, spoken):
            log.info("echo of its own voice, not a turn: %s", masked(heard))
        return echo

    # a lone sound with nothing in it was a turn: in the logs of 2026-11-07..08 อืม 8 times, อ่า 2, เอ่อ, คือ, ฮึ, อ่ะ,
    # หึ, and in the AirPods call 2026-11-08 20:32 นะครับ, เอ่อ, คือ, each cutting the answer off for a bare "ครับ".
    # A hesitation is never an answer; an อืม is a yes after a question, so it passes over only while the secretary
    # talks or thinks. ponytail: fixed lists; หืม (huh?), ครับ, ใช่ and หยุด stay turns
    # เสียงดนตรี: Whisper's caption for music or noise, 22 whole caller turns in the log answered from the brain
    HESITATIONS = {
        "เอ่อ", "อ่า", "อ้า", "คือ", "นะ", "นะครับ", "นะคะ", "เสียงดนตรี",
        "แฮ่ม", "อะแฮ่ม", "แค่ก", "ฮัดเช้ย", "อึ้ม", "หืมม", "โอ๊ย", "อุ๊ย", "ฮึ", "หึ",
    }
    BACKCHANNELS = {"อืม", "อือ", "ฮึ", "หึ", "อ่ะ", "อะ", "อ๋อ", "อ้อ"}

    def just_filler(self, state):
        early = EARLY.get(id(self))  # started 0.25 s into the silence: usually done; none = not checked
        heard = re.sub(r"[\s.,!?…]+", "", early[1].result()) if early else ""
        now, length = time.monotonic(), state.stream.size / state.sampling_rate
        busy = state.responding or self.cut or any(e + 0.5 > now - length for _, e, _, _ in self.playing)  # as hears_itself counts it
        if heard in self.HESITATIONS or (busy and heard in self.BACKCHANNELS):
            log.info("a lone %s, not a turn", heard)
            return True
        return False

    # ลุงจืด 2026-11-17 "mic ไวเกินไป": any sound the VAD took for speech cut the answer off, also one with no words
    # in it (reply() then said nothing). Log 2026-11-14..17: 336 of 937 caller turns came within 2 s of a phrase of
    # its own. ponytail: fixed list; a real cut-in under BARGE_MIN_SPEECH_S that is not in it is lost
    CUT_INS = {"หยุด", "หยุดก่อน", "พอ", "พอแล้ว", "เดี๋ยว", "เดี๋ยวก่อน", "ครับ", "ค่ะ", "ใช่", "ไม่", "ไม่ใช่", "ได้", "หืม"}

    # ลุงจืด 2026-11-23: a phone caller also cuts in with these (no speaker echo on a phone line to say them back)
    SIP_CUT_INS = CUT_INS | {"แป๊บ", "ขอถาม", "สอบถาม", "ราคา", "เท่าไหร่", "ช่วย", "ฟัง", "เอาใหม่"}

    def no_cut_in(self, state, speech_s):
        """While the secretary thinks, or a phrase of it played during the caller's speech, a sound with no words
        or one under BARGE_MIN_SPEECH_S that is no stop word or short answer is no turn (LiveKit's interruption
        min_duration). An answer that starts after its last phrase ended is always a turn."""
        phone = on_phone()
        end = time.monotonic() - (C.SIP_PAUSE_SILENCE_S if phone else C.PAUSE_SILENCE_S)  # the speech ended that long before this check
        if not (state.responding or self.cut or any(s < end and e > end - speech_s for s, e, _, _ in self.playing)):
            return False
        early = EARLY.get(id(self))
        heard = re.sub(r"[\s.,!?…]+", "", early[1].result() if early else transcribe_final((state.sampling_rate, state.stream)))
        if not heard or (speech_s < (C.SIP_BARGE_MIN_SPEECH_S if phone else C.BARGE_MIN_SPEECH_S)
                         and heard not in (self.SIP_CUT_INS if phone else self.CUT_INS)):
            log.info("too short to cut in (%.2fs of speech), not a turn: %s", speech_s, masked(heard))
            return True
        return False

    def push_turn(self, interrupt):
        """Start a reply with no speech (typed chat, system note), like receive() does on a pause; `interrupt` also cuts the bot off."""
        if self.state.responding:
            if not interrupt:
                return  # a mid-reply system note rides along with the caller's next turn
            self._close_generator()
            self.generator = None
        self.clear_queue()
        self.trigger_response()


def make_stream(public=False):
    handler = BargeInReplyOnPause(
        reply_public if public else reply_owner,
        startup_fn=greet_public if public else greet,  # the secretary speaks first when the call connects
        can_interrupt=True,  # barge-in: user speech clears queued bot audio
        # exact speech ends (no padding, a 0.1 s gap ends a stretch): determine_pause measures the silence after it
        model_options=SileroVadOptions(min_speech_duration_ms=C.VAD_MIN_SPEECH_MS, min_silence_duration_ms=100, speech_pad_ms=0),
        output_sample_rate=C.TTS_SAMPLE_RATE,
    )
    if not public:  # 127.0.0.1 only: host candidates are enough, and no STUN means no wait for one
        return Stream(handler, modality="audio", mode="send-receive", concurrency_limit=1,
                      server_rtc_configuration={"iceServers": []})
    # this PC sits behind NAT: STUN gives it a public address the customer's TURN relay can reach (the browser
    # gets TURN from /ice); calls are capped in number and length
    stun = {"iceServers": [{"urls": f"stun:{C.TURN_HOST}:3478"}]} if C.TURN_HOST else None
    return Stream(handler, modality="audio", mode="send-receive", concurrency_limit=C.PUBLIC_CALLS,
                  time_limit=C.PUBLIC_CALL_SECS, server_rtc_configuration=stun)


# ลุงจืด 2026-11-09 "เข้ามาครั้งแรก ต้องคุยเลย": before it answers an offer, aioice asks STUN from every network
# adapter and waits for all of them up to 5 s. Two adapters here never get a reply, so every call waited 5.0 s;
# the adapter that works answers in ~0.05 s.
# ponytail: 1 s keeps one STUN retry (aioice resends at 0.5 s); a slower link loses the server's public candidate
# and customers behind NAT cannot connect. Then raise this or drop the dead adapters.
aioice.ice.Connection.get_component_candidates.__defaults__ = (1,)


def _filter_bindable_ice_addresses(addrs: list[str]) -> list[str]:
    """Drop Windows APIPA (169.254.x.x) and IPv6 link-local (fe80::) addresses that trigger WinError 10049."""
    clean = [a for a in addrs if not a.startswith(("169.254.", "fe80:"))]
    return clean or list(addrs)


_orig_aioice_host_addrs = aioice.ice.get_host_addresses
aioice.ice.get_host_addresses = lambda use_ipv4=True, use_ipv6=True: _filter_bindable_ice_addresses(
    _orig_aioice_host_addrs(use_ipv4=use_ipv4, use_ipv6=use_ipv6)
)


def ice_servers():
    """TURN for the customer's browser, as coturn's use-auth-secret expects: the user name is an expiry time and
    the password its HMAC with the shared secret, so nothing long-lived ever reaches a browser. callSecs: the call's
    time limit, so the page can say why the line closed (static/index.html, text.onclose)."""
    if not (C.TURN_HOST and C.TURN_SECRET):
        return {"iceServers": [], "callSecs": C.PUBLIC_CALL_SECS}
    user = f"{int(time.time()) + 3600}:jarvis-{os.urandom(4).hex()}"  # coturn counts user-quota per name after ":", so one per /ice
    key = base64.b64encode(hmac.new(C.TURN_SECRET.encode(), user.encode(), hashlib.sha1).digest()).decode()
    return {"iceServers": [{"urls": f"stun:{C.TURN_HOST}:3478"},
                           {"urls": [f"turn:{C.TURN_HOST}:3478?transport=udp", f"turn:{C.TURN_HOST}:3478?transport=tcp"],
                            "username": user, "credential": key}], "callSecs": C.PUBLIC_CALL_SECS}


PUBLIC_SITE = "https://callcenter.bcsmethai.com"
ROBOTS_TXT = "\n".join([
    *(f"User-agent: {bot}\nAllow: /" for bot in (
        "Googlebot", "OAI-SearchBot", "ChatGPT-User", "GPTBot", "ClaudeBot", "PerplexityBot", "Google-Extended")),
    "User-agent: *",
    "Allow: /",
    "Disallow: /webrtc/",
    "Disallow: /outputs",
    "Disallow: /chat",
    "Disallow: /ice",
    "Disallow: /leads",
    f"Sitemap: {PUBLIC_SITE}/sitemap.xml",
    "",
])
SITEMAP_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    f"  <url><loc>{PUBLIC_SITE}/</loc><changefreq>weekly</changefreq><priority>1.0</priority></url>\n"
    "</urlset>\n"
)
LLMS_TXT = (
    "# J.A.R.V.I.S — พนักงานตอบคำถามเสียงภาษาไทย BC Ai Account (ถามได้ทุกเรื่องในระบบ)\n\n"
    "> พนักงานตอบคำถามเสียงภาษาไทยสำหรับโปรแกรมบัญชี BC Ai Account (BC AI Account) ถามได้ทุกเรื่องในระบบ คุยโต้ตอบแบบเพื่อนผ่านเบราว์เซอร์ (WebRTC) หรือพิมพ์แชท "
    "ตอบคำถามการใช้งานโปรแกรมบัญชี BC Ai Account และระบบบัญชี SME ไทยจากสมองคลังความรู้บีซี พร้อมคำนวณตัวอย่างด้วย Decimal และรับฝากข้อความติดต่อกลับ\n\n"
    "## ความสามารถหลักบนสายสาธารณะ\n"
    "- ตอบคำถามได้ทุกเรื่องในระบบ ทั้งเมนู ขั้นตอนการใช้งาน และวิธีคิดต้นทุน/ภาษีในโปรแกรมบัญชี BC Ai Account จากสมองคลังความรู้บีซีโดยไม่เดาหรือแต่งข้อมูล\n"
    "- คำนวณตัวอย่างประกอบบนหน้าจอด้วยเครื่องคิดเลขทศนิยมแม่นยำ (Decimal ROUND_HALF_UP)\n"
    "- รับฝากชื่อ ร้าน/บริษัท เบอร์โทร และเรื่องที่ต้องการให้ติดต่อกลับ\n\n"
    "## ลิงก์หลัก\n"
    f"- [หน้าสนทนาหลัก]({PUBLIC_SITE}/): เปิดหน้าเว็บเพื่อเริ่มคุยด้วยเสียงหรือพิมพ์ข้อความได้ทันที\n"
)


def make_app(stream, public=False):
    tools = PUBLIC["tools"] if public else TOOLS

    @asynccontextmanager
    async def lifespan(_):
        task = None if public else asyncio.create_task(remind_loop(stream))
        yield
        task and task.cancel()

    app = FastAPI(lifespan=lifespan)
    stream.mount(app)  # POST /webrtc/offer

    @app.get("/")
    def index():
        return FileResponse(os.path.join(C.BASE_DIR, "static", "index.html"))

    @app.get("/team")  # who is on the team right now
    def team():
        return {"boss": C.DEEPSEEK_MODEL, "workers": sorted(set(WORKER.get(n, "other") for n in tools))}

    @app.get("/cache/stats")  # inspect local TTS & DeepSeek QA cache savings
    def cache_stats():
        tts_stats = tts[1].stats() if isinstance(tts, tuple) and tts[0] == "elevenlabs" else {"enabled": False}
        return {"tts": tts_stats, "qa": QA_CACHE.stats()}

    @app.get("/teach")  # list admin-taught & cached FAQ entries
    def list_taught(taught_only: bool = False):
        return {"ok": True, "entries": QA_CACHE.list_entries(taught_only=taught_only)}

    @app.post("/teach")  # 1-click admin FAQ teaching: persists permanently in QA cache
    async def post_teach(body: dict):
        try:
            q = str(body.get("question") or "").strip()
            a = str(body.get("answer") or "").strip()
            lang = str(body.get("lang") or "th").strip() or "th"
            entry = QA_CACHE.teach(q, a, lang=lang)
            log.info("ADMIN-TAUGHT FAQ (%s): %s -> %d sentences", lang, masked(q), len(entry.get("sentences") or []))
            return {"ok": True, "entry": entry}
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.delete("/teach")  # remove a taught or cached FAQ entry by key
    async def delete_taught(body: dict):
        key = str(body.get("key") or "").strip()
        if not key:
            return JSONResponse({"ok": False, "error": "key is required"}, status_code=400)
        deleted = QA_CACHE.delete_entry(key)
        return {"ok": deleted}

    @app.get("/outputs")  # subtitles, status, info cards, panel (Server-Sent Events)
    async def outputs(webrtc_id: str):
        async def events():
            async for out in stream.output_stream(webrtc_id):
                yield f"data: {json.dumps(out.args[0], ensure_ascii=False)}\n\n"
        return StreamingResponse(events(), media_type="text/event-stream")

    @app.post("/language")  # set or switch caller language + store initial CRM context without triggering extra turn
    async def post_language(body: dict):
        call = str(body.get("webrtc_id") or "")
        lang = body.get("language")
        is_new = bool(body.get("is_new_caller", False))
        init_ctx = body.get("initial_context")
        pending_confirm = body.get("pending_confirm")
        caller_phone = body.get("caller_phone")
        caller_meta = body.get("caller_meta")
        if not call:
            return JSONResponse({"error": "webrtc_id is required"}, status_code=400)
        if lang is None or lang == "" or lang == "auto":
            CALL_LANG[call] = None
        elif str(lang) in LANG_NAMES:
            CALL_LANG[call] = str(lang)
        else:
            return JSONResponse({"error": "unsupported language"}, status_code=400)
        if isinstance(init_ctx, str) and init_ctx.strip():
            CALL_CONTEXT[call] = init_ctx.strip()[:800]
        if isinstance(caller_meta, dict) or caller_phone:
            raw_meta = dict(caller_meta) if isinstance(caller_meta, dict) else {}
            phone_val = _normalize_phone(caller_phone or raw_meta.get("phone") or (CALL_CALLER_META.get(call) or {}).get("phone"))
            merged_meta = _sync_caller_profile(phone_val, raw_meta)
            CALL_CALLER_META[call] = merged_meta
            log.info(
                "CALLER-SYNC (%s): phone=%s name=%s company=%s calls=%d",
                call,
                masked(merged_meta.get("phone") or ""),
                merged_meta.get("name") or "-",
                merged_meta.get("company") or "-",
                int(merged_meta.get("totalCalls") or 0),
            )
        if pending_confirm and str(pending_confirm) in LANG_NAMES:
            PENDING_LANG_CONFIRM[call] = str(pending_confirm)
        elif pending_confirm is None and "pending_confirm" in body:
            PENDING_LANG_CONFIRM.pop(call, None)
        return {
            "ok": True,
            "language": CALL_LANG[call],
            "is_new_caller": is_new,
            "caller_known": bool((CALL_CALLER_META.get(call) or {}).get("name")),
            "caller_name": (CALL_CALLER_META.get(call) or {}).get("name") or "",
        }

    @app.post("/chat")  # typed message instead of speech; answered with voice + screen like a spoken turn
    async def post_chat(body: dict):
        call = str(body.get("webrtc_id"))
        text, handler = body.get("text"), stream.handlers.get(call)
        if not isinstance(text, str) or not text.strip() or len(text) > 500:
            return JSONResponse({"error": "text must be 1-500 characters"}, status_code=400)
        if not handler:
            return JSONResponse({"error": "no active call"}, status_code=409)
        PENDING[call]["text"] = f"{PENDING[call]['text']} {text.strip()}".strip()[:1000]
        if not handler.state.started_talking:  # typed while speaking: joins that turn
            handler.push_turn(interrupt=True)
        return {"ok": True}

    @app.get("/note")  # one BC note in full, behind a card or a [[link]] (ลุงจืด 2026-11-04 "กดดูได้ต่อเนื่อง")
    def note(slug: str):
        # ponytail: no call needed, the brain MCP itself is open to the internet; this reads one note by its slug
        if "read_note" not in tools or not re.fullmatch(r"[\w.-]{1,120}", slug):
            return JSONResponse({"error": "unknown note"}, status_code=404)
        try:
            text = tools["read_note"](slug=slug)[1]
        except Exception as e:
            log.warning("note %s failed: %s", slug, e)
            return JSONResponse({"error": "note unavailable"}, status_code=502)
        return {"text": (NOTE_META.sub("", text) if public else text)[:40000]}  # customers: content, not code paths

    @app.get("/robots.txt")
    def robots():
        return PlainTextResponse(ROBOTS_TXT)

    @app.get("/sitemap.xml")
    def sitemap():
        return Response(SITEMAP_XML, media_type="application/xml")

    @app.get("/llms.txt")
    def llms_txt():
        return PlainTextResponse(LLMS_TXT)

    if public:
        app.get("/ice")(ice_servers)
    else:  # customers' names and numbers: the owner line only, which listens on 127.0.0.1
        app.get("/leads")(lambda: HTMLResponse(leads_page()))

    return app


def tunnel():
    """Customers reach the public port through Caddy on PUBLIC_SSH: a reverse SSH tunnel, redialled when it drops.
    ponytail: a killed server can leave its ssh behind; that one still forwards to the same port and holds it, so
    every dial of this one is refused at once (2026-11-14..17: 19,000 warnings, one every 10 s). Said once a streak"""
    refused = False
    while True:
        began = time.monotonic()
        subprocess.run(["ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=20",
                        "-o", "ServerAliveCountMax=3", "-R", f"127.0.0.1:{C.PUBLIC_TUNNEL_PORT}:127.0.0.1:{C.PUBLIC_PORT}", C.PUBLIC_SSH],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        again, refused = refused, time.monotonic() - began < 30
        if not (again and refused):
            log.warning("public tunnel closed, redialling every 10 s%s", " (refused at once: no network, or an old ssh holds the port)" if refused else "")
        time.sleep(10)


async def serve():
    """The owner line and the public line: two ports, one process, so both share the models already on the GPU."""
    lines = [(make_app(make_stream()), C.SERVER_PORT), (make_app(make_stream(True), public=True), C.PUBLIC_PORT)]
    await asyncio.gather(*(uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")).serve()
                           for app, port in lines))


if __name__ == "__main__":
    os.makedirs(C.DOCS_DIR, exist_ok=True)
    os.makedirs(C.SESSIONS_DIR, exist_ok=True)
    stt = load_stt()
    final_stt = load_final_stt()
    if not C.DEEPSEEK_API_KEY:
        raise SystemExit("OLLAMA_API_KEY is not set: put it in .env.local (J.A.R.V.I.S uses Ollama Cloud)")
    tts = load_tts()
    load_mcp_tools()
    load_workers()
    tts_label = (
        f"elevenlabs/{C.ELEVENLABS_MODEL_ID} (0 GPU VRAM)"
        if isinstance(tts, tuple) and tts[0] == "elevenlabs"
        else f"{C.TTS_MODEL} ({C.DEVICE})"
    )
    log.info("STT=%s (gpu %s, final words) + %s (cpu-onnx, live) BOSS=%s JUDGE=%s TTS=%s", C.FINAL_STT_MODEL,
             C.FINAL_STT_COMPUTE_TYPE, C.STT_MODEL, C.DEEPSEEK_MODEL, C.JUDGE_MODEL, tts_label)
    warm_up()
    if C.PUBLIC_SSH:
        threading.Thread(target=tunnel, daemon=True).start()
    asyncio.run(serve())
