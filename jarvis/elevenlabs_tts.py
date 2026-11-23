"""Real-time Streaming TTS via ElevenLabs Flash v2.5 (PCM 24 kHz, zero GPU usage)."""
from __future__ import annotations

import logging
import re
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
    """Persistent HTTP keep-alive streaming client for ElevenLabs TTS (`pcm_24000`)."""

    def __init__(
        self,
        api_key: str,
        voice_id: str = "H9eYlNHpm1vah6PPgYAk",
        model_id: str = "eleven_v4_turbo",
        speed: float = 0.96,
        stability: float = 0.55,
        similarity_boost: float = 0.78,
        timeout: float = 15.0,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.voice_id = (voice_id or "H9eYlNHpm1vah6PPgYAk").strip()
        self.model_id = (model_id or "eleven_v4_turbo").strip()
        self.speed = float(speed)
        self.stability = float(stability)
        self.similarity_boost = float(similarity_boost)
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
        # Flash v2.5 supports explicit ISO 639-1 language_code for crisp multilingual accent
        if lang and len(lang) == 2:
            body["language_code"] = lang
        return body

    def stream_pcm24k(self, text: str, lang: str | None = None) -> Iterator[np.ndarray]:
        """Yield (1, N) float32 24 kHz audio chunks as bytes stream in from ElevenLabs."""
        cleaned = (text or "").strip()
        if not self.enabled or not cleaned or not SPEAKABLE_RE.search(cleaned):
            return
        url = f"/v1/text-to-speech/{self.voice_id}/stream?output_format=pcm_24000"
        buf = bytearray()
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
                    arr = np.frombuffer(take, dtype="<i2").astype(np.float32) / 32768.0
                    yield arr.reshape(1, -1)
        # Flush trailing samples (align to even int16 boundary)
        rem = len(buf) - (len(buf) % 2)
        if rem > 0:
            arr = np.frombuffer(bytes(buf[:rem]), dtype="<i2").astype(np.float32) / 32768.0
            yield arr.reshape(1, -1)

    def synthesize_pcm24k(self, text: str, lang: str | None = None) -> np.ndarray:
        """Synthesize a complete phrase (used for 0ms pre-rendered greeting/hold fillers)."""
        parts = list(self.stream_pcm24k(text, lang=lang))
        if not parts:
            return np.zeros((1, SAMPLE_RATE // 10), np.float32)
        return np.concatenate(parts, axis=1)
