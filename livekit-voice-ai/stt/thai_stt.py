"""Thai Speech-to-Text adapter สำหรับ LiveKit Agents (รองรับ 8kHz/16kHz/48kHz PCM resampling)."""

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

from livekit import rtc
from livekit.agents import APIConnectOptions, stt
from livekit.agents.utils import AudioBuffer, merge_frames

logger = logging.getLogger("bc-callcenter.stt")

# กรอง Hallucination ที่ Whisper มักพ่นออกมาเวลาเจอเสียงซ่าของสายโทรศัพท์
WHISPER_HALLUCINATIONS = {
    "ขอบคุณที่รับชม",
    "ขอบคุณครับที่รับชม",
    "ซับไตเติล",
    "โปรดกดติดตาม",
    "เสียงดนตรี",
    "บรรยายโดย",
}


def _audio_buffer_to_16k_float32(buffer: AudioBuffer) -> tuple[np.ndarray, int]:
    """รวม AudioFrame จาก LiveKit (8kHz/16kHz/48kHz) แปลงเป็น 16kHz mono float32."""
    frame = merge_frames(buffer)
    pcm16 = np.frombuffer(frame.data, dtype=np.int16)
    if frame.num_channels > 1:
        pcm16 = pcm16.reshape(-1, frame.num_channels).mean(axis=1).astype(np.int16)
    samples = pcm16.astype(np.float32) / 32768.0
    sr = frame.sample_rate
    if sr != 16000 and samples.size > 0:
        from math import gcd

        g = gcd(16000, sr)
        samples = resample_poly(samples, 16000 // g, sr // g).astype(np.float32)
    return samples, 16000


def _float32_to_wav_bytes(samples: np.ndarray, sr: int = 16000) -> bytes:
    pcm16 = np.clip(samples * 32767.0, -32768, 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm16.tobytes())
    return buf.getvalue()


class ThaiWhisperSTT(stt.STT):
    """Thai STT adapter: รัน Faster-Whisper โลคอล (โมเดลที่มีอยู่แล้ว) หรือ OpenAI-compatible HTTP endpoint."""

    def __init__(
        self,
        *,
        provider: str | None = None,
        model_path: str | None = None,
        device: str | None = None,
        compute_type: str | None = None,
        api_base_url: str | None = None,
        api_key: str | None = None,
        api_model: str | None = None,
        language: str | None = "th",
    ) -> None:
        super().__init__(
            capabilities=stt.STTCapabilities(streaming=False, interim_results=False)
        )
        self.provider = (provider or os.getenv("STT_PROVIDER", "local_whisper")).strip().lower()
        self.model_path = model_path or os.getenv(
            "STT_MODEL_PATH", "../jarvis/models/faster-whisper-large-v3-turbo-th"
        )
        self.device = device or os.getenv("STT_DEVICE", "cuda")
        self.compute_type = compute_type or os.getenv("STT_COMPUTE_TYPE", "float16")
        self.api_base_url = (api_base_url or os.getenv("STT_API_BASE_URL", "http://127.0.0.1:8001/v1")).rstrip("/")
        self.api_key = api_key or os.getenv("STT_API_KEY", "local-key")
        self.api_model = api_model or os.getenv("STT_API_MODEL", "biodatlab/whisper-th-large-v3-combined")
        self.language: str | None = language  # None = auto-detect ภาษาของลูกค้าอัตโนมัติ
        self.last_detected_language: str | None = language
        self._model: Any = None
        self._http = httpx.AsyncClient(timeout=20.0)

    def set_language(self, language: str | None) -> None:
        """สลับภาษาการฟัง STT กลางสาย (None = auto-detect)."""
        self.language = language or None
        logger.info("STT language switched to: %s", self.language or "auto")

    def _ensure_local_model(self) -> Any:
        if self._model is None:
            from faster_whisper import WhisperModel

            resolved = os.path.abspath(self.model_path) if os.path.exists(self.model_path) else self.model_path
            logger.info("Loading Faster-Whisper model=%s device=%s compute=%s", resolved, self.device, self.compute_type)
            self._model = WhisperModel(resolved, device=self.device, compute_type=self.compute_type)
        return self._model

    def _transcribe_local_sync(self, samples: np.ndarray) -> tuple[str, str | None]:
        model = self._ensure_local_model()
        segments, info = model.transcribe(
            samples,
            language=self.language,
            beam_size=1,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 250},
            condition_on_previous_text=False,
        )
        detected = getattr(info, "language", None) or self.language
        text = " ".join(seg.text.strip() for seg in segments if seg.text.strip()).strip()
        return text, detected

    async def _transcribe_http(self, samples: np.ndarray, sr: int) -> tuple[str, str | None]:
        wav_bytes = _float32_to_wav_bytes(samples, sr)
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        files = {"file": ("speech.wav", wav_bytes, "audio/wav")}
        data: dict[str, str] = {"model": self.api_model}
        if self.language:
            data["language"] = self.language
        res = await self._http.post(
            f"{self.api_base_url}/audio/transcriptions",
            headers=headers,
            files=files,
            data=data,
        )
        res.raise_for_status()
        payload = res.json()
        return str(payload.get("text", "")).strip(), payload.get("language") or self.language

    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: str | None = None,
        conn_options: APIConnectOptions = APIConnectOptions(),
    ) -> stt.SpeechEvent:
        samples, sr = _audio_buffer_to_16k_float32(buffer)
        detected: str | None = language or self.language
        if samples.size < 1600:  # < 100ms
            text = ""
        elif self.provider == "openai_compat":
            text, detected = await self._transcribe_http(samples, sr)
        else:
            text, detected = await asyncio.to_thread(self._transcribe_local_sync, samples)

        if text.strip(" .!?…") in WHISPER_HALLUCINATIONS:
            text = ""
        if text and detected:
            self.last_detected_language = detected

        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[
                stt.SpeechData(
                    text=text,
                    language=detected or "th",
                    confidence=0.95 if text else 0.0,
                )
            ],
        )

    async def aclose(self) -> None:
        await self._http.aclose()
