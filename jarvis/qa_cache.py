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
# Context-dependent pronouns / follow-ups that MUST NOT be answered from standalone cache
_CONTEXT_DEP_RE = re.compile(
    r"(?:อันนี้|อันนั้น|ตัวนี้|ตัวนั้น|เรื่องนี้|เรื่องนั้น|แบบนั้น|แบบนี้|เมื่อกี้|ต่อเลย|แล้วมัน|แล้วถ้า|มันทำ|ของผม|ของฉัน|ของหนู|บริษัทผม|ร้านผม|เบอร์|ชื่อผม|ชื่อฉัน|"
    r"\b(?:this one|that one|it|previous|continue|my company|my phone|my name)\b)",
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
    if not raw or len(raw) > 160:
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
                    if isinstance(v, dict) and v.get("sentences") and now - float(v.get("saved_at", 0)) < self.ttl_s
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
        if not is_cacheable_question(question):
            return None
        norm = normalize_question(question)
        bucket = f"{lang or 'th'}|{'phone' if phone_mode else 'web'}"
        exact_key = f"{bucket}|{norm}"
        now = time.time()

        with self._lock:
            hit = self._entries.get(exact_key)
            if hit and now - float(hit.get("saved_at", 0)) < self.ttl_s:
                hit["hits"] = int(hit.get("hits", 0)) + 1
                hit["last_hit_at"] = now
                self.hits += 1
                return dict(hit)

            # High-precision fuzzy match within same language & mode bucket (>= 0.88 similarity)
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

    def store(self, question: str, sentences: list[str], lang: str = "th", phone_mode: bool = True) -> bool:
        if not is_cacheable_question(question):
            return False
        clean_sents = [s.strip() for s in (sentences or []) if s and s.strip()]
        if not clean_sents:
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
            # Evict least-recently-used entries if exceeding max_entries
            if len(self._entries) > self.max_entries:
                ordered = sorted(
                    self._entries.items(),
                    key=lambda kv: float(kv[1].get("last_hit_at", kv[1].get("saved_at", 0))),
                )
                for old_k, _ in ordered[: len(self._entries) - self.max_entries]:
                    self._entries.pop(old_k, None)
            self._save()
        return True

    def stats(self) -> dict:
        with self._lock:
            return {
                "entries": len(self._entries),
                "hits": self.hits,
                "misses": self.misses,
            }
