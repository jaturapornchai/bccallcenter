"""Streaming Thai TTS adapter สำหรับ LiveKit Agents (F5-TTS HTTP / OpenAI-compatible / PyThaiTTS)."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import wave
from typing import Any

import httpx
import numpy as np
from scipy.signal import resample_poly

from livekit.agents import APIConnectOptions, tts
from normalizer import chunk_thai_sentences

logger = logging.getLogger("bc-callcenter.tts")


def _wav_bytes_to_pcm16(wav_bytes: bytes, target_sr: int) -> bytes:
    """อ่านไฟล์ WAV และแปลงเป็น 16-bit mono PCM ที่ target_sr."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        sr = wf.getframerate()
        nch = wf.getnchannels()
        raw = wf.readframes(wf.getnframes())
    pcm = np.frombuffer(raw, dtype=np.int16)
    if nch > 1:
        pcm = pcm.reshape(-1, nch).mean(axis=1).astype(np.int16)
    if sr != target_sr and pcm.size > 0:
        from math import gcd

        g = gcd(target_sr, sr)
        floats = pcm.astype(np.float32) / 32768.0
        resampled = resample_poly(floats, target_sr // g, sr // g)
        pcm = np.clip(resampled * 32767.0, -32768, 32767).astype(np.int16)
    return pcm.tobytes()


class _ThaiChunkedStream(tts.ChunkedStream):
    """สตรีมเสียงไทยทีละท่อนประโยค เพื่อให้ท่อนแรกดังทันทีและถูกตัดทิ้งได้ทันทีเมื่อเกิด Barge-in."""

    def __init__(
        self,
        *,
        tts_owner: "ThaiStreamingTTS",
        input_text: str,
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(tts=tts_owner, input_text=input_text, conn_options=conn_options)
        self._owner = tts_owner

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        chunks = chunk_thai_sentences(self._input_text, max_chars=105)
        if not chunks:
            return

        output_emitter.initialize(
            request_id=f"th-tts-{os.urandom(4).hex()}",
            sample_rate=self._owner.sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
        )

        for sentence in chunks:
            try:
                pcm_bytes = await self._owner.synthesize_chunk(sentence)
                if pcm_bytes:
                    output_emitter.push(pcm_bytes)
            except asyncio.CancelledError:
                # ถูกยกเลิกทันทีเมื่อผู้โทรพูดแทรก (Barge-in)
                raise
            except Exception as exc:
                logger.warning("TTS chunk failed for %r: %s", sentence[:40], exc)

        output_emitter.flush()


class ThaiStreamingTTS(tts.TTS):
    """Thai TTS adapter รองรับ F5-TTS-THAI HTTP, OpenAI-compatible /v1/audio/speech, และ PyThaiTTS."""

    def __init__(
        self,
        *,
        provider: str | None = None,
        http_url: str | None = None,
        sample_rate: int | None = None,
        voice: str | None = None,
        ref_audio: str | None = None,
        api_key: str | None = None,
    ) -> None:
        sr = sample_rate or int(os.getenv("TTS_SAMPLE_RATE", "24000"))
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=sr,
            num_channels=1,
        )
        self.provider = (provider or os.getenv("TTS_PROVIDER", "f5_http")).strip().lower()
        self.http_url = (http_url or os.getenv("TTS_HTTP_URL", "http://127.0.0.1:8002/tts")).rstrip("/")
        self.voice = voice or os.getenv("TTS_VOICE", "jarvis-th")
        self.ref_audio = ref_audio or os.getenv("TTS_REF_AUDIO", "../jarvis/ref_audio.wav")
        self.api_key = api_key or os.getenv("TTS_API_KEY", "")
        self._http = httpx.AsyncClient(timeout=25.0)
        self._pythaitts: Any = None

    def _synthesize_pythaitts_sync(self, text: str) -> bytes:
        if self._pythaitts is None:
            from pythaitts import TTS as PyThaiTTS

            self._pythaitts = PyThaiTTS()
        wav_path = self._pythaitts.tts(text, filename=None)
        with open(wav_path, "rb") as f:
            wav_bytes = f.read()
        try:
            os.remove(wav_path)
        except OSError:
            pass
        return _wav_bytes_to_pcm16(wav_bytes, self.sample_rate)

    async def synthesize_chunk(self, text: str) -> bytes:
        if self.provider == "pythaitts":
            return await asyncio.to_thread(self._synthesize_pythaitts_sync, text)

        if self.provider == "openai_compat":
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
            res = await self._http.post(
                f"{self.http_url}/audio/speech",
                headers=headers,
                json={
                    "model": "f5-tts-thai",
                    "voice": self.voice,
                    "input": text,
                    "response_format": "wav",
                },
            )
            res.raise_for_status()
            return _wav_bytes_to_pcm16(res.content, self.sample_rate)

        # Default: f5_http (POST {"text": ..., "ref_audio": ...} -> WAV bytes)
        res = await self._http.post(
            self.http_url,
            json={"text": text, "voice": self.voice, "ref_audio": self.ref_audio},
        )
        res.raise_for_status()
        return _wav_bytes_to_pcm16(res.content, self.sample_rate)

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = APIConnectOptions(),
    ) -> tts.ChunkedStream:
        return _ThaiChunkedStream(tts_owner=self, input_text=text, conn_options=conn_options)

    async def aclose(self) -> None:
        await self._http.aclose()
