"""PostgreSQL 18 + pgvector HNSW + Thai-Segmented Full-Text Hybrid RRF Store for Embedded BC Brain.

Architecture:
- Dedicated local container ``bccallcenter-pg`` (``pgvector/pgvector:0.8.6-pg18`` on ``127.0.0.1:5446``).
- Embeddings via local Ollama ``bge-m3:latest`` (1024-dim multilingual Thai/English, ~20–60ms warm).
- Thai Word Segmentation via ``pythainlp`` ``newmm`` with custom BC accounting/ERP/POS dictionary.
- Single-round-trip parameterized SQL combining:
    1) ``pgvector`` HNSW cosine similarity (``embedding <=> $vec``)
    2) PostgreSQL ``GIN`` ``tsvector`` full-text ranking on Thai-segmented tokens (``tokens_th``)
  fused via Reciprocal Rank Fusion (RRF), strictly enforcing ``audience = 'user'`` for customer calls.
- Fail-soft with circuit breaker: if PostgreSQL or Ollama is offline, callers fall back seamlessly to
  in-RAM BM25 with zero phone call latency penalty.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import sys
import threading
import time
from collections import OrderedDict
from typing import Any, Callable

import httpx

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_VENDOR_DIR = os.path.join(_BASE_DIR, "_vendor")
if os.path.isdir(_VENDOR_DIR) and _VENDOR_DIR not in sys.path:
    sys.path.insert(0, _VENDOR_DIR)

try:
    import pg8000.native as _pg_native  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - fail-soft if vendor not installed
    _pg_native = None

log = logging.getLogger("voice")

EMBED_DIM = 1024
_DEFAULT_PG_HOST = "127.0.0.1"
_DEFAULT_PG_PORT = 5446
_DEFAULT_PG_DB = "bccallcenter_brain"
_DEFAULT_PG_USER = "bcbrain"
_DEFAULT_EMBED_URL = "http://127.0.0.1:11434/api/embed"
_DEFAULT_EMBED_MODEL = "bge-m3:latest"

_ENV_LOADED = False


def _load_root_env_once() -> None:
    """Load BRAIN_PG_* and BRAIN_EMBED_* from d:\\yalecom\\.env.local without overwriting real env vars."""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True
    root_env = os.path.join(os.path.dirname(_BASE_DIR), ".env.local")
    if not os.path.isfile(root_env):
        return
    allowed = {
        "BRAIN_PG_HOST", "BRAIN_PG_PORT", "BRAIN_PG_DB", "BRAIN_PG_USER", "BRAIN_PG_PASSWORD",
        "BRAIN_PG_ENABLED", "BRAIN_EMBED_URL", "BRAIN_EMBED_MODEL",
    }
    try:
        with open(root_env, encoding="utf-8") as f:
            for line in f:
                k, sep, v = line.strip().partition("=")
                if sep and k in allowed and v:
                    os.environ.setdefault(k, v.strip())
    except OSError:
        pass


def _format_pg_vector(vec: list[float]) -> str:
    return "[" + ",".join(f"{float(x):.6f}" for x in vec) + "]"


def _split_note_into_chunks(note: dict[str, Any], max_chars: int = 1150) -> list[dict[str, str]]:
    """Split a media-free BC Brain note into clean, self-contained section chunks (covering all menus & sections)."""
    slug = str(note.get("slug") or "").strip()
    title = str(note.get("title") or slug).strip()
    body = str(note.get("body") or "").strip()
    if not body:
        return []

    lines = body.splitlines()
    if lines and lines[0].lstrip().startswith("# "):
        body = "\n".join(lines[1:]).strip()
    if not body:
        return []

    chunks: list[dict[str, str]] = []

    # Special handling for 4-digit menu catalog tables (menu-cat-1xxx..9xxx) so all 735 menus are cleanly indexed
    if slug.startswith("menu-cat-"):
        cur_heading = title
        cur_rows: list[str] = []
        for raw_ln in body.splitlines():
            s = raw_ln.strip()
            if not s:
                continue
            if s.startswith("## "):
                if cur_rows:
                    chunks.append({"heading": cur_heading, "content": "\n".join(cur_rows)})
                    cur_rows = []
                cur_heading = s.lstrip("# ").strip()
                continue
            if s.startswith("| ---"):
                continue
            cur_rows.append(s)
            if len(cur_rows) >= 16 or sum(len(x) + 1 for x in cur_rows) >= max_chars:
                chunks.append({"heading": cur_heading, "content": "\n".join(cur_rows)})
                cur_rows = []
        if cur_rows:
            chunks.append({"heading": cur_heading, "content": "\n".join(cur_rows)})
        return chunks

    # General notes: split by ## and ### headings, then sub-split oversized sections on paragraph/line boundaries
    raw_sections = [s.strip() for s in re.split(r"\n(?=##{1,2}\s)", body) if s.strip()]
    if not raw_sections:
        raw_sections = [body]

    for sec in raw_sections:
        sec_lines = sec.splitlines()
        first = sec_lines[0].strip() if sec_lines else ""
        if first.startswith("##"):
            heading = first.lstrip("# ").strip()
            sec_body = "\n".join(sec_lines[1:]).strip() or heading
        else:
            heading = title
            sec_body = sec

        if len(sec_body) <= max_chars:
            chunks.append({"heading": heading, "content": sec_body})
            continue

        # Sub-split long sections cleanly by paragraphs/lines with light overlap
        paras = [p.strip() for p in re.split(r"\n{2,}", sec_body) if p.strip()]
        buf: list[str] = []
        buf_len = 0
        part_no = 1
        for p in paras:
            if len(p) > max_chars:
                # Split giant single block (e.g. long markdown table) by lines
                for ln in p.splitlines():
                    ln_s = ln.strip()
                    if not ln_s:
                        continue
                    if buf and buf_len + len(ln_s) + 1 > max_chars:
                        chunks.append({
                            "heading": f"{heading} (ส่วน {part_no})" if part_no > 1 else heading,
                            "content": "\n".join(buf),
                        })
                        part_no += 1
                        overlap = buf[-1:] if len(buf[-1]) <= 180 else []
                        buf = list(overlap)
                        buf_len = sum(len(x) + 1 for x in buf)
                    buf.append(ln_s[:max_chars])
                    buf_len += min(len(ln_s), max_chars) + 1
                continue
            if buf and buf_len + len(p) + 2 > max_chars:
                chunks.append({
                    "heading": f"{heading} (ส่วน {part_no})" if part_no > 1 else heading,
                    "content": "\n\n".join(buf),
                })
                part_no += 1
                overlap = buf[-1:] if len(buf[-1]) <= 180 else []
                buf = list(overlap)
                buf_len = sum(len(x) + 2 for x in buf)
            buf.append(p)
            buf_len += len(p) + 2
        if buf:
            chunks.append({
                "heading": f"{heading} (ส่วน {part_no})" if part_no > 1 else heading,
                "content": "\n\n".join(buf),
            })

    return chunks


class BrainPgVectorStore:
    """Thread-safe PostgreSQL 18 + pgvector HNSW + Thai FTS Hybrid RRF backend."""

    def __init__(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        database: str | None = None,
        user: str | None = None,
        password: str | None = None,
        embed_url: str | None = None,
        embed_model: str | None = None,
        enabled: bool | None = None,
    ) -> None:
        _load_root_env_once()
        env_enabled = os.environ.get("BRAIN_PG_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")
        self.enabled = env_enabled if enabled is None else bool(enabled)
        self.host = (host or os.environ.get("BRAIN_PG_HOST") or _DEFAULT_PG_HOST).strip()
        self.port = int(port or os.environ.get("BRAIN_PG_PORT") or _DEFAULT_PG_PORT)
        self.database = (database or os.environ.get("BRAIN_PG_DB") or _DEFAULT_PG_DB).strip()
        self.user = (user or os.environ.get("BRAIN_PG_USER") or _DEFAULT_PG_USER).strip()
        self.password = password if password is not None else os.environ.get("BRAIN_PG_PASSWORD", "")
        self.embed_url = (embed_url or os.environ.get("BRAIN_EMBED_URL") or _DEFAULT_EMBED_URL).strip()
        self.embed_model = (embed_model or os.environ.get("BRAIN_EMBED_MODEL") or _DEFAULT_EMBED_MODEL).strip()

        self._pg_lock = threading.Lock()
        self._conn: Any = None
        self._schema_ready = False
        self._embed_lock = threading.Lock()
        self._http: httpx.Client | None = None
        self._embed_cache: OrderedDict[str, list[float]] = OrderedDict()
        self._embed_cache_max = 512
        self._pg_fail_until = 0.0
        self._embed_fail_until = 0.0
        self._embed_timeout_streak = 0
        self._warming = False
        self._last_sync_count = 0
        self._last_chunk_total = 0

        if not self.password or _pg_native is None:
            self.enabled = False

    def _close_conn_locked(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def _get_conn_locked(self, timeout_s: float = 3.0) -> Any:
        if not self.enabled or _pg_native is None:
            return None
        now = time.monotonic()
        if now < self._pg_fail_until:
            return None
        if self._conn is not None:
            return self._conn
        try:
            self._conn = _pg_native.Connection(
                user=self.user,
                password=self.password,
                host=self.host,
                port=self.port,
                database=self.database,
                timeout=timeout_s,
            )
            return self._conn
        except Exception as e:
            self._pg_fail_until = now + 20.0
            self._conn = None
            log.warning("BC Brain pgvector connect skipped (%s)", type(e).__name__)
            return None

    def _get_http(self) -> httpx.Client:
        with self._embed_lock:
            if self._http is None:
                self._http = httpx.Client(
                    timeout=httpx.Timeout(connect=1.5, read=30.0, write=10.0, pool=2.0),
                    limits=httpx.Limits(max_keepalive_connections=4, max_connections=8),
                )
            return self._http

    def ensure_schema(self) -> bool:
        if not self.enabled:
            return False
        with self._pg_lock:
            if self._schema_ready and self._conn is not None:
                return True
            conn = self._get_conn_locked(timeout_s=5.0)
            if conn is None:
                return False
            try:
                conn.run("CREATE EXTENSION IF NOT EXISTS vector")
                conn.run(
                    f"""
                    CREATE TABLE IF NOT EXISTS brain_chunks (
                        id BIGSERIAL PRIMARY KEY,
                        slug TEXT NOT NULL,
                        chunk_idx INT NOT NULL,
                        folder TEXT NOT NULL,
                        audience TEXT NOT NULL,
                        title TEXT NOT NULL,
                        heading TEXT NOT NULL,
                        content TEXT NOT NULL,
                        tokens_th TEXT NOT NULL,
                        tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', tokens_th)) STORED,
                        content_hash TEXT NOT NULL,
                        embedding vector({EMBED_DIM}) NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        UNIQUE (slug, chunk_idx)
                    )
                    """
                )
                conn.run(
                    """
                    CREATE INDEX IF NOT EXISTS idx_brain_chunks_hnsw
                    ON brain_chunks USING hnsw (embedding vector_cosine_ops)
                    WITH (m = 16, ef_construction = 64)
                    """
                )
                conn.run("CREATE INDEX IF NOT EXISTS idx_brain_chunks_tsv ON brain_chunks USING gin (tsv)")
                conn.run("CREATE INDEX IF NOT EXISTS idx_brain_chunks_aud_slug ON brain_chunks (audience, slug)")
                self._schema_ready = True
                return True
            except Exception as e:
                self._close_conn_locked()
                self._pg_fail_until = time.monotonic() + 20.0
                log.warning("BC Brain pgvector ensure_schema failed (%s)", type(e).__name__)
                return False

    def embed_texts(self, texts: list[str], timeout_s: float = 25.0) -> list[list[float]] | None:
        if not self.enabled or not texts:
            return None
        now = time.monotonic()
        if now < self._embed_fail_until:
            return None
        try:
            client = self._get_http()
            resp = client.post(
                self.embed_url,
                json={"model": self.embed_model, "input": texts, "keep_alive": -1},
                timeout=timeout_s,
            )
            if resp.status_code != 200:
                self._embed_fail_until = now + 20.0
                return None
            data = resp.json()
            embs = data.get("embeddings")
            if not isinstance(embs, list) or len(embs) != len(texts):
                return None
            if embs and len(embs[0]) != EMBED_DIM:
                log.warning("BC Brain embed dim mismatch: got %d expected %d", len(embs[0]), EMBED_DIM)
                return None
            self._embed_timeout_streak = 0
            return embs
        except httpx.TimeoutException as e:
            self._embed_timeout_streak += 1
            if self._embed_timeout_streak >= 3:
                self._embed_fail_until = now + 10.0
                self._embed_timeout_streak = 0
            log.warning("BC Brain embed timeout (%s)", type(e).__name__)
            self.warmup_async()
            return None
        except Exception as e:
            self._embed_fail_until = now + 15.0
            self._embed_timeout_streak = 0
            log.warning("BC Brain embed failed (%s)", type(e).__name__)
            return None

    def embed_query(self, query: str, timeout_s: float = 1.25) -> tuple[list[float] | None, float]:
        t0 = time.perf_counter()
        key = " ".join((query or "").strip().lower().split())
        if not key:
            return None, 0.0
        with self._embed_lock:
            cached = self._embed_cache.get(key)
            if cached is not None:
                self._embed_cache.move_to_end(key)
                return cached, round((time.perf_counter() - t0) * 1000.0, 2)
        embs = self.embed_texts([key], timeout_s=timeout_s)
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        if not embs:
            return None, ms
        vec = embs[0]
        with self._embed_lock:
            self._embed_cache[key] = vec
            self._embed_cache.move_to_end(key)
            while len(self._embed_cache) > self._embed_cache_max:
                self._embed_cache.popitem(last=False)
        return vec, ms

    def warmup_async(self) -> None:
        """Non-blocking background reload of bge-m3 into Ollama VRAM (keep_alive=-1) so calls never suffer repeated ReadTimeouts."""
        if not self.enabled or self._warming:
            return
        self._warming = True

        def _bg() -> None:
            try:
                client = self._get_http()
                resp = client.post(
                    self.embed_url,
                    json={"model": self.embed_model, "input": ["โปรแกรมบัญชี บีซีเอไอแอคเคาท์"], "keep_alive": -1},
                    timeout=18.0,
                )
                if resp.status_code == 200:
                    self._embed_fail_until = 0.0
                    self._embed_timeout_streak = 0
                    log.info("BC Brain embed model warmed in VRAM (keep_alive=-1)")
            except Exception as e:
                log.debug("BC Brain background warmup skipped (%s)", type(e).__name__)
            finally:
                self._warming = False

        threading.Thread(target=_bg, daemon=True, name="brain-embed-warmup").start()

    def warmup(self) -> dict[str, Any]:
        """Pre-warm PostgreSQL connection, schema, and Ollama bge-m3 VRAM on startup."""
        if not self.enabled:
            return {"enabled": False, "ready": False}
        ok_schema = self.ensure_schema()
        vec, embed_ms = self.embed_query("โปรแกรมบัญชี บีซีเอไอแอคเคาท์ สต็อก โปรโมชัน", timeout_s=8.0)
        st = self.stats()
        st["warmupEmbedMs"] = embed_ms
        st["embedReady"] = vec is not None
        st["schemaReady"] = ok_schema
        return st

    def stats(self) -> dict[str, Any]:
        base = {
            "enabled": self.enabled,
            "ready": False,
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "model": self.embed_model,
            "dim": EMBED_DIM,
            "chunks": self._last_chunk_total,
            "notes": 0,
            "userChunks": 0,
            "devChunks": 0,
        }
        if not self.enabled:
            return base
        with self._pg_lock:
            conn = self._get_conn_locked(timeout_s=2.0)
            if conn is None:
                return base
            try:
                rows = conn.run(
                    """
                    SELECT
                        COUNT(*)::int,
                        COUNT(DISTINCT slug)::int,
                        COUNT(*) FILTER (WHERE audience = 'user')::int,
                        COUNT(*) FILTER (WHERE audience = 'dev')::int
                    FROM brain_chunks
                    """
                )
                if rows:
                    total, notes_cnt, user_cnt, dev_cnt = rows[0]
                    self._last_chunk_total = int(total or 0)
                    base.update({
                        "ready": int(total or 0) > 0,
                        "chunks": int(total or 0),
                        "notes": int(notes_cnt or 0),
                        "userChunks": int(user_cnt or 0),
                        "devChunks": int(dev_cnt or 0),
                    })
                return base
            except Exception:
                self._close_conn_locked()
                return base

    def sync_vault(
        self,
        notes_by_slug: dict[str, dict[str, Any]],
        segment_fn: Callable[[str], str],
        *,
        force: bool = False,
        batch_size: int = 24,
    ) -> dict[str, Any]:
        """Incrementally chunk, Thai-segment, embed (bge-m3), and upsert all vault notes into pgvector."""
        t0 = time.perf_counter()
        if not self.enabled or not self.ensure_schema():
            return {"ok": False, "enabled": self.enabled, "reason": "pgvector unavailable"}

        # Build desired chunks in memory (skip media-only index directories unsuitable for voice)
        voice_exclude = frozenset({"video-visual-tutorials", "website-manual-and-blog-directory"})
        desired: dict[tuple[str, int], dict[str, Any]] = {}
        for slug, note in sorted(notes_by_slug.items()):
            if slug in voice_exclude:
                continue
            title = str(note.get("title") or slug).strip()
            folder = str(note.get("folder") or "").strip()
            audience = "dev" if note.get("is_dev") else "user"
            aliases = note.get("aliases") or []
            tags = note.get("tags") or []
            meta_prefix = " ".join(x for x in [title, *aliases[:6], *tags[:6]] if x)

            raw_chunks = _split_note_into_chunks(note)
            for idx, ch in enumerate(raw_chunks):
                heading = ch["heading"]
                content = ch["content"]
                embed_input = f"{title} | {heading}\n{meta_prefix}\n{content}"[:2200]
                tokens_th = segment_fn(f"{title} {heading} {meta_prefix} {content}")
                content_hash = hashlib.sha256(
                    f"{self.embed_model}|{audience}|{folder}|{heading}|{content}|{tokens_th}".encode("utf-8")
                ).hexdigest()
                desired[(slug, idx)] = {
                    "slug": slug,
                    "chunk_idx": idx,
                    "folder": folder,
                    "audience": audience,
                    "title": title,
                    "heading": heading,
                    "content": content,
                    "tokens_th": tokens_th,
                    "content_hash": content_hash,
                    "embed_input": embed_input,
                }

        # Compare existing hashes from DB
        existing: dict[tuple[str, int], str] = {}
        with self._pg_lock:
            conn = self._get_conn_locked(timeout_s=5.0)
            if conn is None:
                return {"ok": False, "reason": "pg connection failed"}
            rows = conn.run("SELECT slug, chunk_idx, content_hash FROM brain_chunks")
            for r_slug, r_idx, r_hash in rows:
                existing[(str(r_slug), int(r_idx))] = str(r_hash)

        to_delete = [k for k in existing if k not in desired]
        to_upsert = [
            rec for k, rec in desired.items()
            if force or existing.get(k) != rec["content_hash"]
        ]

        # Embed changed/new chunks in batches
        upserted = 0
        for i in range(0, len(to_upsert), max(1, batch_size)):
            batch = to_upsert[i : i + batch_size]
            embs = self.embed_texts([b["embed_input"] for b in batch], timeout_s=60.0)
            if not embs:
                return {
                    "ok": False,
                    "reason": "embedding batch failed",
                    "upserted": upserted,
                    "remaining": len(to_upsert) - i,
                }
            with self._pg_lock:
                conn = self._get_conn_locked(timeout_s=10.0)
                if conn is None:
                    return {"ok": False, "reason": "pg lost during sync", "upserted": upserted}
                conn.run("BEGIN")
                try:
                    for rec, vec in zip(batch, embs):
                        conn.run(
                            """
                            INSERT INTO brain_chunks (
                                slug, chunk_idx, folder, audience, title, heading,
                                content, tokens_th, content_hash, embedding, updated_at
                            )
                            VALUES (
                                :slug, :chunk_idx, :folder, :audience, :title, :heading,
                                :content, :tokens_th, :content_hash, CAST(:vec AS vector), now()
                            )
                            ON CONFLICT (slug, chunk_idx) DO UPDATE SET
                                folder = EXCLUDED.folder,
                                audience = EXCLUDED.audience,
                                title = EXCLUDED.title,
                                heading = EXCLUDED.heading,
                                content = EXCLUDED.content,
                                tokens_th = EXCLUDED.tokens_th,
                                content_hash = EXCLUDED.content_hash,
                                embedding = EXCLUDED.embedding,
                                updated_at = now()
                            """,
                            slug=rec["slug"],
                            chunk_idx=rec["chunk_idx"],
                            folder=rec["folder"],
                            audience=rec["audience"],
                            title=rec["title"],
                            heading=rec["heading"],
                            content=rec["content"],
                            tokens_th=rec["tokens_th"],
                            content_hash=rec["content_hash"],
                            vec=_format_pg_vector(vec),
                        )
                        upserted += 1
                    conn.run("COMMIT")
                except Exception:
                    try:
                        conn.run("ROLLBACK")
                    except Exception:
                        pass
                    self._close_conn_locked()
                    raise

        deleted = 0
        if to_delete:
            with self._pg_lock:
                conn = self._get_conn_locked(timeout_s=5.0)
                if conn is not None:
                    conn.run("BEGIN")
                    try:
                        for d_slug, d_idx in to_delete:
                            conn.run(
                                "DELETE FROM brain_chunks WHERE slug = :slug AND chunk_idx = :idx",
                                slug=d_slug,
                                idx=d_idx,
                            )
                            deleted += 1
                        conn.run("COMMIT")
                    except Exception:
                        try:
                            conn.run("ROLLBACK")
                        except Exception:
                            pass
                        self._close_conn_locked()

        self._last_sync_count = upserted
        self._last_chunk_total = len(desired)
        elapsed_s = round(time.perf_counter() - t0, 2)
        return {
            "ok": True,
            "notes": len(notes_by_slug),
            "totalChunks": len(desired),
            "upserted": upserted,
            "deleted": deleted,
            "unchanged": len(desired) - len(to_upsert),
            "elapsedSec": elapsed_s,
            "model": self.embed_model,
        }

    def hybrid_search(
        self,
        query: str,
        *,
        tokens: list[str],
        audience: str = "user",
        limit: int = 8,
        timeout_s: float = 1.25,
        embed_text: str | None = None,
    ) -> dict[str, Any]:
        """Single-SQL Hybrid RRF combining pgvector HNSW cosine + Thai-segmented GIN tsvector."""
        t0 = time.perf_counter()
        empty = {"ok": False, "hits": [], "embedMs": 0.0, "sqlMs": 0.0, "totalMs": 0.0}
        if not self.enabled:
            return empty

        q_clean = (embed_text or query or "").strip()
        if not q_clean:
            return empty

        vec, embed_ms = self.embed_query(q_clean, timeout_s=timeout_s)
        if vec is None:
            return empty

        # Build safe OR tsquery string from clean Thai/ASCII tokens (alphanumeric + Thai block only)
        safe_toks: list[str] = []
        seen_t: set[str] = set()
        for t in tokens[:14]:
            ct = re.sub(r"[^a-zA-Z0-9\u0E00-\u0E7F]", "", str(t or "").lower())
            if len(ct) >= 2 and ct not in seen_t:
                seen_t.add(ct)
                safe_toks.append(ct)
        tsq_expr = " | ".join(safe_toks)

        aud = (audience or "user").strip().lower()
        if aud not in ("user", "dev", "all"):
            aud = "user"
        aud_filter = "" if aud == "all" else "audience = :aud"
        vec_str = _format_pg_vector(vec)
        lim = max(1, min(int(limit or 8), 20))

        t_sql = time.perf_counter()
        with self._pg_lock:
            conn = self._get_conn_locked(timeout_s=max(0.5, timeout_s))
            if conn is None:
                return empty
            try:
                where_vec = f"WHERE {aud_filter}" if aud_filter else ""
                if tsq_expr:
                    where_fts = f"WHERE {aud_filter} AND tsv @@ to_tsquery('simple', :tsq)" if aud_filter else "WHERE tsv @@ to_tsquery('simple', :tsq)"
                    sql = f"""
                    WITH q AS (
                        SELECT CAST(:vec AS vector) AS qvec, to_tsquery('simple', :tsq) AS tsq
                    ),
                    vec_hits AS (
                        SELECT
                            id, slug, chunk_idx, folder, audience, title, heading, content,
                            (1.0 - (embedding <=> (SELECT qvec FROM q)))::float8 AS cosine,
                            ROW_NUMBER() OVER (ORDER BY embedding <=> (SELECT qvec FROM q))::int AS v_rank
                        FROM brain_chunks
                        {where_vec}
                        ORDER BY embedding <=> (SELECT qvec FROM q)
                        LIMIT 24
                    ),
                    fts_hits AS (
                        SELECT
                            id, slug, chunk_idx, folder, audience, title, heading, content,
                            ts_rank_cd(tsv, (SELECT tsq FROM q))::float8 AS fts_score,
                            ROW_NUMBER() OVER (ORDER BY ts_rank_cd(tsv, (SELECT tsq FROM q)) DESC, id)::int AS f_rank
                        FROM brain_chunks
                        {where_fts}
                        ORDER BY fts_score DESC, id
                        LIMIT 24
                    ),
                    fused AS (
                        SELECT
                            COALESCE(v.slug, f.slug) AS slug,
                            COALESCE(v.chunk_idx, f.chunk_idx) AS chunk_idx,
                            COALESCE(v.folder, f.folder) AS folder,
                            COALESCE(v.audience, f.audience) AS audience,
                            COALESCE(v.title, f.title) AS title,
                            COALESCE(v.heading, f.heading) AS heading,
                            COALESCE(v.content, f.content) AS content,
                            COALESCE(v.cosine, 0.0)::float8 AS cosine,
                            COALESCE(f.fts_score, 0.0)::float8 AS fts_score,
                            COALESCE(v.v_rank, 999)::int AS v_rank,
                            COALESCE(f.f_rank, 999)::int AS f_rank,
                            (
                                COALESCE(0.62 / (40.0 + v.v_rank), 0.0)
                                + COALESCE(0.38 / (40.0 + f.f_rank), 0.0)
                                + COALESCE(v.cosine * 0.018, 0.0)
                            )::float8 AS rrf_score
                        FROM vec_hits v
                        FULL OUTER JOIN fts_hits f ON v.id = f.id
                    )
                    SELECT slug, chunk_idx, folder, audience, title, heading, content,
                           cosine, fts_score, v_rank, f_rank, rrf_score
                    FROM fused
                    ORDER BY rrf_score DESC, cosine DESC
                    LIMIT :lim
                    """
                    params: dict[str, Any] = {"vec": vec_str, "tsq": tsq_expr, "lim": lim}
                    if aud_filter:
                        params["aud"] = aud
                    rows = conn.run(sql, **params)
                else:
                    sql = f"""
                    SELECT
                        slug, chunk_idx, folder, audience, title, heading, content,
                        (1.0 - (embedding <=> CAST(:vec AS vector)))::float8 AS cosine,
                        0.0::float8 AS fts_score,
                        ROW_NUMBER() OVER (ORDER BY embedding <=> CAST(:vec AS vector))::int AS v_rank,
                        999::int AS f_rank,
                        (1.0 - (embedding <=> CAST(:vec AS vector)))::float8 AS rrf_score
                    FROM brain_chunks
                    {where_vec}
                    ORDER BY embedding <=> CAST(:vec AS vector)
                    LIMIT :lim
                    """
                    params = {"vec": vec_str, "lim": lim}
                    if aud_filter:
                        params["aud"] = aud
                    rows = conn.run(sql, **params)
            except Exception as e:
                self._close_conn_locked()
                self._pg_fail_until = time.monotonic() + 15.0
                log.warning("BC Brain pgvector hybrid_search fallback (%s)", type(e).__name__)
                return empty

        sql_ms = round((time.perf_counter() - t_sql) * 1000.0, 2)
        total_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        hits = [
            {
                "slug": str(r[0]),
                "chunk_idx": int(r[1]),
                "folder": str(r[2]),
                "audience": str(r[3]),
                "title": str(r[4]),
                "heading": str(r[5]),
                "content": str(r[6]),
                "cosine": round(float(r[7] or 0.0), 4),
                "fts_score": round(float(r[8] or 0.0), 4),
                "v_rank": int(r[9] or 999),
                "f_rank": int(r[10] or 999),
                "rrf_score": round(float(r[11] or 0.0), 6),
            }
            for r in rows
        ]
        return {
            "ok": True,
            "hits": hits,
            "embedMs": embed_ms,
            "sqlMs": sql_ms,
            "totalMs": total_ms,
        }


_SHARED_PG_LOCK = threading.Lock()
_SHARED_PG_STORE: BrainPgVectorStore | None = None


def get_pgvector_store() -> BrainPgVectorStore:
    global _SHARED_PG_STORE
    with _SHARED_PG_LOCK:
        if _SHARED_PG_STORE is None:
            _SHARED_PG_STORE = BrainPgVectorStore()
        return _SHARED_PG_STORE
