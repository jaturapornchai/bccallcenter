"""Persistent local FAQ / DeepSeek response cache to save LLM & Judge tokens on repeated questions."""
from __future__ import annotations

import difflib
import json
import logging
import os
import re
import threading
import time

log = logging.getLogger("voice")

# Filler suffixes & punctuation stripped before matching so "ราคาเท่าไหร่ครับ" == "ราคาเท่าไหร่คะ" == "ราคาเท่าไหร่"
_POLITE_RE = re.compile(
    r"(?:นะครับ|นะคะ|นะคับ|ครับผม|ครับ|ค่ะ|คะ|คับ|จ้า|จ๊ะ|หน่อย|ด้วย|หรือเปล่า|หรือปล่าว|รึเปล่า|ไหม|มั้ย|เหรอ|หรอ|ล่ะ|ละ|เลย|อะ|อ่ะ)\s*$",
    re.I,
)
_STRIP_PUNCT_RE = re.compile(r"[\s.,!?;:\"'()\[\]{}\-_/\\~`?؟。、「」！？]+")
# Context-dependent pronouns / personal / callback / follow-ups that MUST NOT use standalone cache
_CONTEXT_DEP_RE = re.compile(
    r"(?:อันนี้|อันนั้น|ตัวนี้|ตัวนั้น|เรื่องนี้|เรื่องนั้น|แบบนั้น|แบบนี้|เมื่อกี้|ต่อเลย|แล้วมัน|แล้วถ้า|มันทำ|"
    r"ของผม|ของฉัน|ของหนู|บริษัทผม|ร้านผม|เบอร์|ชื่อผม|ชื่อฉัน|ผมชื่อ|ฉันชื่อ|หนูชื่อ|ชื่อว่า|ชื่ออะไร|จำผม|จำชื่อ|จำได้ไหม|"
    r"โทรกลับ|ติดต่อกลับ|ฝากเรื่อง|สาขาอื่น|ไปใช้สาขา|ใช่เลย|ใช่แค่|"
    r"ฉันนี้|ช่วยคนหาน็อต|หลังจากที่ตกลง|ดูดวง|"
    r"\b(?:this one|that one|it|previous|continue|my company|my phone|my name|who am i|call me back|callback|"
    r"thank you|thanks|what's that|who can speak|you can speak|mm-hmm|hello|hi there|how are you|how you there)\b)",
    re.I,
)
_PERSONAL_REPLY_RE = re.compile(
    r"(?:ยังไม่รู้ชื่อ|ขอเรียกชื่อ|ยินดีที่ได้รู้จัก|มองไม่เห็นเบอร์|ไม่ค่อยเข้าใจคำถาม|ฝากเรื่องไว้ให้|ขอเบอร์ที่สะดวก|ติดต่อกลับไป|"
    r"ยังไม่มีข้อมูลยืนยัน|ไม่มีข้อมูลยืนยัน|ไม่พบข้อมูล|abstain|โทรกลับ|ฝ่ายขายโทร|พนักงานโทร|เจ้าหน้าที่ติดต่อกลับ|"
    r"ดูดวง|สำหรับร้านอาหารใหม่|เข้าใจแล้วครับคุณ|ยินดีครับคุณ|ลุงจืด|คุณ[ก-๙]{2,}|"
    r"uncle\s+\w+|not quite sure|not sure what|don't have any info|you're (?:very )?welcome|speak up a bit louder|sorry,)",
    re.I,
)
_PERSONAL_NUM_RE = re.compile(r"\d{4,}")


def normalize_question(text: str) -> str:
    s = (text or "").strip().lower()
    if not s:
        return ""
    # Repeatedly trim trailing polite particles
    for _ in range(3):
        ns = _POLITE_RE.sub("", s).strip()
        if ns == s:
            break
        s = ns
    return _STRIP_PUNCT_RE.sub("", s)


def is_cacheable_question(text: str) -> bool:
    """Only cache standalone, general FAQ questions (never short acks, personal info, or context follow-ups)."""
    raw = (text or "").strip()
    if not raw or len(raw) > 90:
        return False
    if _CONTEXT_DEP_RE.search(raw) or _PERSONAL_NUM_RE.search(raw):
        return False
    norm = normalize_question(raw)
    return len(norm) >= 8


class QACache:
    """Thread-safe JSON-backed FAQ cache with exact + high-threshold fuzzy matching and TTL."""

    def __init__(self, path: str, ttl_s: int = 7 * 24 * 3600, max_entries: int = 500) -> None:
        self.path = path
        self.ttl_s = max(300, int(ttl_s))
        self.max_entries = max(50, int(max_entries))
        self._lock = threading.Lock()
        self._entries: dict[str, dict] = {}
        self.hits = 0
        self.misses = 0
        self._load()

    def _load(self) -> None:
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                now = time.time()
                self._entries = {
                    k: v for k, v in data.items()
                    if isinstance(v, dict) and v.get("sentences")
                    and (v.get("taught") or now - float(v.get("saved_at", 0)) < self.ttl_s)
                }
        except Exception:
            log.warning("QA cache load failed, starting fresh")
            self._entries = {}

    def _save(self) -> None:
        if not self.path:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = f"{self.path}.{os.getpid()}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._entries, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass

    def lookup(self, question: str, lang: str = "th", phone_mode: bool = True) -> dict | None:
        raw = (question or "").strip()
        if not raw:
            return None
        norm = normalize_question(raw)
        if len(norm) < 4:
            return None
        allow_auto = is_cacheable_question(raw)
        bucket = f"{lang or 'th'}|{'phone' if phone_mode else 'web'}"
        taught_bucket = f"{lang or 'th'}|taught"
        now = time.time()

        with self._lock:
            # 1. Check Admin-Taught FAQ first (exact then fuzzy >= 0.80)
            t_hit = self._entries.get(f"{taught_bucket}|{norm}")
            if t_hit:
                t_hit["hits"] = int(t_hit.get("hits", 0)) + 1
                t_hit["last_hit_at"] = now
                self.hits += 1
                return dict(t_hit)

            best_t_score = 0.0
            best_t_entry = None
            for k, entry in self._entries.items():
                if not k.startswith(taught_bucket + "|"):
                    continue
                cand_norm = entry.get("norm", "")
                if not cand_norm:
                    continue
                ratio = difflib.SequenceMatcher(None, norm, cand_norm).ratio()
                if ratio > best_t_score:
                    best_t_score = ratio
                    best_t_entry = entry
            if best_t_entry is not None and best_t_score >= 0.80:
                best_t_entry["hits"] = int(best_t_entry.get("hits", 0)) + 1
                best_t_entry["last_hit_at"] = now
                self.hits += 1
                return dict(best_t_entry)

            if not allow_auto:
                return None

            # 2. Auto-cached general FAQ (exact then fuzzy >= 0.88)
            exact_key = f"{bucket}|{norm}"
            hit = self._entries.get(exact_key)
            if hit and now - float(hit.get("saved_at", 0)) < self.ttl_s:
                hit["hits"] = int(hit.get("hits", 0)) + 1
                hit["last_hit_at"] = now
                self.hits += 1
                return dict(hit)

            best_score = 0.0
            best_entry = None
            for k, entry in self._entries.items():
                if not k.startswith(bucket + "|"):
                    continue
                if now - float(entry.get("saved_at", 0)) >= self.ttl_s:
                    continue
                cand_norm = entry.get("norm", "")
                if not cand_norm or abs(len(cand_norm) - len(norm)) > max(4, int(len(norm) * 0.25)):
                    continue
                ratio = difflib.SequenceMatcher(None, norm, cand_norm).ratio()
                if ratio > best_score:
                    best_score = ratio
                    best_entry = entry

            if best_entry is not None and best_score >= 0.88:
                best_entry["hits"] = int(best_entry.get("hits", 0)) + 1
                best_entry["last_hit_at"] = now
                self.hits += 1
                return dict(best_entry)

            self.misses += 1
            return None

    def teach(self, question: str, answer: str, lang: str = "th") -> dict:
        """Admin-verified 1-click FAQ teaching: persists permanently (never evicted by auto-cache)."""
        q = (question or "").strip()[:240]
        a = (answer or "").strip()[:800]
        if len(q) < 3 or len(a) < 3:
            raise ValueError("กรุณาระบุคำถามและคำตอบอย่างน้อย 3 ตัวอักษร")
        norm = normalize_question(q)
        if len(norm) < 3:
            raise ValueError("คำถามสั้นเกินไป")
        # Split answer into 1-3 crisp spoken sentences
        raw_parts = [p.strip() for p in re.split(r"[\r\n]+|(?<=[.!?])\s+", a) if p.strip()]
        sentences = raw_parts[:4] if raw_parts else [a]
        bucket = f"{lang or 'th'}|taught"
        key = f"{bucket}|{norm}"
        now = time.time()
        entry = {
            "key": key,
            "question": q,
            "norm": norm,
            "lang": lang or "th",
            "mode": "taught",
            "taught": True,
            "sentences": sentences,
            "saved_at": now,
            "last_hit_at": now,
            "hits": 0,
        }
        with self._lock:
            self._entries[key] = entry
            self._save()
        return dict(entry)

    def delete_entry(self, key: str) -> bool:
        with self._lock:
            if key in self._entries:
                del self._entries[key]
                self._save()
                return True
        return False

    def list_entries(self, taught_only: bool = False) -> list[dict]:
        with self._lock:
            items = []
            for k, v in self._entries.items():
                if taught_only and not v.get("taught"):
                    continue
                items.append({
                    "key": k,
                    "question": v.get("question", ""),
                    "answer": " ".join(v.get("sentences") or []),
                    "lang": v.get("lang", "th"),
                    "mode": v.get("mode", "phone"),
                    "taught": bool(v.get("taught", False)),
                    "hits": int(v.get("hits", 0)),
                    "saved_at": float(v.get("saved_at", 0)),
                })
            items.sort(key=lambda x: (not x["taught"], -x["saved_at"]))
            return items[:120]

    def store(self, question: str, sentences: list[str], lang: str = "th", phone_mode: bool = True, caller_name: str = "") -> bool:
        if not is_cacheable_question(question):
            return False
        clean_sents = [s.strip() for s in (sentences or []) if s and s.strip()]
        if not clean_sents:
            return False
        joined = " ".join(clean_sents)
        if _PERSONAL_REPLY_RE.search(joined):
            return False
        cname = (caller_name or "").strip()
        if cname and len(cname) >= 2 and cname in joined:
            return False
        norm = normalize_question(question)
        bucket = f"{lang or 'th'}|{'phone' if phone_mode else 'web'}"
        key = f"{bucket}|{norm}"
        now = time.time()

        with self._lock:
            self._entries[key] = {
                "question": question.strip(),
                "norm": norm,
                "lang": lang or "th",
                "mode": "phone" if phone_mode else "web",
                "sentences": clean_sents,
                "saved_at": now,
                "last_hit_at": now,
                "hits": 0,
            }
            # Evict least-recently-used auto entries (never evict admin-taught entries)
            if len(self._entries) > self.max_entries:
                ordered = sorted(
                    ((k, v) for k, v in self._entries.items() if not v.get("taught")),
                    key=lambda kv: float(kv[1].get("last_hit_at", kv[1].get("saved_at", 0))),
                )
                for old_k, _ in ordered[: max(0, len(self._entries) - self.max_entries)]:
                    self._entries.pop(old_k, None)
            self._save()
        return True

    def stats(self) -> dict:
        with self._lock:
            taught_count = sum(1 for v in self._entries.values() if v.get("taught"))
            return {
                "entries": len(self._entries),
                "taught_entries": taught_count,
                "hits": self.hits,
                "misses": self.misses,
            }
