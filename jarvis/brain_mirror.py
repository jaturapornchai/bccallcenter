"""Embedded Local In-Process BC Brain Engine (<1–3 ms RAM, zero MCP HTTP API).

Reads markdown notes directly from ``d:\\yalecom\\bcbrain\\vault`` into RAM with
automatic mtime hot-reload, O(1) 4-digit menu catalog indexing, BM25 + field-weighted
Thai/English search, synonym expansion, 1-hop wikilink graph traversal, strict
user/dev audience isolation, OOV abstain guard, nearby verified fallback, and
bootstrap pack generation — all inside the same process with zero external network calls.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import math
import os
import re
import threading
import time
from collections import deque
from typing import Any

import httpx

try:
    from pythainlp.corpus import thai_stopwords as _pythai_stopwords
    from pythainlp.tokenize import word_tokenize as _pythai_tokenize
except Exception:  # pragma: no cover - fallback when pythainlp unavailable
    _pythai_stopwords = None
    _pythai_tokenize = None

log = logging.getLogger("voice")

DEFAULT_LOCAL_VAULT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bcbrain", "vault")
)

DEV_FOLDERS = frozenset({"api", "architecture", "dev-playbook", "queries", "schema"})
_MENU_ROW_RE = re.compile(
    r"^\|\s*\*\*(\d{4})\*\*\s*\|\s*\*\*([^|*]+)\*\*\s*\|\s*\*\*([^|*]+)\*\*(?:<br>([^|]+))?\s*\|\s*([^|]+)\s*\|"
)
_MENU_CODE_TOKEN_RE = re.compile(r"\b([1-9]\d{3})\b")
_LEGACY_CODE_TOKEN_RE = re.compile(r"\b(\d+\.\d+(?:\.\d+)?)\b")
_SOURCE_LINE_RE = re.compile(r"(?m)^(?:sources|slug):\s*.*\n?")
_WS_TOKEN_RE = re.compile(r"[a-zA-Z0-9\u0E00-\u0E7F]{2,}")
_WIKILINK_RE = re.compile(r"\[\[(?:[a-z_]+::)?([^\]|#]+?)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\((https://[^)\s]+)\)")
_MD_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\((https://[^)\s]+)\)")
_YT_URL_RE = re.compile(r"(?:youtube\.com/(?:watch\?[^\s)]*v=|embed/|shorts/|playlist\?)|youtu\.be/)", re.I)
_SEE_ALSO_HEADING_RE = re.compile(r"^##\s+(?:ดูต่อที่เกี่ยวข้อง|ดูเพิ่มเติม|ลิงก์ที่เกี่ยวข้อง)(?:\s|$)")
_TABLE_IDENT_RE = re.compile(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b")
_QUOTED_ERR_RE = re.compile(r'[`"“]([^`"”\n]{6,120})[`"”]')

# STT typo normalization applied ONLY to search tokens (never alters spoken replies or grounding)
_STT_SEARCH_NORMALIZE_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"ออกบิน"), "ออกบิล"),
    (re.compile(r"คราวก|(?:เรื่อง|บน|ระบบ|ของ|เป็น)(?:คราว|คาว)"), " คลาวด์ "),
    (re.compile(r"ถึ[งก](?:ดีล|ดิว|ดิ้ว)"), " ครบกำหนด "),
    (re.compile(r"มิติบุคคล"), "นิติบุคคล"),
    (re.compile(r"(?:เป็น|มัน|ของ|แยก|คนละ)\s*นิติ(?!บุคคล)"), " นิติบุคคล "),
    (re.compile(r"การเซ็กชั่น|ทานเทคชัน|ทาง\s*tection", re.I), " ธุรกรรม transaction "),
    (re.compile(r"สต(?:๊อก|็อค|อค)"), "สต็อก"),
    (re.compile(r"ซีเรียลัมเบอร์|ซีเริ่มอัมเบอร์|ซีเรียสอัมเบอร์"), " ซีเรียลนัมเบอร์ serial "),
    (re.compile(r"ไฟโฟ|ฟีโฟ"), " fifo "),
    (re.compile(r"พีโอเอส"), " pos "),
    (re.compile(r"คลางสินค้า|คังสินค้า"), "คลังสินค้า"),
    (re.compile(r"(?:ตาม|รวม|แยก|แย่)(?:คัง|ขัง|ขัน)|รวมครั้ง(?=\s*ไหม)|ทุกครั้น"), " คลังสินค้า "),
    (re.compile(r"ยอดคนเหลือ|ยอดโครงเรือ"), " ยอดคงเหลือ "),
    (re.compile(r"เซลฟเวอร์|เซฟเวอร์"), " เซิร์ฟเวอร์ "),
    (re.compile(r"(?:ขาย)?อย่างรถยนต์"), " ยางรถยนต์ "),
    (re.compile(r"โลเคชั[น่]น?"), " โลเคชั่น จุดเก็บ location bin "),
    (re.compile(r"(?<![a-zA-Z])(?:vc|วีซี)(?![a-zA-Z])|มีซี(?![a-zA-Z\u0E00-\u0E7F])", re.I), " บีซี "),
)

# Synonym groups ported from bcbrain/lib/vault.ts + Call Center voice synonyms
_BRAIN_SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("สต็อก", "สินค้าคงเหลือ", "คงเหลือ", "คลัง", "ยอดคงเหลือ"),
    ("คลัง", "คลังสินค้า", "สต็อก", "wms"),
    ("ที่เก็บ", "จุดเก็บ", "ชั้นวาง", "โลเคชั่น", "location", "bin", "wms"),
    ("ลูกหนี้", "ลูกค้า", "ค้างชำระ", "วางบิล"),
    ("เจ้าหนี้", "ซัพพลาย", "ซัพพลายเออร์", "ผู้จำหน่าย", "ผู้ขาย"),
    ("ลดหนี้", "คืนสินค้า", "รับคืน", "ส่งคืน", "ใบลดหนี้"),
    ("เช็ค", "เช็ครับ", "เช็คจ่าย", "เช็คเด้ง", "ทะเบียนเช็ค"),
    ("มัดจำ", "เงินมัดจำ", "ล่วงหน้า"),
    ("ภพ30", "vat", "ภาษีมูลค่าเพิ่ม", "ภาษีซื้อ", "ภาษีขาย"),
    ("ภงด3", "ภงด53", "wht", "หักณที่จ่าย", "หัก ณ ที่จ่าย", "ทวิ"),
    ("งบการเงิน", "งบทดลอง", "งบดุล", "งบกำไรขาดทุน"),
    ("ผังบัญชี", "สมุดรายวัน", "เดบิต", "เครดิต"),
    ("สินทรัพย์", "ทรัพย์สิน", "ค่าเสื่อม", "ค่าเสื่อมราคา"),
    ("บัตรคุม", "บัตรคุมสินค้า", "สต็อกการ์ด"),
    ("สิทธิ์", "กำหนดสิทธิ์"),
    ("รหัสเมนู", "เลขเมนู", "สารบัญเมนู", "ผังเมนู"),
    ("คีย์ลัด", "ปุ่มลัด", "shortcut", "shortcuts"),
    ("หาเมนูไม่เจอ", "เมนูหาย", "ซ่อนเมนู", "switchboard"),
    ("สต็อกไม่ตรง", "สต็อกเพี้ยน", "คำนวณยอดสต็อกใหม่"),
    ("พิมพ์ไม่ออก", "ปริ้นไม่ออก", "แบบฟอร์ม", "ตั้งค่าหน้ากระดาษ"),
    ("วิดีโอ", "คลิป", "คลิปสอน", "ยูทูป", "ยูทูบ", "youtube", "สอนใช้งาน"),
    ("ราคา", "แพ็กเกจ", "ค่าบริการ", "ใบเสนอราคา", "ทดลองใช้", "pricing"),
    ("สำนักงานบัญชี", "พาร์ตเนอร์", "partner"),
    ("ข้อเลือกพิเศษ", "ตั้งค่าพิเศษ", "1004"),
    ("ปรับราคา", "ศูนย์ราคา", "ตารางสูตรราคา", "สูตรราคา"),
    ("shopee", "lazada", "tiktok", "ช้อปปี้", "ลาซาด้า", "ติ๊กต็อก", "marketplace", "ใบกำกับอย่างย่อ", "abb"),
    ("เงินเดือน", "ประกันสังคม", "สปส", "ภงด1", "payroll"),
    ("dbd", "efiling", "สบช3", "taxonomy", "ส่งงบ", "กรมพัฒนาธุรกิจ"),
    ("นักท่องเที่ยว", "ภพ10", "evrt", "vrt", "คืนภาษีนักท่องเที่ยว"),
    ("สองภาษา", "แปลภาษา", "ภาษาอังกฤษ", "bilingual", "i18n"),
    ("ประวัติการพิมพ์", "พิมพ์ซ้ำ", "ต้นฉบับ", "สำเนา"),
    ("บัญชีย่อย", "กระทบยอดบัญชีย่อย", "subledger"),
    ("พักบิล", "ปิดกะ", "ลิ้นชัก", "จอลูกค้า", "เครื่องพิมพ์ใบเสร็จ"),
    ("ทุจริต", "เฝ้าระวัง", "จอพนักงาน", "fraud", "workmonitor"),
    ("ป้ายขนส่ง", "ป้ายชื่อขนส่ง", "สติกเกอร์บาร์โค้ด"),
    ("นิติบุคคล", "หลายนิติบุคคล", "บุคคลธรรมดา", "สำนักงานใหญ่"),
    ("ขายติดลบ", "สต็อกติดลบ", "ห้ามติดลบ", "สินค้าติดลบ", "negative"),
    ("รูปแบบเอกสาร", "เลขที่เอกสาร", "เล่มเอกสาร", "1014", "1015"),
    ("ออกแบบฟอร์ม", "พิมพ์เอกสาร", "ตัวอย่างก่อนพิมพ์", "1096", "1097"),
    ("ข้อมูลเริ่มต้น", "preset", "starter", "ค่าเริ่มต้น"),
    ("ปรับปรุงภาษา", "พจนานุกรมภาษา", "1005"),
    ("คิวงาน", "ประมวลผลเบื้องหลัง", "procqueue"),
    ("ปิดเครดิต", "วันปิดเครดิต"),
    ("เก็บเงิน", "ลูกหนี้", "วางบิล", "ค้างชำระ"),
    ("จ่ายเงิน", "เจ้าหนี้", "ครบกำหนด", "ค้างชำระ"),
    ("แจ้งเตือน", "เตือน", "ศูนย์แจ้งเตือน"),
    ("ครบกำหนด", "อายุหนี้", "เกินกำหนด"),
    ("คลาวด์", "cloud", "เซิร์ฟเวอร์", "server", "บนเว็บ"),
    ("ยอดขาย", "ขาย", "รายงานขาย"),
    ("ออกบิล", "บิล", "ขาย"),
    ("สาขา", "แยกสาขา", "สำนักงานใหญ่"),
    ("ยางรถยนต์", "ร้านยาง", "อู่ซ่อมรถ", "autocare", "ออโต้แคร์"),
)


def _build_synonym_map() -> dict[str, tuple[str, ...]]:
    tmp: dict[str, list[str]] = {}
    for grp in _BRAIN_SYNONYM_GROUPS:
        norm_grp = [w.strip().lower() for w in grp if w.strip()]
        for w in norm_grp:
            bucket = tmp.setdefault(w, [])
            for other in norm_grp:
                if other != w and other not in bucket:
                    bucket.append(other)
    return {k: tuple(v[:8]) for k, v in tmp.items()}


_SEARCH_SYNONYMS: dict[str, tuple[str, ...]] = _build_synonym_map()

_PHONE_FILLER_STOPWORDS = frozenset({
    "ครับ", "ค่ะ", "คะ", "คับ", "นะ", "นะครับ", "นะคะ", "เนี่ย", "เนี้ย", "อย่างเงี้ย", "อย่างงี้",
    "ตอนนี้", "พอดี", "อยากจะถาม", "อยากถาม", "ถามว่า", "เรื่อง", "ตัว", "อันนี้", "อันนั้น",
    "ได้ไหม", "ได้มั้ย", "หรือเปล่า", "รึเปล่า", "ไหม", "มั้ย", "ใช่ไหม", "ยังไง", "อย่างไร",
    "หน่อย", "เลย", "อ๋อ", "อ้อ", "เอ่อ", "อืม", "ผม", "ฉัน", "หนู", "คุณ", "พี่", "เขา", "มัน",
    "เป็น", "มี", "จะ", "ต้อง", "แล้ว", "ก็", "แต่", "ว่า", "คือ", "หรือ", "กับ", "ใน", "ที่",
    "ได้", "ให้", "ใช้", "ทำ", "ดู", "เอา", "มา", "ไป", "อยู่", "ถึง", "แบบ", "ของ", "และ",
    "เข้า", "ออก", "อีก", "เดียวกัน", "พัฒนา", "ขั้น", "ช่วงนี้", "อะไร", "เท่าไหร่", "เท่าไร",
    "รู้", "จำ", "จำได้", "บอก", "รอบ", "ครั้ง", "กี่", "นึง", "เงี้ย", "เดิน", "ถึงขั้น", "เท้า",
    "สริป", "หน่าย", "เป็นมัน", "กใช่ไหม", "แล้วก็", "หรือว่า", "คุย", "นิสัย", "ส่วนตัว", "อากาศ",
    "คน", "หา", "ดี", "ริ", "ออ",
})

_GENERIC_NOISE_TOKENS = frozenset({
    "โปรแกรม", "ระบบ", "ข้อมูล", "bc", "บีซี", "เอไอ", "แอคเคาท์", "บริษัท",
    "ลูกค้า", "ใช้งาน", "ปัญหา", "มีปัญหา", "ทำงาน", "ทำได้", "สามารถ", "ขั้นตอน", "วิธีการ", "รายละเอียด",
    "รายงาน", "เอกสาร", "รายการ", "หน้าจอ", "ทั้งหมด", "ทั่วไป", "ยืนยัน", "สอบถาม",
    "สรุป", "เวลา", "โทร", "ติดต่อ", "แยกกัน", "รวมกัน",
})

_VALID_SHORT_THAI_TOKENS = frozenset({"งบ", "ยา", "รถ", "บิล", "คลัง"})
_ASCII_TOKEN_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{1,}")

_META_NAV_SLUGS = frozenset({
    "index", "playbook-sales", "playbook-call-center", "website-manual-and-blog-directory", "video-visual-tutorials",
})

_BOOTSTRAP_HOT_CODES: tuple[str, ...] = (
    "1002", "1003", "1004", "1005", "1015", "1097", "1098",
    "2001", "2007", "2020", "2034", "2050",
    "3001", "3003", "3009", "3011", "3014", "3021", "3026",
    "4001", "4005", "4011", "4013", "4014", "4017", "4026",
    "5001", "5012", "5023",
    "6001", "6012", "6023", "6024", "6032",
    "7001", "7006", "7009", "7036", "7037",
    "8001", "9001", "9006", "9029",
)

_BOOTSTRAP_USER_SLUGS: tuple[str, ...] = (
    "sales-pricing-packages",
    "sales-support-implementation-contact",
    "start-first-day",
    "ts-common-period-closed",
    "ts-stock-negative-control",
    "ts-stock-cost-mismatch-rebuild",
    "ts-arap-balance-recalc",
    "ts-menu-missing-switchboard",
    "howto-sale-invoice",
    "howto-buy-invoice",
    "howto-ar-receipt-fifo",
    "howto-cheque-received",
    "howto-tax-vat-input-output",
    "howto-gl-period-open-close",
    "faq-stock-cost",
)

_BOOTSTRAP_DEV_SLUGS: tuple[str, ...] = (
    "dev-rules-accounting-sql",
    "doctypes-overview",
    "api-overview",
    "api-process-engine",
    "api-query-gateway",
    "arch-overview",
)

_THAI_STOPWORDS = frozenset(_pythai_stopwords()) if _pythai_stopwords else frozenset()


def _normalize_search_text(text: str) -> str:
    s = str(text or "").strip().lower()
    for pat, repl in _STT_SEARCH_NORMALIZE_RULES:
        s = pat.sub(repl, s)
    return " ".join(s.split())


def _is_thai_word(tok: str) -> bool:
    return any("\u0E00" <= ch <= "\u0E7F" for ch in tok)


def _tokenize_query(text: str, expand_synonyms: bool = True, keep_codes: bool = False) -> list[str]:
    norm = _normalize_search_text(text)
    if not norm:
        return []
    raw_toks: list[str] = []
    if _pythai_tokenize is not None:
        try:
            raw_toks.extend(_pythai_tokenize(norm, keep_whitespace=False))
        except Exception:
            raw_toks.extend(_WS_TOKEN_RE.findall(norm))
    else:
        raw_toks.extend(_WS_TOKEN_RE.findall(norm))
    raw_toks.extend(_ASCII_TOKEN_RE.findall(norm))
    if keep_codes:
        raw_toks.extend(_MENU_CODE_TOKEN_RE.findall(norm))
        raw_toks.extend(_LEGACY_CODE_TOKEN_RE.findall(norm))
    seen: set[str] = set()
    out: list[str] = []

    def _add(tok: str) -> None:
        t = re.sub(r"^[^\w\u0E00-\u0E7F]+|[^\w\u0E00-\u0E7F]+$", "", tok.lower())
        if len(t) < 2:
            return
        if t.isdigit() and not (keep_codes and len(t) == 4 and t[0] in "123456789"):
            return
        if _is_thai_word(t) and len(t) < 3 and t not in _VALID_SHORT_THAI_TOKENS:
            return
        if t in _THAI_STOPWORDS or t in _PHONE_FILLER_STOPWORDS:
            return
        if t not in seen:
            seen.add(t)
            out.append(t)

    for tok in raw_toks:
        _add(tok)
    if expand_synonyms:
        added_syn = 0
        for tok in list(out):
            for syn in _SEARCH_SYNONYMS.get(tok, ()):
                if syn not in seen and added_syn < 8:
                    _add(syn)
                    added_syn += 1
    return out


def _strip_see_also(body: str) -> str:
    parts = body.split("\n## ")
    if len(parts) <= 1:
        return body.strip()
    kept = [parts[0]]
    for part in parts[1:]:
        sec = "## " + part
        if _SEE_ALSO_HEADING_RE.match(sec.lstrip()):
            continue
        kept.append(sec)
    res = "\n".join(kept).rstrip()
    if res.endswith("\n---"):
        res = res[:-4].rstrip()
    return res


def _extract_tldr(title: str, clean_body: str, max_chars: int = 620) -> str:
    cleaned = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", clean_body or "")
    cleaned = re.sub(r"\[🎬[^\]]*\]\([^)]*\)", "", cleaned)
    cleaned = _WIKILINK_RE.sub(lambda m: m.group(1), cleaned).strip()
    lines = [ln.rstrip() for ln in cleaned.splitlines()]
    # Drop leading H1 duplicate of title
    if lines and lines[0].lstrip().startswith("# "):
        lines = lines[1:]
    joined = "\n".join(lines).strip()
    if len(joined) <= max_chars:
        return joined
    secs = [s.strip() for s in re.split(r"\n(?=##\s)", joined) if s.strip()]
    out = secs[0] if secs else joined
    if len(out) < max_chars // 2 and len(secs) > 1:
        out = f"{out}\n\n{secs[1]}"
    return out[:max_chars].rsplit("\n", 1)[0].strip() if len(out) > max_chars else out.strip()


def _extract_relevant_excerpt(title: str, body: str, tokens: list[str], max_chars: int) -> str:
    """Pick the most query-relevant section(s) of a note within max_chars."""
    clean = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", body or "")
    clean = re.sub(r"\[🎬[^\]]*\]\([^)]*\)", "", clean).strip()
    if len(clean) <= max_chars:
        return clean
    sub_toks = [t for t in tokens if t not in _GENERIC_NOISE_TOKENS] or tokens
    sections = [s.strip() for s in re.split(r"\n(?=##\s)", clean) if s.strip()]
    if not sections:
        return clean[:max_chars].strip()
    intro = sections[0] if not sections[0].startswith("## ") else ""
    scored_secs: list[tuple[float, int, str]] = []
    for idx, sec in enumerate(sections):
        if sec.startswith("## สื่อภาพและวิดีโอ"):
            continue
        sec_low = sec.lower()
        hits = sum(2.0 if t in sec_low else 0.0 for t in sub_toks)
        head = sec.splitlines()[0].lower() if sec.splitlines() else ""
        hits += sum(2.5 for t in sub_toks if t in head)
        scored_secs.append((hits, idx, sec))
    scored_secs.sort(key=lambda x: (-x[0], x[1]))
    picked: list[str] = []
    used = 0
    if intro and scored_secs and scored_secs[0][1] != 0:
        intro_short = intro[: min(220, max_chars // 3)].strip()
        if intro_short:
            picked.append(intro_short)
            used += len(intro_short) + 2
    for _, _, sec in scored_secs:
        if used >= max_chars:
            break
        remain = max_chars - used
        if remain < 120:
            break
        chunk = sec[:remain].strip()
        if chunk and chunk not in picked:
            picked.append(chunk)
            used += len(chunk) + 2
    return "\n\n".join(picked)[:max_chars].strip() or clean[:max_chars].strip()


def _extract_media(body: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    images: list[dict[str, str]] = []
    videos: list[dict[str, str]] = []
    seen_img: set[str] = set()
    seen_vid: set[str] = set()
    for alt, url in _MD_IMAGE_RE.findall(body or ""):
        if url not in seen_img:
            seen_img.add(url)
            images.append({"alt": alt.strip() or "ภาพประกอบ", "url": url.strip()})
    for label, url in _MD_LINK_RE.findall(body or ""):
        if _YT_URL_RE.search(url) and url not in seen_vid:
            seen_vid.add(url)
            videos.append({"title": label.strip().lstrip("🎬").strip() or "วิดีโอสอนใช้งาน", "url": url.strip()})
    return images, videos


def _parse_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    text = str(raw or "").replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    fm_block = text[4:end]
    body = text[end + 5 :].lstrip("\n")
    meta: dict[str, Any] = {}
    cur_list_key: str | None = None
    for line in fm_block.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- ") and cur_list_key:
            val = stripped[2:].strip().strip('"').strip("'")
            meta.setdefault(cur_list_key, []).append(val)
            continue
        cur_list_key = None
        if ":" not in line or line.startswith((" ", "\t")):
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if not val:
            cur_list_key = key
            meta.setdefault(key, [])
        elif val.startswith("[") and val.endswith("]"):
            items = [x.strip().strip('"').strip("'") for x in val[1:-1].split(",")]
            meta[key] = [x for x in items if x]
        else:
            meta[key] = val.strip('"').strip("'")
    return meta, body


def _extract_heading(body: str) -> str:
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("# ").strip()
    return ""


def _format_media_tail(note: dict[str, Any]) -> str:
    lines: list[str] = []
    vids = note.get("videos") or []
    imgs = note.get("images") or []
    if vids:
        lines.append("youtube_videos: " + " | ".join(f"{v['title']} -> {v['url']}" for v in vids[:2]))
    if imgs:
        lines.append("images: " + " | ".join(f"{i['alt']} -> {i['url']}" for i in imgs[:2]))
    return ("\n" + "\n".join(lines)) if lines else ""


def _dir_signature(vault_dir: str) -> tuple[int, float]:
    count = 0
    max_mtime = 0.0
    for root, _, files in os.walk(vault_dir):
        for fn in files:
            if not fn.endswith(".md"):
                continue
            count += 1
            try:
                mt = os.stat(os.path.join(root, fn)).st_mtime
                if mt > max_mtime:
                    max_mtime = mt
            except OSError:
                pass
    return count, max_mtime


class BrainVaultMirror:
    """In-Process Embedded BC Brain Engine (reads local bcbrain/vault with mtime hot-reload, zero MCP HTTP)."""

    def __init__(
        self,
        vault_pack_url: str = "",
        ttl_s: int = 1800,
        vault_dir: str | None = None,
        watch_interval_s: float = 3.0,
    ) -> None:
        self.url = (vault_pack_url or "").strip()
        self.vault_dir = os.path.abspath(vault_dir) if vault_dir else ""
        self.ttl_s = max(120, int(ttl_s))
        self.watch_interval_s = max(1.0, float(watch_interval_s))
        self._lock = threading.Lock()
        self._refreshing = False
        self._etag: str | None = None
        self._last_sync_m: float = 0.0
        self._last_watch_m: float = 0.0
        self._dir_sig: tuple[int, float] = (0, 0.0)
        self._version: str = "embedded-3.0"
        self._commit: str = "local"
        self._notes_by_slug: dict[str, dict[str, Any]] = {}
        self._menus_by_code: dict[str, dict[str, str]] = {}
        self._menus_by_legacy: dict[str, dict[str, str]] = {}
        self._menus_list: list[dict[str, str]] = []
        self._tables_by_name: dict[str, dict[str, str]] = {}
        self._error_entries: list[dict[str, Any]] = []
        self._idf: dict[str, float] = {}
        self._bootstrap_cache: dict[str, str] = {}

    @property
    def ready(self) -> bool:
        with self._lock:
            return bool(self._notes_by_slug)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "ready": bool(self._notes_by_slug),
                "mode": "local" if self.vault_dir and os.path.isdir(self.vault_dir) else "remote",
                "vaultDir": self.vault_dir,
                "version": self._version,
                "commit": self._commit,
                "etag": self._etag,
                "notes": len(self._notes_by_slug),
                "menus": len(self._menus_list),
                "errors": len(self._error_entries),
            }

    def load_notes_payload(self, obj: dict[str, Any], etag: str | None = None) -> dict[str, Any]:
        raw_notes = obj.get("notes") or []
        by_slug: dict[str, dict[str, Any]] = {}
        menus_by_code: dict[str, dict[str, str]] = {}
        menus_by_legacy: dict[str, dict[str, str]] = {}
        menus_list: list[dict[str, str]] = []
        tables_by_name: dict[str, dict[str, str]] = {}
        error_entries: list[dict[str, Any]] = []

        for item in raw_notes:
            if not isinstance(item, dict):
                continue
            slug = str(item.get("slug") or "").strip()
            folder = str(item.get("folder") or "").strip()
            raw = str(item.get("raw") or "")
            if not slug or not raw:
                continue
            meta, body = _parse_frontmatter(raw)
            fm_aud = str(meta.get("audience") or "").strip().lower()
            is_dev = (folder in DEV_FOLDERS) or (fm_aud == "dev") or (slug == "playbook-dev")
            title = str(meta.get("title") or "").strip() or _extract_heading(body) or slug
            aliases = [str(a).strip() for a in (meta.get("aliases") or []) if str(a).strip()]
            tags = [str(t).strip() for t in (meta.get("tags") or []) if str(t).strip()]
            clean_body = _strip_see_also(_SOURCE_LINE_RE.sub("", body).strip())
            tldr = _extract_tldr(title, clean_body)
            images, videos = _extract_media(body)
            out_links = list(dict.fromkeys(m.strip() for m in _WIKILINK_RE.findall(body) if m.strip() and m.strip() != slug))
            title_meta_low = f"{title} {' '.join(aliases)} {' '.join(tags)} {slug}".lower()
            aliases_low = [a.lower() for a in aliases]
            headings_low = " ".join(
                ln.lstrip("# ").strip().lower()
                for ln in clean_body.splitlines()
                if ln.lstrip().startswith("##")
            )
            body_low = clean_body.lower()

            note_rec = {
                "slug": slug,
                "folder": folder,
                "audience": "dev" if is_dev else "user",
                "is_dev": is_dev,
                "is_meta_nav": slug in _META_NAV_SLUGS or slug.startswith("menu-cat-"),
                "title": title,
                "aliases": aliases,
                "aliases_low": aliases_low,
                "tags": tags,
                "body": clean_body,
                "tldr": tldr,
                "images": images,
                "videos": videos,
                "out_links": out_links,
                "backlinks": [],
                "title_meta_low": title_meta_low,
                "headings_low": headings_low,
                "body_low": body_low,
                "search_hay": f"{title_meta_low}\n{body_low}",
                "raw": raw,
            }
            by_slug[slug] = note_rec

            if slug.startswith("menu-cat-"):
                for line in body.splitlines():
                    m = _MENU_ROW_RE.match(line.strip())
                    if not m:
                        continue
                    code, legacy, m_title, m_path, m_edition = [
                        (g or "").strip() for g in m.groups()
                    ]
                    rec = {
                        "code": code,
                        "legacy": legacy,
                        "title": m_title,
                        "path": m_path,
                        "edition": m_edition,
                        "catalogSlug": slug,
                    }
                    menus_by_code[code] = rec
                    if legacy:
                        menus_by_legacy[legacy] = rec
                    menus_list.append(rec)

            if folder == "schema":
                for tbl in _TABLE_IDENT_RE.findall(f"{title} {slug} {body[:1500]}"):
                    if tbl not in tables_by_name and not tbl.startswith(("bc_user_list", "bc_mcp_", "pg_")):
                        tables_by_name[tbl] = {"table": tbl, "schemaSlug": slug, "schemaTitle": title}

            if folder == "troubleshoot" or "แจ้งเตือน" in title or "error" in title.lower():
                cands = [title] + aliases + _QUOTED_ERR_RE.findall(clean_body)[:6]
                seen_err: set[str] = set()
                for c in cands:
                    norm_c = _normalize_search_text(c)
                    if len(norm_c) >= 5 and norm_c not in seen_err:
                        seen_err.add(norm_c)
                        error_entries.append({
                            "errorText": c.strip(),
                            "norm": norm_c,
                            "slug": slug,
                            "title": title,
                            "folder": folder,
                            "audience": "dev" if is_dev else "user",
                            "is_dev": is_dev,
                            "fixSnippet": tldr[:320],
                        })

        # Build backlinks for 1-hop graph traversal
        for slug, note in by_slug.items():
            for target in note["out_links"]:
                t_note = by_slug.get(target)
                if t_note is not None and slug not in t_note["backlinks"]:
                    t_note["backlinks"].append(slug)

        now_m = time.monotonic()
        with self._lock:
            self._notes_by_slug = by_slug
            self._menus_by_code = menus_by_code
            self._menus_by_legacy = menus_by_legacy
            self._menus_list = menus_list
            self._tables_by_name = tables_by_name
            self._error_entries = error_entries
            self._idf = {}
            self._bootstrap_cache = {}
            self._version = str(obj.get("version") or self._version or "embedded-3.0")
            self._commit = str(obj.get("syncCommit") or "local")
            if etag:
                self._etag = etag
            self._last_sync_m = now_m
            self._last_watch_m = now_m
        _tokenize_query("warmup", expand_synonyms=False)
        return self.stats()

    def load_from_dir(self, vault_dir: str | None = None, force: bool = False) -> dict[str, Any]:
        target = os.path.abspath(vault_dir or self.vault_dir or DEFAULT_LOCAL_VAULT_DIR)
        if not os.path.isdir(target):
            return self.stats()
        sig = _dir_signature(target)
        with self._lock:
            if not force and self.vault_dir == target and self._notes_by_slug and sig == self._dir_sig:
                self._last_watch_m = time.monotonic()
                return self.stats()
        notes: list[dict[str, str]] = []
        hasher = hashlib.sha256()
        for root, dirs, files in os.walk(target):
            dirs.sort()
            for fn in sorted(files):
                if not fn.endswith(".md"):
                    continue
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, target).replace("\\", "/")
                folder = rel.split("/", 1)[0] if "/" in rel else ""
                slug = fn[:-3]
                try:
                    with open(full, encoding="utf-8") as f:
                        raw = f.read()
                except OSError:
                    continue
                notes.append({"slug": slug, "folder": folder, "raw": raw})
                hasher.update(rel.encode("utf-8"))
                hasher.update(str(len(raw)).encode("ascii"))
        self.vault_dir = target
        etag = f'"local-vault-{len(notes)}-{hasher.hexdigest()[:12]}"'
        st = self.load_notes_payload({"version": "embedded-3.0", "syncCommit": "local", "notes": notes}, etag=etag)
        with self._lock:
            self._dir_sig = sig
        return st

    def sync(self, force: bool = False) -> dict[str, Any]:
        # Local vault takes priority whenever configured and present (zero network calls)
        if self.vault_dir and os.path.isdir(self.vault_dir):
            return self.load_from_dir(self.vault_dir, force=force)
        if not self.url:
            if os.path.isdir(DEFAULT_LOCAL_VAULT_DIR):
                self.vault_dir = DEFAULT_LOCAL_VAULT_DIR
                return self.load_from_dir(self.vault_dir, force=force)
            return self.stats()
        now_m = time.monotonic()
        with self._lock:
            if not force and self._notes_by_slug and (now_m - self._last_sync_m) < self.ttl_s:
                return self.stats()
            prev_etag = self._etag
        try:
            headers = {"Accept": "application/json", "Accept-Encoding": "gzip"}
            if prev_etag:
                headers["If-None-Match"] = prev_etag
            r = httpx.get(self.url, headers=headers, timeout=15.0)
            if r.status_code == 304 and self.ready:
                with self._lock:
                    self._last_sync_m = time.monotonic()
                return self.stats()
            if r.status_code == 200 and r.content:
                raw_bytes = r.content
                if raw_bytes[:2] == b"\x1f\x8b":
                    raw_bytes = gzip.decompress(raw_bytes)
                obj = json.loads(raw_bytes.decode("utf-8"))
                return self.load_notes_payload(obj, etag=r.headers.get("etag") or prev_etag)
        except Exception as e:
            log.warning("BC Brain vault-pack sync skipped (%s)", type(e).__name__)
        return self.stats()

    def maybe_refresh(self) -> None:
        now_m = time.monotonic()
        if self.vault_dir and os.path.isdir(self.vault_dir):
            with self._lock:
                if (now_m - self._last_watch_m) < self.watch_interval_s:
                    return
                self._last_watch_m = now_m
                prev_sig = self._dir_sig
            sig = _dir_signature(self.vault_dir)
            if sig != prev_sig:
                st = self.load_from_dir(self.vault_dir, force=True)
                log.info("Local BC Brain hot-reloaded: %d notes, %d menus", st["notes"], st["menus"])
            return
        if not self.url:
            return
        with self._lock:
            stale = not self._notes_by_slug or (now_m - self._last_sync_m) >= self.ttl_s
            if not stale or self._refreshing:
                return
            self._refreshing = True

        def _worker() -> None:
            try:
                self.sync(force=True)
            finally:
                with self._lock:
                    self._refreshing = False

        threading.Thread(target=_worker, name="bcbrain-mirror-sync", daemon=True).start()

    def get_bootstrap_pack(self, audience: str = "user") -> str:
        self.maybe_refresh()
        aud = "dev" if (audience or "user").strip().lower() == "dev" else "user"
        with self._lock:
            cached = self._bootstrap_cache.get(aud)
            if cached:
                return cached
            notes_by_slug = dict(self._notes_by_slug)
            menus_by_code = dict(self._menus_by_code)
            tables_by_name = dict(self._tables_by_name)
            menu_count = len(self._menus_list)
        if not notes_by_slug:
            return ""
        lines: list[str] = []
        if aud == "user":
            lines.extend([
                "# BC Brain Bootstrap Pack (Embedded Local — User / Call Center)",
                f"source: local bcbrain/vault · {len(notes_by_slug)} notes · {menu_count} screens (1001–9070)",
                "กฎตอบลูกค้า: ภาษาไทยเข้าใจง่ายเป็นขั้นตอน ระบุชื่อเมนู+รหัส 4 หลัก ห้ามพูดชื่อตาราง SQL/โค้ด หากเกินข้อมูลให้เรียก quick_answer(audience:\"user\") หรือ lookup_menu",
                "",
                "## รหัสเมนูยอดฮิต (Hot 4-Digit Menu Codes)",
            ])
            for code in _BOOTSTRAP_HOT_CODES:
                m = menus_by_code.get(code)
                if m:
                    lines.append(f"- **{m['code']}** {m['title']} ({m['path']})")
            lines.extend(["", "## สรุปสั้นหัวข้อหลัก (TL;DR)"])
            for slug in _BOOTSTRAP_USER_SLUGS:
                n = notes_by_slug.get(slug)
                if not n or n["is_dev"]:
                    continue
                lines.extend([f"### {n['title']} [{n['slug']}]", n["tldr"], ""])
            lines.append("สื่อทางการ: https://bcaccount.com · ใบเสนอราคา: https://bcaccount.com/quotation · LINE @bcaccount")
        else:
            lines.extend([
                "# BC Brain Bootstrap Pack (Embedded Local — Developer / SQL / Schema / API)",
                "กฎเหล็ก: PostgreSQL NUMERIC (ห้าม float/double), กรอง COALESCE(last_status, 0) = 0 เสมอ",
                "",
                "## ตารางหลัก",
            ])
            for t in (
                "ic_trans", "ic_trans_detail", "ic_inventory", "ic_warehouse", "ic_shelf",
                "ar_customer", "ap_supplier", "cb_trans", "gl_journal", "erp_option",
            ):
                ent = tables_by_name.get(t)
                if ent:
                    lines.append(f"- `{ent['table']}` → `{ent['schemaSlug']}` ({ent['schemaTitle']})")
            lines.extend(["", "## สรุปสั้นสถาปัตยกรรมและกฎกลาง (TL;DR)"])
            for slug in _BOOTSTRAP_DEV_SLUGS:
                n = notes_by_slug.get(slug)
                if n:
                    lines.extend([f"### {n['title']} [{n['slug']}]", n["tldr"], ""])
        text = "\n".join(lines).strip()[:20000]
        with self._lock:
            self._bootstrap_cache[aud] = text
        return text

    def lookup_menu(self, query: str, audience: str = "user", limit: int = 5) -> str | None:
        self.maybe_refresh()
        aud = (audience or "user").strip().lower() or "user"
        q = (query or "").strip()
        if not q:
            return None
        with self._lock:
            if not self._menus_list and not self._tables_by_name:
                return None
            by_code = self._menus_by_code
            by_legacy = self._menus_by_legacy
            all_menus = self._menus_list
            tables = self._tables_by_name

        if aud != "user":
            ql = q.lower()
            if aud == "dev":
                tbl_hits = [
                    v for k, v in tables.items()
                    if ql == k or ql in k or k in ql
                ][:limit]
                if tbl_hits:
                    lines = [f"## Tables ({len(tbl_hits)})"]
                    for r in tbl_hits:
                        lines.append(f"- `{r['table']}` → `{r['schemaSlug']}` ({r['schemaTitle']})")
                    return "\n".join(lines)
                return None

        matches: list[dict[str, str]] = []
        seen_codes: set[str] = set()

        def _add(rec: dict[str, str] | None) -> None:
            if rec and rec["code"] not in seen_codes and len(matches) < limit:
                seen_codes.add(rec["code"])
                matches.append(rec)

        for code in _MENU_CODE_TOKEN_RE.findall(q):
            _add(by_code.get(code))
        for leg in _LEGACY_CODE_TOKEN_RE.findall(q):
            _add(by_legacy.get(leg))

        if len(matches) < limit:
            ql = q.lower()
            for rec in all_menus:
                if ql in rec["title"].lower() or ql in rec["path"].lower():
                    _add(rec)
                    if len(matches) >= limit:
                        break
        if len(matches) < limit:
            tokens = [t for t in (_tokenize_query(q) or _WS_TOKEN_RE.findall(q.lower())) if len(t) >= 2]
            if tokens:
                scored: list[tuple[int, dict[str, str]]] = []
                for rec in all_menus:
                    if rec["code"] in seen_codes:
                        continue
                    hay = f"{rec['title']} {rec['path']}".lower()
                    score = sum(1 for tok in tokens if tok in hay)
                    if score > 0:
                        scored.append((score, rec))
                scored.sort(key=lambda x: -x[0])
                for _, rec in scored:
                    _add(rec)
                    if len(matches) >= limit:
                        break

        if not matches:
            return None
        lines = [f"## Menus ({len(matches)})"]
        for r in matches:
            leg_part = f" ({r['legacy']})" if r["legacy"] else ""
            path_part = f"{r['path']} · " if r["path"] else ""
            ed_part = r["edition"]
            lines.append(f"- **{r['code']}**{leg_part} **{r['title']}** — {path_part}{ed_part}".rstrip(" —·"))
        return "\n".join(lines)

    def lookup_error(self, query: str, audience: str = "user", limit: int = 5) -> str:
        self.maybe_refresh()
        aud = (audience or "user").strip().lower() or "user"
        q = (query or "").strip()
        if not q:
            return f'🛑 Abstain — No verified BC error message found for "" (audience={aud}).'
        q_norm = _normalize_search_text(q)
        tokens = _tokenize_query(q, expand_synonyms=True)
        with self._lock:
            entries = [e for e in self._error_entries if aud != "user" or not e["is_dev"]]
        scored: list[tuple[float, dict[str, Any]]] = []
        seen_slugs: set[str] = set()
        for ent in entries:
            score = 0.0
            if q_norm and (q_norm in ent["norm"] or ent["norm"] in q_norm):
                score += 40.0
            for tok in tokens:
                if tok in ent["norm"]:
                    score += 8.0
                elif tok in ent["fixSnippet"].lower():
                    score += 2.0
            if score >= 12.0:
                scored.append((score, ent))
        scored.sort(key=lambda x: -x[0])
        picked: list[dict[str, Any]] = []
        for _, ent in scored:
            if ent["slug"] in seen_slugs:
                continue
            seen_slugs.add(ent["slug"])
            picked.append(ent)
            if len(picked) >= limit:
                break
        if not picked:
            return f'🛑 Abstain — No verified BC error message found for "{q}" (audience={aud}).'
        lines = [f"[lookup_error | audience={aud} | hits={len(picked)}]", ""]
        for p in picked:
            lines.append(f"## {p['title']} (slug: {p['slug']})\n{p['fixSnippet']}")
        return "\n\n".join(lines)

    def read_note(self, slug: str, audience: str = "user") -> str | None:
        self.maybe_refresh()
        clean_slug = (slug or "").strip()
        if not clean_slug:
            return None
        with self._lock:
            note = self._notes_by_slug.get(clean_slug)
        if not note:
            return None
        aud = (audience or "user").strip().lower()
        if aud == "user" and note["is_dev"]:
            return None
        header = f"slug: {note['slug']}\n"
        media_tail = _format_media_tail(note) if aud != "dev" else ""
        return f"{header}# {note['title']}\n\n{note['body']}{media_tail}" if aud == "user" else note["raw"]

    def batch_read(
        self,
        slugs: list[str],
        audience: str = "user",
        query: str = "",
        max_chars_per_note: int = 2000,
    ) -> str | None:
        self.maybe_refresh()
        clean_slugs = [str(s or "").strip() for s in (slugs or []) if str(s or "").strip()][:5]
        if not clean_slugs:
            return None
        aud = (audience or "user").strip().lower()
        cap = max(300, min(int(max_chars_per_note or 2000), 6000))
        q_toks = _tokenize_query(query, expand_synonyms=True) if query else []
        chunks: list[str] = []
        with self._lock:
            if not self._notes_by_slug:
                return None
            for s in clean_slugs:
                note = self._notes_by_slug.get(s)
                if not note:
                    return None
                if aud == "user" and note["is_dev"]:
                    continue
                meta_line = f"slug: {note['slug']} | folder: {note['folder']} | audience: {note['audience']}"
                text = _extract_relevant_excerpt(note["title"], note["body"], q_toks, cap) if q_toks else note["body"][:cap]
                if note["title"] and not text.lstrip().startswith("#"):
                    chunks.append(f"# {note['title']}\n{meta_line}\n\n{text}")
                else:
                    chunks.append(f"{meta_line}\n{text}")
        if not chunks:
            return "🛑 Abstain: ไม่พบโน้ตในโซนผู้ใช้งาน"
        return "\n\n---\n\n".join(chunks)

    def _token_idf(self, tokens: list[str], notes: list[dict[str, Any]]) -> dict[str, float]:
        n_docs = max(len(notes), 1)
        out: dict[str, float] = {}
        missing: list[str] = []
        with self._lock:
            for tok in tokens:
                if tok in self._idf:
                    out[tok] = self._idf[tok]
                else:
                    missing.append(tok)
        if missing:
            computed: dict[str, float] = {}
            for tok in missing:
                df = sum(1 for note in notes if tok in note["search_hay"])
                computed[tok] = round(math.log((1.0 + n_docs) / (1.0 + df)) + 1.0, 3)
            with self._lock:
                self._idf.update(computed)
            out.update(computed)
        return out

    def search_with_meta(
        self,
        query: str,
        limit: int = 8,
        audience: str = "user",
    ) -> dict[str, Any]:
        """Multi-signal BM25-weighted search with confidence & OOV coverage guard."""
        self.maybe_refresh()
        q = (query or "").strip()
        aud = (audience or "user").strip().lower() or "user"
        if not q:
            return {"hits": [], "confidence": "low", "total_terms": 0, "best_matched_terms": 0, "top_score": 0}

        primary_tokens = _tokenize_query(q, expand_synonyms=False, keep_codes=True)
        expanded_tokens = _tokenize_query(q, expand_synonyms=True, keep_codes=True)
        syn_only = [t for t in expanded_tokens if t not in primary_tokens]
        menu_codes = _MENU_CODE_TOKEN_RE.findall(q)
        phrase = _normalize_search_text(q)

        if not primary_tokens and not expanded_tokens:
            return {"hits": [], "confidence": "low", "total_terms": 0, "best_matched_terms": 0, "top_score": 0}

        with self._lock:
            notes = [
                n for n in self._notes_by_slug.values()
                if aud == "all" or (aud == "dev" and n["is_dev"]) or (aud == "user" and not n["is_dev"])
            ]
            menus_by_code = self._menus_by_code

        if not notes:
            return {"hits": [], "confidence": "low", "total_terms": len(primary_tokens), "best_matched_terms": 0, "top_score": 0}

        # OOV 4-digit menu trap: if user asks specifically about an unknown 4-digit menu code (e.g. 9999)
        if menu_codes and all(c not in menus_by_code for c in menu_codes) and len(primary_tokens) <= 3:
            return {"hits": [], "confidence": "low", "total_terms": len(primary_tokens), "best_matched_terms": 0, "top_score": 0}

        all_toks = primary_tokens + syn_only
        idf = self._token_idf(all_toks, notes)
        substantive_primary = [t for t in primary_tokens if t not in _GENERIC_NOISE_TOKENS] or primary_tokens
        sub_set = set(substantive_primary)

        scored: list[tuple[float, int, int, dict[str, Any]]] = []
        for note in notes:
            title_meta = note["title_meta_low"]
            headings = note["headings_low"]
            body_low = note["body_low"]
            score = 0.0
            primary_hits = 0
            sub_hits = 0

            # Exact phrase / alias hit boost
            if len(phrase) >= 4 and phrase in title_meta:
                score += 28.0
            elif any(a and len(a) >= 4 and (a in phrase or phrase in a) for a in note["aliases_low"]):
                score += 24.0

            # Direct 4-digit menu code boost
            for code in menu_codes:
                if code in body_low or code in title_meta:
                    score += 32.0
                    if menus_by_code.get(code, {}).get("catalogSlug") == note["slug"]:
                        score += 10.0

            for tok in primary_tokens:
                w = idf.get(tok, 1.0)
                if tok in _GENERIC_NOISE_TOKENS:
                    w *= 0.28
                matched = False
                if tok in title_meta:
                    score += 11.5 * w
                    matched = True
                if tok in headings:
                    score += 7.0 * w
                    matched = True
                if tok in body_low:
                    tf = min(body_low.count(tok), 6)
                    score += (2.8 + 1.1 * math.log1p(tf)) * w
                    matched = True
                if matched:
                    primary_hits += 1
                    if tok in sub_set:
                        sub_hits += 1

            for tok in syn_only:
                w = idf.get(tok, 1.0) * 0.45
                if tok in title_meta:
                    score += 5.5 * w
                elif tok in headings:
                    score += 3.2 * w
                elif tok in body_low:
                    score += 1.4 * w

            if primary_hits == 0 and score < 15.0:
                continue
            if len(substantive_primary) >= 2 and sub_hits >= 2:
                score += 6.0 * (sub_hits - 1)
            if note["is_meta_nav"] and not menu_codes:
                score *= 0.35 if note["slug"] in _META_NAV_SLUGS else 0.55

            int_score = int(round(score))
            if int_score >= 8:
                scored.append((float(int_score), sub_hits, primary_hits, note))

        scored.sort(key=lambda x: (-x[0], -x[1], -x[2], x[3]["slug"]))
        top_items = scored[: max(1, int(limit))]
        if not top_items:
            return {"hits": [], "confidence": "low", "total_terms": len(substantive_primary), "best_matched_terms": 0, "top_score": 0}

        hits: list[dict[str, Any]] = []
        for sc, _, _, note in top_items:
            hits.append({
                "slug": note["slug"],
                "title": note["title"],
                "folder": note["folder"],
                "audience": note["audience"],
                "tags": note["tags"],
                "score": int(sc),
                "snippet": _extract_relevant_excerpt(note["title"], note["body"], expanded_tokens, 280).replace("\n", " ")[:280],
                "images": note["images"][:3],
                "videos": note["videos"][:3],
            })

        top_score = hits[0]["score"]
        best_sub_hits = top_items[0][1]
        total_sub = max(1, len(substantive_primary))
        coverage = best_sub_hits / total_sub

        confidence = "low"
        if (coverage >= 0.6 and top_score >= 42) or (total_sub == 1 and top_score >= 28) or (coverage >= 1.0 and top_score >= 25):
            confidence = "high"
        elif coverage >= 0.5 and top_score >= 24:
            confidence = "medium"

        return {
            "hits": hits,
            "confidence": confidence,
            "total_terms": total_sub,
            "best_matched_terms": best_sub_hits,
            "top_score": top_score,
        }

    def quick_answer(
        self,
        question: str,
        audience: str = "user",
        mode: str = "ultra",
        max_chars: int | None = None,
        include_media: bool = False,
    ) -> str:
        self.maybe_refresh()
        q = (question or "").strip()
        aud = (audience or "user").strip().lower() or "user"
        m = (mode or "ultra").strip().lower()
        default_budget = 700 if m == "ultra" else (1400 if m == "fast" else 2000)
        budget = max(250, min(int(max_chars or default_budget), 4000))

        # Direct 4-digit menu question short-circuit when user specifically queries a known code
        codes = _MENU_CODE_TOKEN_RE.findall(q)
        if codes and len(_tokenize_query(q, expand_synonyms=False)) <= 3:
            menu_hit = self.lookup_menu(q, audience=aud, limit=5)
            if menu_hit:
                meta = self.search_with_meta(q, limit=2, audience=aud)
                if meta["hits"] and meta["confidence"] != "low":
                    with self._lock:
                        top_note = self._notes_by_slug.get(meta["hits"][0]["slug"])
                    if top_note and not top_note["slug"].startswith("menu-cat-"):
                        body = _extract_relevant_excerpt(top_note["title"], top_note["body"], _tokenize_query(q), max(350, budget - len(menu_hit)))
                        return (
                            f"[quick_answer:{m} | audience={aud} | confidence=high | slug: {top_note['slug']}]\n"
                            f"{menu_hit}\n\n# {top_note['title']}\n{body}"
                        )
                return f"[quick_answer:{m} | audience={aud} | confidence=high | slug: menu-catalog]\n{menu_hit}"

        res = self.search_with_meta(q, limit=3, audience=aud)
        hits = res["hits"]
        confidence = res["confidence"]
        total_terms = res["total_terms"]
        best_matched = res["best_matched_terms"]
        top_score = res["top_score"]

        if not hits:
            return f"🛑 Abstain — No matching notes (audience={aud}). Do not guess—try lookup_menu, lookup_error, or search_brain."

        coverage = best_matched / max(1, total_terms)
        if confidence == "low" and (coverage < 0.5 or top_score < 24):
            cands = "\n".join(f"- {h['slug']} ({h['folder']}) — {h['title']}" for h in hits[:3])
            return (
                f"🛑 Abstain — Insufficient Grounded Evidence in BC Brain "
                f"(audience={aud}, confidence=low, coverage={best_matched}/{total_terms}, topScore={top_score}) for: {q}\n\n"
                f"Do NOT extrapolate or guess. State clearly that BC Brain has no verified record for this question.\n"
                f"Closest candidates:\n{cands}"
            )

        toks = _tokenize_query(q, expand_synonyms=True, keep_codes=True)
        with self._lock:
            top = self._notes_by_slug.get(hits[0]["slug"])
            second = self._notes_by_slug.get(hits[1]["slug"]) if len(hits) > 1 else None
        if not top:
            return f"🛑 Abstain — No matching notes (audience={aud})."

        if m == "ultra":
            body = _extract_relevant_excerpt(top["title"], top["body"], toks, budget)
            media_line = _format_media_tail(top) if (include_media and aud != "dev") else ""
            return (
                f"[quick_answer:ultra | audience={aud} | confidence={confidence} | slug: {top['slug']}]\n"
                f"# {top['title']}\n{body}{media_line}"
            )

        use_second = (
            second is not None
            and not (confidence == "high" and hits[0]["score"] >= hits[1]["score"] * 1.25)
            and hits[1]["score"] >= top_score * 0.72
        )
        picked = [top, second] if use_second and second else [top]
        caps = [budget] if len(picked) == 1 else [int(budget * 0.65), max(450, int(budget * 0.35))]
        blocks: list[str] = []
        for note, cap in zip(picked, caps):
            excerpt = _extract_relevant_excerpt(note["title"], note["body"], toks, cap)
            blocks.append(f"# {note['title']}\nslug: {note['slug']} | folder: {note['folder']} | audience: {note['audience']}\n\n{excerpt}")
        media_line = _format_media_tail(top) if (include_media and aud != "dev") else ""
        header_tag = "quick_answer:fast" if m == "fast" else "quick_answer"
        return (
            f"[{header_tag} | audience={aud} | confidence={confidence} | coverage={best_matched}/{total_terms}]\n\n"
            + "\n\n---\n\n".join(blocks)
            + media_line
        )

    def batch_ask(
        self,
        questions: list[str],
        audience: str = "user",
        mode: str = "ultra",
        max_chars_per_answer: int | None = None,
        include_media: bool = False,
    ) -> str:
        clean_qs = [str(x or "").strip() for x in (questions or []) if str(x or "").strip()][:5]
        if not clean_qs:
            return "🛑 Abstain — No questions provided."
        aud = (audience or "user").strip().lower() or "user"
        m = (mode or "ultra").strip().lower()
        blocks = []
        for idx, q in enumerate(clean_qs, 1):
            ans = self.quick_answer(q, audience=aud, mode=m, max_chars=max_chars_per_answer, include_media=include_media)
            blocks.append(f"## Q{idx}: {q}\n{ans}")
        return f"[batch_ask | count={len(clean_qs)} | audience={aud} | mode={m}]\n\n" + "\n\n---\n\n".join(blocks)

    def search_brain(self, query: str, audience: str = "user", limit: int = 8) -> str:
        res = self.search_with_meta(query, limit=limit, audience=audience)
        return json.dumps(res["hits"], ensure_ascii=False)

    def ask_brain(self, question: str, audience: str = "user", compact: bool = True, max_notes: int = 2) -> str:
        mode = "fast" if compact else "normal"
        return self.quick_answer(question, audience=audience, mode=mode, max_chars=1600 if compact else 2800, include_media=True)

    def traverse_brain(self, slug: str, depth: int = 1, audience: str = "user", limit: int = 12) -> str:
        self.maybe_refresh()
        start_slug = (slug or "").strip()
        aud = (audience or "user").strip().lower() or "user"
        max_depth = max(1, min(int(depth or 1), 2))
        max_limit = max(1, min(int(limit or 12), 25))
        with self._lock:
            start = self._notes_by_slug.get(start_slug)
            if not start or (aud == "user" and start["is_dev"]):
                return f"🛑 Abstain — Note not found: {start_slug}"
            visited = {start_slug}
            q = deque([(start_slug, 0)])
            found: list[str] = []
            while q and len(found) < max_limit:
                cur, d = q.popleft()
                if d >= max_depth:
                    continue
                node = self._notes_by_slug.get(cur)
                if not node:
                    continue
                for direction, neighbors in (("out", node["out_links"]), ("in", node["backlinks"])):
                    for nb in neighbors:
                        if nb in visited:
                            continue
                        nb_node = self._notes_by_slug.get(nb)
                        if not nb_node or (aud == "user" and nb_node["is_dev"]):
                            continue
                        visited.add(nb)
                        found.append(f"- [{d + 1}-hop {direction}] **{nb_node['slug']}** ({nb_node['folder']}) — {nb_node['title']}")
                        if len(found) >= max_limit:
                            break
                        q.append((nb, d + 1))
        if not found:
            return f"No connected notes for {start_slug} (audience={aud})."
        return f"## Graph around {start_slug} ({len(found)})\n" + "\n".join(found)

    def list_notes(self, folder: str = "", audience: str = "user") -> str:
        self.maybe_refresh()
        aud = (audience or "user").strip().lower() or "user"
        f_filter = (folder or "").strip().lower()
        with self._lock:
            rows = [
                n for n in self._notes_by_slug.values()
                if (aud == "all" or (aud == "dev" and n["is_dev"]) or (aud == "user" and not n["is_dev"]))
                and (not f_filter or n["folder"].lower() == f_filter)
            ]
        rows.sort(key=lambda n: (n["folder"], n["slug"]))
        lines = [f"## Notes ({len(rows)}, audience={aud})"]
        for n in rows[:80]:
            lines.append(f"- `{n['slug']}` ({n['folder'] or 'root'}) — {n['title']}")
        return "\n".join(lines)

    def find_media(self, query: str, audience: str = "user", limit: int = 8) -> str:
        res = self.search_with_meta(query, limit=10, audience=audience)
        items: list[dict[str, str]] = []
        seen: set[str] = set()
        for h in res["hits"]:
            for v in h.get("videos") or []:
                if v["url"] not in seen and len(items) < limit:
                    seen.add(v["url"])
                    items.append({"kind": "youtube", "title": v["title"], "url": v["url"], "noteSlug": h["slug"], "noteTitle": h["title"]})
            for im in h.get("images") or []:
                if im["url"] not in seen and len(items) < limit:
                    seen.add(im["url"])
                    items.append({"kind": "image", "title": im["alt"], "url": im["url"], "noteSlug": h["slug"], "noteTitle": h["title"]})
        return json.dumps({"query": query, "count": len(items), "items": items}, ensure_ascii=False)

    def dispatch_tool(self, name: str, args: dict[str, Any] | None = None) -> str:
        a = args or {}
        aud = str(a.get("audience") or "user").strip() or "user"
        if name == "quick_answer":
            return self.quick_answer(
                str(a.get("question") or ""),
                audience=aud,
                mode=str(a.get("mode") or "ultra"),
                max_chars=a.get("max_chars"),
                include_media=bool(a.get("include_media", False)),
            )
        if name == "batch_ask":
            return self.batch_ask(
                a.get("questions") or [],
                audience=aud,
                mode=str(a.get("mode") or "ultra"),
                max_chars_per_answer=a.get("max_chars_per_answer"),
                include_media=bool(a.get("include_media", False)),
            )
        if name == "lookup_menu":
            return self.lookup_menu(str(a.get("query") or ""), audience=aud, limit=int(a.get("limit") or 5)) or (
                f'No direct menu code or SQL table match for "{a.get("query", "")}" (audience={aud}).'
            )
        if name == "lookup_error":
            return self.lookup_error(str(a.get("query") or ""), audience=aud, limit=int(a.get("limit") or 5))
        if name == "batch_read":
            return self.batch_read(
                a.get("slugs") or [],
                audience=aud,
                query=str(a.get("query") or ""),
                max_chars_per_note=int(a.get("max_chars_per_note") or 2000),
            ) or "🛑 Abstain: ไม่พบโน้ตที่ระบุ"
        if name == "read_note":
            return self.read_note(str(a.get("slug") or ""), audience=aud) or f"🛑 Abstain: ไม่พบโน้ต {a.get('slug', '')}"
        if name == "search_brain":
            return self.search_brain(str(a.get("query") or ""), audience=aud, limit=int(a.get("limit") or 8))
        if name == "ask_brain":
            return self.ask_brain(str(a.get("question") or ""), audience=aud, compact=bool(a.get("compact", True)))
        if name == "traverse_brain":
            return self.traverse_brain(str(a.get("slug") or ""), depth=int(a.get("depth") or 1), audience=aud, limit=int(a.get("limit") or 12))
        if name == "list_notes":
            return self.list_notes(str(a.get("folder") or ""), audience=aud)
        if name == "find_media":
            return self.find_media(str(a.get("query") or ""), audience=aud, limit=int(a.get("limit") or 8))
        raise KeyError(f"Unknown local brain tool: {name}")

    def search_nearby(
        self,
        query: str,
        audience: str = "user",
        limit_notes: int = 2,
        limit_menus: int = 4,
        max_chars_per_note: int = 650,
        exclude_slugs: set[str] | None = None,
        min_score: float = 4.0,
    ) -> dict[str, Any] | None:
        """Find closest verified BC Brain notes & menus (<1ms) when direct QA abstains, keeping strict user/dev isolation."""
        self.maybe_refresh()
        q = (query or "").strip()
        if not q:
            return None
        aud = (audience or "user").strip().lower() or "user"
        tokens = _tokenize_query(q, expand_synonyms=True)
        if not tokens:
            return None
        substantive = [t for t in tokens if t not in _GENERIC_NOISE_TOKENS]
        if not substantive:
            return None

        with self._lock:
            notes = [
                n for n in self._notes_by_slug.values()
                if (aud != "user" or not n["is_dev"]) and (not exclude_slugs or n["slug"] not in exclude_slugs)
            ]
            all_menus = list(self._menus_list) if aud == "user" else []
        if not notes and not all_menus:
            return None

        idf = self._token_idf(tokens, notes)
        sub_set = set(substantive)
        scored_notes: list[tuple[float, int, dict[str, Any]]] = []
        for note in notes:
            title_meta = note["title_meta_low"]
            headings = note["headings_low"]
            body_low = note["body_low"]
            score = 0.0
            sub_hits = 0
            for tok in tokens:
                w = idf.get(tok, 1.0)
                if tok in _GENERIC_NOISE_TOKENS:
                    w *= 0.25
                matched = False
                if tok in title_meta:
                    score += 3.5 * w
                    matched = True
                if tok in headings:
                    score += 2.2 * w
                    matched = True
                if tok in body_low:
                    score += 1.0 * w
                    matched = True
                if matched and tok in sub_set:
                    sub_hits += 1
            if sub_hits < 1:
                continue
            if sub_hits >= 2:
                score += 1.2 * (sub_hits - 1)
            if note["is_meta_nav"]:
                score *= 0.25 if note["slug"] in _META_NAV_SLUGS else 0.4
            if score >= min_score:
                scored_notes.append((score, sub_hits, note))

        scored_notes.sort(key=lambda x: (-x[0], -x[1], x[2]["slug"]))
        picked_notes = [n for _, _, n in scored_notes[: max(0, int(limit_notes))]]
        top_score = scored_notes[0][0] if scored_notes else 0.0

        scored_menus: list[tuple[float, int, dict[str, str]]] = []
        for rec in all_menus:
            title_low = rec["title"].lower()
            path_low = rec["path"].lower()
            score = 0.0
            sub_hits = 0
            for tok in substantive:
                w = idf.get(tok, 1.0)
                if tok in title_low:
                    score += 3.0 * w
                    sub_hits += 1
                elif tok in path_low:
                    score += 1.5 * w
                    sub_hits += 1
            if sub_hits >= 1 and score >= max(2.5, min_score * 0.6):
                scored_menus.append((score, sub_hits, rec))
        scored_menus.sort(key=lambda x: (-x[0], -x[1], x[2]["code"]))
        picked_menus = [r for _, _, r in scored_menus[: max(0, int(limit_menus))]]
        if scored_menus:
            top_score = max(top_score, scored_menus[0][0])

        if not picked_notes and not picked_menus:
            return None

        cap = max(220, min(int(max_chars_per_note or 650), 2000))
        parts = [
            "[ข้อมูลใกล้เคียงจากคลังความรู้ BC (ไม่ใช่คำตอบตรงเป๊ะ — เล่าเฉพาะข้อเท็จจริงที่มีอยู่จริงด้านล่าง ห้ามเดา ห้ามแต่งเติม)]"
        ]
        for note in picked_notes:
            excerpt = _extract_relevant_excerpt(note["title"], note["body"], tokens, cap)
            parts.append(f"## โน้ตใกล้เคียง: {note['title']} (slug: {note['slug']})\n{excerpt}")
        if picked_menus:
            menu_lines = ["## เมนูใกล้เคียงที่เกี่ยวข้อง:"]
            for r in picked_menus:
                leg_part = f" ({r['legacy']})" if r["legacy"] else ""
                path_part = f"{r['path']} · " if r["path"] else ""
                ed_part = r["edition"]
                menu_lines.append(
                    f"- **{r['code']}**{leg_part} **{r['title']}** — {path_part}{ed_part}".rstrip(" —·")
                )
            parts.append("\n".join(menu_lines))

        return {
            "text": "\n\n".join(parts),
            "slugs": [n["slug"] for n in picked_notes],
            "menus": [r["code"] for r in picked_menus],
            "top_score": round(top_score, 2),
        }

    def fallback_quick_answer(self, question: str, audience: str = "user", max_chars: int = 700) -> str | None:
        q = (question or "").strip()
        if not q or (audience or "user") != "user":
            return None
        tokens = _tokenize_query(q, expand_synonyms=True) or _WS_TOKEN_RE.findall(q.lower())
        if not tokens:
            return None
        with self._lock:
            notes = [n for n in self._notes_by_slug.values() if not n["is_dev"]]
        best_score = 0
        best_note: dict[str, Any] | None = None
        for note in notes:
            title_meta = note["title_meta_low"]
            head_body = note["body_low"]
            score = 0
            for tok in tokens:
                if len(tok) < 2:
                    continue
                if tok in title_meta:
                    score += 3
                elif tok in head_body:
                    score += 1
            if note["is_meta_nav"]:
                score = int(score * 0.5)
            if score > best_score:
                best_score = score
                best_note = note
        if not best_note or best_score < 3:
            return None
        cap = max(200, min(int(max_chars or 700), 2500))
        excerpt = _extract_relevant_excerpt(best_note["title"], best_note["body"], tokens, cap)
        return (
            f"[quick_answer:mirror-fallback | audience=user | slug: {best_note['slug']}]\n"
            f"# {best_note['title']}\n{excerpt}"
        )


class LocalBrainClient:
    """Drop-in in-process replacement for MCPClient backed directly by BrainVaultMirror (0 HTTP/MCP calls)."""

    def __init__(self, mirror: BrainVaultMirror) -> None:
        self.mirror = mirror
        self.url = f"local://{mirror.vault_dir or DEFAULT_LOCAL_VAULT_DIR}"

    def read_only_tools(self) -> list[dict[str, Any]]:
        aud_prop = {"type": "string", "enum": ["user", "dev", "all"], "default": "user", "description": "โซนคลังความรู้ (สายลูกค้าใช้ user เสมอ)"}
        return [
            {
                "name": "quick_answer",
                "description": "ตอบคำถาม BC Ai Account แบบเร็วสุดจากสมองในเครื่อง (<2ms, mode ultra สำหรับสายโทรศัพท์)",
                "annotations": {"readOnlyHint": True},
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "คำถามภาษาไทยหรืออังกฤษ"},
                        "audience": aud_prop,
                        "mode": {"type": "string", "enum": ["ultra", "fast", "normal"], "default": "ultra"},
                        "max_chars": {"type": "integer"},
                        "include_media": {"type": "boolean", "default": False},
                    },
                    "required": ["question"],
                },
            },
            {
                "name": "batch_ask",
                "description": "ตอบคำถามหลายประเด็น (1-5 ข้อ) ในรอบเดียวจากสมองในเครื่อง",
                "annotations": {"readOnlyHint": True},
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "questions": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
                        "audience": aud_prop,
                        "mode": {"type": "string", "enum": ["ultra", "fast", "normal"], "default": "ultra"},
                        "include_media": {"type": "boolean", "default": False},
                    },
                    "required": ["questions"],
                },
            },
            {
                "name": "lookup_menu",
                "description": "ค้นรหัสเมนู 4 หลัก (1001-9070) หรือชื่อเมนูของ BC Ai Account ใน <1ms",
                "annotations": {"readOnlyHint": True},
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "รหัสเมนู 4 หลัก รหัสเดิม หรือชื่อเมนู"},
                        "audience": aud_prop,
                        "limit": {"type": "integer", "default": 5},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "lookup_error",
                "description": "ค้นข้อความแจ้งเตือน Error บนหน้าจอโปรแกรมและวิธีแก้ไข",
                "annotations": {"readOnlyHint": True},
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "ข้อความแจ้งเตือนหรืออาการที่พบ"},
                        "audience": aud_prop,
                        "limit": {"type": "integer", "default": 5},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "batch_read",
                "description": "อ่านหลายโน้ตพร้อมกันตาม slug (สูงสุด 5 ใบ)",
                "annotations": {"readOnlyHint": True},
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "slugs": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
                        "audience": aud_prop,
                        "query": {"type": "string"},
                        "max_chars_per_note": {"type": "integer", "default": 2000},
                    },
                    "required": ["slugs"],
                },
            },
            {
                "name": "read_note",
                "description": "อ่านโน้ตเต็ม 1 ใบตาม slug",
                "annotations": {"readOnlyHint": True},
                "inputSchema": {
                    "type": "object",
                    "properties": {"slug": {"type": "string"}, "audience": aud_prop},
                    "required": ["slug"],
                },
            },
            {
                "name": "search_brain",
                "description": "ค้นรายชื่อโน้ตและคะแนนความเกี่ยวข้องในคลังความรู้ BC",
                "annotations": {"readOnlyHint": True},
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}, "audience": aud_prop, "limit": {"type": "integer", "default": 8}},
                    "required": ["query"],
                },
            },
            {
                "name": "ask_brain",
                "description": "ตอบคำถามละเอียดจากหลายโน้ตในคลังความรู้ BC",
                "annotations": {"readOnlyHint": True},
                "inputSchema": {
                    "type": "object",
                    "properties": {"question": {"type": "string"}, "audience": aud_prop, "compact": {"type": "boolean", "default": True}},
                    "required": ["question"],
                },
            },
            {
                "name": "traverse_brain",
                "description": "ดูโน้ตที่เชื่อมโยงกับ slug ที่ระบุ",
                "annotations": {"readOnlyHint": True},
                "inputSchema": {
                    "type": "object",
                    "properties": {"slug": {"type": "string"}, "depth": {"type": "integer", "default": 1}, "audience": aud_prop, "limit": {"type": "integer", "default": 12}},
                    "required": ["slug"],
                },
            },
            {
                "name": "list_notes",
                "description": "แสดงรายชื่อโน้ตในคลังความรู้ตามหมวด",
                "annotations": {"readOnlyHint": True},
                "inputSchema": {
                    "type": "object",
                    "properties": {"folder": {"type": "string"}, "audience": aud_prop},
                },
            },
            {
                "name": "find_media",
                "description": "ค้นหาภาพและวิดีโอ YouTube สอนใช้งานจากคลังความรู้ BC",
                "annotations": {"readOnlyHint": True},
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}, "audience": aud_prop, "limit": {"type": "integer", "default": 8}},
                    "required": ["query"],
                },
            },
        ]

    def call(self, name: str, args: dict[str, Any] | None = None) -> str:
        return self.mirror.dispatch_tool(name, args)

    def call_batch(self, items: list[tuple[str, dict[str, Any]]]) -> list[str]:
        return [self.mirror.dispatch_tool(name, args) for name, args in items]


_SHARED_LOCAL_BRAIN_LOCK = threading.Lock()
_SHARED_LOCAL_BRAIN: BrainVaultMirror | None = None


def get_local_brain(vault_dir: str | None = None) -> BrainVaultMirror:
    """Singleton accessor shared by J.A.R.V.I.S and livekit-voice-ai (0 network calls)."""
    global _SHARED_LOCAL_BRAIN
    target = os.path.abspath(vault_dir or os.environ.get("BC_BRAIN_VAULT_DIR") or DEFAULT_LOCAL_VAULT_DIR)
    with _SHARED_LOCAL_BRAIN_LOCK:
        if _SHARED_LOCAL_BRAIN is None:
            _SHARED_LOCAL_BRAIN = BrainVaultMirror(vault_dir=target)
            _SHARED_LOCAL_BRAIN.load_from_dir(target, force=True)
        elif _SHARED_LOCAL_BRAIN.vault_dir != target and os.path.isdir(target):
            _SHARED_LOCAL_BRAIN.load_from_dir(target, force=True)
        return _SHARED_LOCAL_BRAIN
