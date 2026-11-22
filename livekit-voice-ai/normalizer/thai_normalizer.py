"""Thai Text Normalization & Sentence Chunker สำหรับเสียงโทรศัพท์ (Low-Latency TTS)."""

from __future__ import annotations

import re

THAI_DIGITS = {
    "0": "ศูนย์",
    "1": "หนึ่ง",
    "2": "สอง",
    "3": "สาม",
    "4": "สี่",
    "5": "ห้า",
    "6": "หก",
    "7": "เจ็ด",
    "8": "แปด",
    "9": "เก้า",
}

THAI_NUM_WORDS = ["", "หนึ่ง", "สอง", "สาม", "สี่", "ห้า", "หก", "เจ็ด", "แปด", "เก้า"]
THAI_PLACES = ["", "สิบ", "ร้อย", "พัน", "หมื่น", "แสน", "ล้าน"]

ABBREVIATIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bJ\.A\.R\.V\.I\.S\b", re.I), "เจาร์วิส"),
    (re.compile(r"\bBC\s*Ai\s*Account\b", re.I), "บีซี เอไอ แอคเคาท์"),
    (re.compile(r"\bBC\s*Call\s*Center\b", re.I), "บีซี คอลเซ็นเตอร์"),
    (re.compile(r"\bVAT\b", re.I), "แวต"),
    (re.compile(r"\bWHT\b", re.I), "ภาษีหัก ณ ที่จ่าย"),
    (re.compile(r"\bSME\b", re.I), "เอสเอ็มอี"),
    (re.compile(r"\bERP\b", re.I), "อีอาร์พี"),
    (re.compile(r"\bPOS\b", re.I), "พีโอเอส"),
    (re.compile(r"\bPDF\b", re.I), "พีดีเอฟ"),
    (re.compile(r"\bQR\b", re.I), "คิวอาร์"),
    (re.compile(r"\bAI\b"), "เอไอ"),
    (re.compile(r"\bID\b"), "ไอดี"),
    (re.compile(r"บจก\."), "บริษัทจำกัด "),
    (re.compile(r"หจก\."), "ห้างหุ้นส่วนจำกัด "),
    (re.compile(r"บมจ\."), "บริษัทมหาชนจำกัด "),
    (re.compile(r"บ\./เดือน"), "บาทต่อเดือน"),
    (re.compile(r"บ\./ปี"), "บาทต่อปี"),
    (re.compile(r"(\d)\s*บ\."), r"\1 บาท"),
    (re.compile(r"น\."), "นาฬิกา"),
    (re.compile(r"กม\."), "กิโลเมตร"),
    (re.compile(r"ชั่วโมง|ชม\."), "ชั่วโมง"),
    (re.compile(r"ฯลฯ"), "และอื่นๆ"),
]


def int_to_thai(n: int) -> str:
    """แปลงจำนวนเต็มเป็นคำอ่านภาษาไทยที่ถูกต้องตามหลักภาษา."""
    if n == 0:
        return "ศูนย์"
    if n < 0:
        return "ลบ" + int_to_thai(-n)
    if n >= 1_000_000:
        millions, rem = divmod(n, 1_000_000)
        return int_to_thai(millions) + "ล้าน" + (int_to_thai(rem) if rem else "")

    digits = [int(c) for c in str(n)]
    length = len(digits)
    parts: list[str] = []
    for idx, d in enumerate(digits):
        pos = length - idx - 1
        if d == 0:
            continue
        if pos == 0 and d == 1 and length > 1:
            parts.append("เอ็ด")
        elif pos == 1 and d == 2:
            parts.append("ยี่สิบ")
        elif pos == 1 and d == 1:
            parts.append("สิบ")
        else:
            parts.append(THAI_NUM_WORDS[d] + THAI_PLACES[pos])
    return "".join(parts)


def decimal_to_thai(num_str: str) -> str:
    """แปลงเลขทศนิยม (เช่น 1,250.75) เป็นคำอ่านไทย."""
    clean = num_str.replace(",", "")
    if "." not in clean:
        return int_to_thai(int(clean))
    whole, frac = clean.split(".", 1)
    frac_words = " ".join(THAI_DIGITS.get(c, c) for c in frac)
    return f"{int_to_thai(int(whole))}จุด{frac_words}"


def _read_phone_match(m: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", m.group(0))
    if len(digits) == 10:
        groups = [digits[:3], digits[3:6], digits[6:]]
    elif len(digits) == 9:
        groups = [digits[:2], digits[2:5], digits[5:]]
    else:
        groups = [digits]
    spoken_groups = [" ".join(THAI_DIGITS[c] for c in g) for g in groups]
    return " , ".join(spoken_groups)


def _read_baht_match(m: re.Match[str]) -> str:
    raw = m.group("num").replace(",", "")
    if "." in raw:
        whole, frac = raw.split(".", 1)
        satang = int(frac.ljust(2, "0")[:2])
        baht_part = f"{int_to_thai(int(whole))}บาท"
        return f"{baht_part}{int_to_thai(satang)}สตางค์" if satang > 0 else f"{baht_part}ถ้วน"
    return f"{int_to_thai(int(raw))}บาท"


def _read_time_match(m: re.Match[str]) -> str:
    h = int(m.group(1))
    minute = int(m.group(2))
    if minute == 0:
        return f"{int_to_thai(h)}นาฬิกา"
    return f"{int_to_thai(h)}นาฬิกา{int_to_thai(minute)}นาที"


def strip_markdown_and_tables(text: str) -> str:
    """ลบ Markdown, ตาราง และสัญลักษณ์ที่ไม่ควรส่งเข้า TTS."""
    # ลบเส้นคั่นตาราง |---|---|
    text = re.sub(r"^\s*\|?[\s:-]+\|[\s|:-]*$", " ", text, flags=re.MULTILINE)
    # แทนที่ท่อตาราง | ด้วยวรรค
    text = text.replace("|", " ")
    # ลบ markdown heading, bold, italic, code
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"[#*_`~>]", "", text)
    # ลบ bullet ต้นบรรทัด
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.MULTILINE)
    return text


def normalize_thai_for_speech(text: str) -> str:
    """แปลงข้อความภาษาไทยให้พร้อมอ่านออกเสียงผ่าน TTS อย่างเป็นธรรมชาติ."""
    if not text:
        return ""

    out = strip_markdown_and_tables(text)

    # 1. เบอร์โทรศัพท์ไทย (02-xxx-xxxx, 08x-xxx-xxxx, 09x-xxx-xxxx, 06x-xxx-xxxx)
    out = re.sub(r"(?<!\d)0[2689]\d(?:[- ]?\d{3,4}){1,2}(?!\d)", _read_phone_match, out)

    # 2. เวลา (เช่น 09:30, 14:00 น.)
    out = re.sub(r"\b([01]?\d|2[0-3]):([0-5]\d)(?:\s*น\.)?", _read_time_match, out)

    # 3. ราคาเงินบาท (฿1,990 หรือ 1,990 บาท)
    out = re.sub(r"฿\s*(?P<num>\d[\d,]*(?:\.\d{1,2})?)", _read_baht_match, out)
    out = re.sub(r"(?P<num>\d[\d,]*(?:\.\d{1,2})?)\s*บาท", _read_baht_match, out)

    # 4. เปอร์เซ็นต์
    out = re.sub(
        r"(\d[\d,]*(?:\.\d+)?)\s*%",
        lambda m: f"{decimal_to_thai(m.group(1))}เปอร์เซ็นต์",
        out,
    )

    # 5. ช่วงตัวเลข (เช่น 1-5 คน -> หนึ่งถึงห้า คน)
    out = re.sub(
        r"(\d[\d,]*)\s*[-–]\s*(\d[\d,]*)",
        lambda m: f"{int_to_thai(int(m.group(1).replace(',', '')))}ถึง{int_to_thai(int(m.group(2).replace(',', '')))}",
        out,
    )

    # 6. ตัวย่อและศัพท์เฉพาะ
    for pattern, replacement in ABBREVIATIONS:
        out = pattern.sub(replacement, out)

    # 7. ตัวเลขทั่วไปที่เหลือ
    out = re.sub(
        r"\b\d[\d,]*(?:\.\d+)?\b",
        lambda m: decimal_to_thai(m.group(0)),
        out,
    )

    # เก็บกวาดช่องว่างซ้ำ
    out = re.sub(r"\s+", " ", out).strip()
    return out


def chunk_thai_sentences(text: str, max_chars: int = 110) -> list[str]:
    """แบ่งประโยคภาษาไทยเป็นท่อนสั้นๆ เพื่อให้ Streaming TTS เล่นท่อนแรกได้เร็วที่สุด."""
    cleaned = normalize_thai_for_speech(text)
    if not cleaned:
        return []

    raw_parts = re.split(r"(?<=[.!?…])\s+|\n+|(?<=ครับ)\s+|(?<=ค่ะ)\s+", cleaned)
    chunks: list[str] = []
    buf = ""
    for part in raw_parts:
        p = part.strip()
        if not p:
            continue
        if len(buf) + len(p) + 1 <= max_chars:
            buf = f"{buf} {p}".strip()
        else:
            if buf:
                chunks.append(buf)
            if len(p) <= max_chars:
                buf = p
            else:
                words = p.split(" ")
                sub = ""
                for w in words:
                    if len(sub) + len(w) + 1 <= max_chars:
                        sub = f"{sub} {w}".strip()
                    else:
                        if sub:
                            chunks.append(sub)
                        sub = w
                buf = sub
    if buf:
        chunks.append(buf)
    return chunks
