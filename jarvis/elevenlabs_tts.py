"""Real-time Streaming TTS via ElevenLabs (PCM 24 kHz, zero GPU usage) with local disk PCM cache."""
from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
from typing import Iterator

import httpx
import numpy as np

log = logging.getLogger("voice")

SAMPLE_RATE = 24000
# ~120 ms per streamed PCM chunk at 24 kHz int16 mono (24000 * 0.12 * 2 bytes = 5760 bytes)
CHUNK_BYTES = 5760
SPEAKABLE_RE = re.compile(
    r"[\u0E00-\u0E7FA-Za-z0-9\u0E80-\u0EFF\u1000-\u109F\u1780-\u17FF\uAC00-\uD7AF\u3040-\u30FF\u4E00-\u9FFF]"
)


class ElevenLabsTTS:
    """Persistent HTTP keep-alive streaming client for ElevenLabs TTS (`pcm_24000`) with atomic disk caching."""

    def __init__(
        self,
        api_key: str,
        voice_id: str = "H9eYlNHpm1vah6PPgYAk",
        model_id: str = "eleven_v4_turbo",
        speed: float = 0.96,
        stability: float = 0.55,
        similarity_boost: float = 0.78,
        timeout: float = 15.0,
        cache_dir: str | None = None,
        max_cache_mb: int = 500,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.voice_id = (voice_id or "H9eYlNHpm1vah6PPgYAk").strip()
        self.model_id = (model_id or "eleven_v4_turbo").strip()
        self.speed = float(speed)
        self.stability = float(stability)
        self.similarity_boost = float(similarity_boost)
        self.cache_dir = cache_dir
        self.max_cache_bytes = max(20, int(max_cache_mb)) * 1024 * 1024
        self._cache_lock = threading.Lock()
        self.cache_hits = 0
        self.cache_misses = 0
        self.chars_saved = 0
        if self.cache_dir:
            os.makedirs(self.cache_dir, exist_ok=True)
        self._client = httpx.Client(
            base_url="https://api.elevenlabs.io",
            timeout=httpx.Timeout(connect=5.0, read=timeout, write=10.0, pool=5.0),
            headers={
                "xi-api-key": self.api_key,
                "Content-Type": "application/json",
                "Accept": "application/octet-stream",
            },
        )

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _cache_path(self, text: str, lang: str | None) -> str | None:
        if not self.cache_dir:
            return None
        key_src = f"{self.model_id}|{self.voice_id}|{self.speed:.2f}|{lang or 'auto'}|{text}"
        digest = hashlib.sha256(key_src.encode("utf-8")).hexdigest()[:24]
        return os.path.join(self.cache_dir, f"{digest}.pcm")

    def _read_cache(self, path: str | None) -> bytes | None:
        if not path or not os.path.exists(path):
            return None
        try:
            with open(path, "rb") as f:
                raw = f.read()
            if len(raw) < 400:
                return None
            # Touch mtime for LRU tracking
            try:
                os.utime(path, None)
            except OSError:
                pass
            return raw
        except OSError:
            return None

    def _write_cache(self, path: str | None, raw: bytes) -> None:
        if not path or len(raw) < 400:
            return
        tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        try:
            with self._cache_lock:
                with open(tmp, "wb") as f:
                    f.write(raw)
                os.replace(tmp, path)
                self._evict_if_needed()
        except OSError:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass

    def _evict_if_needed(self) -> None:
        if not self.cache_dir:
            return
        try:
            entries = []
            total = 0
            for name in os.listdir(self.cache_dir):
                if not name.endswith(".pcm"):
                    continue
                fp = os.path.join(self.cache_dir, name)
                try:
                    st = os.stat(fp)
                    entries.append((st.st_mtime, st.st_size, fp))
                    total += st.st_size
                except OSError:
                    continue
            if total <= self.max_cache_bytes:
                return
            entries.sort(key=lambda x: x[0])  # oldest first (LRU)
            target = int(self.max_cache_bytes * 0.85)
            for _, sz, fp in entries:
                if total <= target:
                    break
                try:
                    os.remove(fp)
                    total -= sz
                except OSError:
                    pass
        except OSError:
            pass

    def _payload(self, text: str, lang: str | None = None) -> dict:
        body: dict = {
            "text": text,
            "model_id": self.model_id,
            "voice_settings": {
                "stability": self.stability,
                "similarity_boost": self.similarity_boost,
                "style": 0.0,
                "use_speaker_boost": True,
                "speed": self.speed,
            },
        }
        if lang and len(lang) == 2:
            body["language_code"] = lang
        return body

    @staticmethod
    def _yield_pcm_chunks(raw: bytes) -> Iterator[np.ndarray]:
        usable = len(raw) - (len(raw) % 2)
        for off in range(0, usable, CHUNK_BYTES):
            piece = raw[off:min(off + CHUNK_BYTES, usable)]
            if len(piece) >= 2:
                arr = np.frombuffer(piece, dtype="<i2").astype(np.float32) / 32768.0
                yield arr.reshape(1, -1)

    def stream_pcm24k(self, text: str, lang: str | None = None) -> Iterator[np.ndarray]:
        """Yield (1, N) float32 24 kHz chunks from local disk cache (<1ms, 0 credits) or live ElevenLabs stream."""
        cleaned = (text or "").strip()
        if not self.enabled or not cleaned or not SPEAKABLE_RE.search(cleaned):
            return
        cpath = self._cache_path(cleaned, lang)
        cached = self._read_cache(cpath)
        if cached is not None:
            self.cache_hits += 1
            self.chars_saved += len(cleaned)
            log.info("TTS-CACHE HIT (0 credits, saved %d chars total): %s", self.chars_saved, cleaned[:60])
            yield from self._yield_pcm_chunks(cached)
            return

        self.cache_misses += 1
        url = f"/v1/text-to-speech/{self.voice_id}/stream?output_format=pcm_24000"
        buf = bytearray()
        full_pcm = bytearray()
        completed = False
        try:
            with self._client.stream("POST", url, json=self._payload(cleaned, lang)) as resp:
                if resp.status_code != 200:
                    err_body = resp.read().decode("utf-8", errors="replace")[:240]
                    raise RuntimeError(f"ElevenLabs HTTP {resp.status_code}: {err_body}")
                for raw in resp.iter_bytes(chunk_size=2048):
                    if not raw:
                        continue
                    buf.extend(raw)
                    while len(buf) >= CHUNK_BYTES:
                        take = bytes(buf[:CHUNK_BYTES])
                        del buf[:CHUNK_BYTES]
                        full_pcm.extend(take)
                        arr = np.frombuffer(take, dtype="<i2").astype(np.float32) / 32768.0
                        yield arr.reshape(1, -1)
            rem = len(buf) - (len(buf) % 2)
            if rem > 0:
                tail = bytes(buf[:rem])
                full_pcm.extend(tail)
                arr = np.frombuffer(tail, dtype="<i2").astype(np.float32) / 32768.0
                yield arr.reshape(1, -1)
            completed = True
        finally:
            # Only persist complete phrases (never save interrupted/partial audio on barge-in)
            if completed and full_pcm:
                self._write_cache(cpath, bytes(full_pcm))

    def synthesize_pcm24k(self, text: str, lang: str | None = None) -> np.ndarray:
        """Synthesize a complete phrase (serves from disk cache at 0ms / 0 credits on subsequent restarts)."""
        parts = list(self.stream_pcm24k(text, lang=lang))
        if not parts:
            return np.zeros((1, SAMPLE_RATE // 10), np.float32)
        return np.concatenate(parts, axis=1)

    def stats(self) -> dict:
        files = 0
        total_bytes = 0
        if self.cache_dir and os.path.isdir(self.cache_dir):
            for name in os.listdir(self.cache_dir):
                if name.endswith(".pcm"):
                    files += 1
                    try:
                        total_bytes += os.path.getsize(os.path.join(self.cache_dir, name))
                    except OSError:
                        pass
        return {
            "files": files,
            "size_mb": round(total_bytes / (1024 * 1024), 2),
            "hits": self.cache_hits,
            "misses": self.cache_misses,
            "chars_saved": self.chars_saved,
        }
