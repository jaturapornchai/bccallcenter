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
import re
import threading
import time
from typing import Any

import httpx

log = logging.getLogger("voice")

DEV_FOLDERS = frozenset({"api", "architecture", "dev-playbook", "queries", "schema"})
_MENU_ROW_RE = re.compile(
    r"^\|\s*\*\*(\d{4})\*\*\s*\|\s*\*\*([^|*]+)\*\*\s*\|\s*\*\*([^|*]+)\*\*(?:<br>([^|]+))?\s*\|\s*([^|]+)\s*\|"
)
_MENU_CODE_TOKEN_RE = re.compile(r"\b([1-9]\d{3})\b")
_LEGACY_CODE_TOKEN_RE = re.compile(r"\b(\d+\.\d+(?:\.\d+)?)\b")
_SOURCE_LINE_RE = re.compile(r"(?m)^(?:sources|slug):\s*.*\n?")
_WS_TOKEN_RE = re.compile(r"[a-zA-Z0-9\u0E00-\u0E7F]{2,}")


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

            by_slug[slug] = {
                "slug": slug,
                "folder": folder,
                "audience": "dev" if is_dev else "user",
                "is_dev": is_dev,
                "title": title,
                "aliases": aliases,
                "tags": tags,
                "body": clean_body,
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
            self._version = str(obj.get("version") or "")
            self._commit = str(obj.get("syncCommit") or "")
            if etag:
                self._etag = etag
            self._last_sync_m = now_m
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
            tokens = [t for t in _WS_TOKEN_RE.findall(q.lower()) if len(t) >= 3]
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

    def fallback_quick_answer(self, question: str, audience: str = "user", max_chars: int = 700) -> str | None:
        q = (question or "").strip()
        if not q or (audience or "user") != "user":
            return None
        tokens = _WS_TOKEN_RE.findall(q.lower())
        if not tokens:
            return None
        with self._lock:
            notes = [n for n in self._notes_by_slug.values() if not n["is_dev"]]
        best_score = 0
        best_note: dict[str, Any] | None = None
        for note in notes:
            title_meta = f"{note['title']} {' '.join(note['aliases'])} {' '.join(note['tags'])}".lower()
            head_body = note["body"][:600].lower()
            score = 0
            for tok in tokens:
                if len(tok) < 2:
                    continue
                if tok in title_meta:
                    score += 3
                elif tok in head_body:
                    score += 1
            if score > best_score:
                best_score = score
                best_note = note
        if not best_note or best_score < 3:
            return None
        cap = max(200, min(int(max_chars or 700), 2500))
        return (
            f"[quick_answer:mirror-fallback | audience=user | slug: {best_note['slug']}]\n"
            f"# {best_note['title']}\n{best_note['body'][:cap]}"
        )
