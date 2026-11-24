"""Local <1 ms RAM Vault Mirror for BC Brain v2.3.0 (/api/vault-pack ETag gzip sync).

Keeps an in-memory snapshot of all BC Brain notes and O(1) 4-digit menu catalog indexes
strictly inside d:\\yalecom so lookup_menu, batch_read, read_note, and network-failover
fallback run in sub-millisecond latency with zero cross-workspace access and strict
user/dev audience isolation.
"""
from __future__ import annotations

import gzip
import json
import logging
import math
import re
import threading
import time
from typing import Any

import httpx

try:
    from pythainlp.corpus import thai_stopwords as _pythai_stopwords
    from pythainlp.tokenize import word_tokenize as _pythai_tokenize
except Exception:  # pragma: no cover - fallback when pythainlp unavailable
    _pythai_stopwords = None
    _pythai_tokenize = None

log = logging.getLogger("voice")

DEV_FOLDERS = frozenset({"api", "architecture", "dev-playbook", "queries", "schema"})
_MENU_ROW_RE = re.compile(
    r"^\|\s*\*\*(\d{4})\*\*\s*\|\s*\*\*([^|*]+)\*\*\s*\|\s*\*\*([^|*]+)\*\*(?:<br>([^|]+))?\s*\|\s*([^|]+)\s*\|"
)
_MENU_CODE_TOKEN_RE = re.compile(r"\b([1-9]\d{3})\b")
_LEGACY_CODE_TOKEN_RE = re.compile(r"\b(\d+\.\d+(?:\.\d+)?)\b")
_SOURCE_LINE_RE = re.compile(r"(?m)^(?:sources|slug):\s*.*\n?")
_WS_TOKEN_RE = re.compile(r"[a-zA-Z0-9\u0E00-\u0E7F]{2,}")

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
    (re.compile(r"(?<![a-zA-Z])(?:vc|วีซี)(?![a-zA-Z])|มีซี(?![a-zA-Z\u0E00-\u0E7F])", re.I), " บีซี "),
)

_SEARCH_SYNONYMS: dict[str, tuple[str, ...]] = {
    "เก็บเงิน": ("ลูกหนี้", "วางบิล", "ค้างชำระ"),
    "จ่ายเงิน": ("เจ้าหนี้", "ครบกำหนด", "ค้างชำระ"),
    "เตือน": ("แจ้งเตือน",),
    "แจ้งเตือน": ("เตือน", "ศูนย์แจ้งเตือน"),
    "ครบกำหนด": ("อายุหนี้", "เกินกำหนด"),
    "คลาวด์": ("cloud", "เซิร์ฟเวอร์", "บนเว็บ"),
    "cloud": ("คลาวด์", "เซิร์ฟเวอร์", "บนเว็บ"),
    "ยอดขาย": ("ขาย", "รายงานขาย"),
    "ออกบิล": ("บิล", "ขาย"),
    "แยกสาขา": ("สาขา",),
    "สาขา": ("แยกสาขา", "สำนักงานใหญ่"),
    "นิติบุคคล": ("หลายนิติบุคคล", "สำนักงานใหญ่"),
}

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

_VALID_SHORT_THAI_TOKENS = frozenset({"งบ", "ยา", "รถ"})
_ASCII_TOKEN_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{1,}")

_META_NAV_SLUGS = frozenset({
    "index", "playbook-sales", "website-manual-and-blog-directory", "video-visual-tutorials",
})

_THAI_STOPWORDS = frozenset(_pythai_stopwords()) if _pythai_stopwords else frozenset()


def _normalize_search_text(text: str) -> str:
    s = str(text or "").strip().lower()
    for pat, repl in _STT_SEARCH_NORMALIZE_RULES:
        s = pat.sub(repl, s)
    return " ".join(s.split())


def _is_thai_word(tok: str) -> bool:
    return any("\u0E00" <= ch <= "\u0E7F" for ch in tok)


def _tokenize_query(text: str, expand_synonyms: bool = True) -> list[str]:
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
    seen: set[str] = set()
    out: list[str] = []

    def _add(tok: str) -> None:
        t = re.sub(r"^[^\w\u0E00-\u0E7F]+|[^\w\u0E00-\u0E7F]+$", "", tok.lower())
        if len(t) < 2 or t.isdigit():
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
        for tok in list(out):
            for syn in _SEARCH_SYNONYMS.get(tok, ()):
                _add(syn)
    return out


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


class BrainVaultMirror:
    """Thread-safe RAM mirror of /api/vault-pack with ETag sync and O(1) user/dev isolation."""

    def __init__(self, vault_pack_url: str, ttl_s: int = 1800) -> None:
        self.url = (vault_pack_url or "").strip()
        self.ttl_s = max(120, int(ttl_s))
        self._lock = threading.Lock()
        self._refreshing = False
        self._etag: str | None = None
        self._last_sync_m: float = 0.0
        self._version: str = ""
        self._commit: str = ""
        self._notes_by_slug: dict[str, dict[str, Any]] = {}
        self._menus_by_code: dict[str, dict[str, str]] = {}
        self._menus_by_legacy: dict[str, dict[str, str]] = {}
        self._menus_list: list[dict[str, str]] = []
        self._idf: dict[str, float] = {}

    @property
    def ready(self) -> bool:
        with self._lock:
            return bool(self._notes_by_slug)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "ready": bool(self._notes_by_slug),
                "version": self._version,
                "commit": self._commit,
                "etag": self._etag,
                "notes": len(self._notes_by_slug),
                "menus": len(self._menus_list),
            }

    def load_notes_payload(self, obj: dict[str, Any], etag: str | None = None) -> dict[str, Any]:
        raw_notes = obj.get("notes") or []
        by_slug: dict[str, dict[str, Any]] = {}
        menus_by_code: dict[str, dict[str, str]] = {}
        menus_by_legacy: dict[str, dict[str, str]] = {}
        menus_list: list[dict[str, str]] = []

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
            clean_body = _SOURCE_LINE_RE.sub("", body).strip()
            title_meta_low = f"{title} {' '.join(aliases)} {' '.join(tags)}".lower()
            headings_low = " ".join(
                ln.lstrip("# ").strip().lower()
                for ln in clean_body.splitlines()
                if ln.lstrip().startswith("##")
            )
            body_low = clean_body.lower()

            by_slug[slug] = {
                "slug": slug,
                "folder": folder,
                "audience": "dev" if is_dev else "user",
                "is_dev": is_dev,
                "is_meta_nav": slug in _META_NAV_SLUGS or slug.startswith("menu-cat-"),
                "title": title,
                "aliases": aliases,
                "tags": tags,
                "body": clean_body,
                "title_meta_low": title_meta_low,
                "headings_low": headings_low,
                "body_low": body_low,
                "search_hay": f"{title_meta_low}\n{body_low}",
                "raw": raw,
            }

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
                    }
                    menus_by_code[code] = rec
                    if legacy:
                        menus_by_legacy[legacy] = rec
                    menus_list.append(rec)

        now_m = time.monotonic()
        with self._lock:
            self._notes_by_slug = by_slug
            self._menus_by_code = menus_by_code
            self._menus_by_legacy = menus_by_legacy
            self._menus_list = menus_list
            self._idf = {}
            self._version = str(obj.get("version") or "")
            self._commit = str(obj.get("syncCommit") or "")
            if etag:
                self._etag = etag
            self._last_sync_m = now_m
        _tokenize_query("warmup", expand_synonyms=False)
        return self.stats()

    def sync(self, force: bool = False) -> dict[str, Any]:
        if not self.url:
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
        if not self.url:
            return
        with self._lock:
            stale = not self._notes_by_slug or (time.monotonic() - self._last_sync_m) >= self.ttl_s
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

    def lookup_menu(self, query: str, audience: str = "user", limit: int = 5) -> str | None:
        if (audience or "user") != "user":
            return None
        q = (query or "").strip()
        if not q:
            return None
        with self._lock:
            if not self._menus_list:
                return None
            by_code = self._menus_by_code
            by_legacy = self._menus_by_legacy
            all_menus = self._menus_list

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

    def read_note(self, slug: str, audience: str = "user") -> str | None:
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
        return f"{header}# {note['title']}\n\n{note['body']}" if aud == "user" else note["raw"]

    def batch_read(
        self,
        slugs: list[str],
        audience: str = "user",
        query: str = "",
        max_chars_per_note: int = 2000,
    ) -> str | None:
        clean_slugs = [str(s or "").strip() for s in (slugs or []) if str(s or "").strip()][:5]
        if not clean_slugs:
            return None
        aud = (audience or "user").strip().lower()
        cap = max(300, min(int(max_chars_per_note or 2000), 6000))
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
                text = note["body"][:cap]
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
