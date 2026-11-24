"""Unit tests for clean telephony ElevenLabs settings & TTS bandpass/noise-gate DSP."""
from __future__ import annotations

import os
import sys
import unittest

import numpy as np
from scipy.ndimage import maximum_filter1d, uniform_filter1d
from scipy.signal import butter, sosfiltfilt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JARVIS_DIR = os.path.join(ROOT, "jarvis")
if JARVIS_DIR not in sys.path:
    sys.path.insert(0, JARVIS_DIR)

import config as C  # noqa: E402
from elevenlabs_tts import ElevenLabsTTS  # noqa: E402


def _clean_telephony_level(arr: np.ndarray, sr: int = C.TTS_SAMPLE_RATE, target_rms: float = C.TTS_RMS) -> np.ndarray:
    bp_sos = butter(4, [110.0, 3500.0], btype="bandpass", fs=sr, output="sos")
    arr = np.asarray(arr, dtype=np.float32).reshape(-1)
    if arr.size < 64:
        return arr
    arr = arr - float(np.mean(arr))
    if arr.size > 32:
        arr = sosfiltfilt(bp_sos, arr).astype(np.float32)
    env_win = max(1, int(0.015 * sr))
    rms_env = np.sqrt(np.maximum(uniform_filter1d(arr * arr, env_win), 1e-12))
    peak_env = float(np.max(rms_env))
    if peak_env < 1e-4:
        return np.zeros_like(arr)
    active_mask = rms_env > max(0.012, peak_env * 0.12)
    speech_rms = (
        float(np.sqrt(np.mean(arr[active_mask] ** 2)))
        if np.any(active_mask)
        else float(np.sqrt(np.mean(arr ** 2)))
    )
    if speech_rms < 1e-4:
        return np.zeros_like(arr)
    gate_hi = speech_rms * 0.20
    gate_lo = speech_rms * 0.06
    t = np.clip((rms_env - gate_lo) / max(gate_hi - gate_lo, 1e-6), 0.0, 1.0)
    gate_gain = 0.04 + 0.96 * (t * t * (3.0 - 2.0 * t))
    hold_win = max(1, int(0.025 * sr))
    gate_gain = uniform_filter1d(maximum_filter1d(gate_gain, hold_win), hold_win)
    arr = (arr * gate_gain).astype(np.float32)
    arr = arr * (target_rms / speech_rms)
    win = 2 * int(0.02 * sr) + 1
    gain = np.minimum(1.0, 0.78 / np.maximum(maximum_filter1d(np.abs(arr), win), 1e-6))
    arr = (arr * uniform_filter1d(gain, win)).astype(np.float32)
    fade = min(int(0.006 * sr), arr.size // 4)
    if fade > 1:
        ramp = 0.5 * (1.0 - np.cos(np.linspace(0.0, np.pi, fade, dtype=np.float32)))
        arr[:fade] *= ramp
        arr[-fade:] *= ramp[::-1]
    return arr


class TestTtsAudioClean(unittest.TestCase):
    def test_elevenlabs_telephony_defaults_and_cache_key(self) -> None:
        el = ElevenLabsTTS(api_key="test-key", cache_dir=os.path.join(JARVIS_DIR, "cache", "tts"))
        self.assertAlmostEqual(el.stability, 0.65)
        self.assertAlmostEqual(el.similarity_boost, 0.72)
        self.assertAlmostEqual(el.style, 0.0)
        self.assertFalse(el.use_speaker_boost)

        emp_stab, emp_style, _ = el._resolve_emotion("empathy")
        up_stab, up_style, _ = el._resolve_emotion("upbeat")
        self.assertGreaterEqual(emp_stab, 0.65)
        self.assertEqual(emp_style, 0.0)
        self.assertGreaterEqual(up_stab, 0.55)
        self.assertLessEqual(up_style, 0.05)

        p1 = el._cache_path("สวัสดีครับ", "th", el.stability, el.style, el.speed)
        el_boost = ElevenLabsTTS(api_key="test-key", cache_dir=el.cache_dir, use_speaker_boost=True)
        p2 = el_boost._cache_path("สวัสดีครับ", "th", el_boost.stability, el_boost.style, el_boost.speed)
        self.assertNotEqual(p1, p2)

        payload = el._payload("สวัสดีครับ", "th")
        self.assertFalse(payload["voice_settings"]["use_speaker_boost"])
        self.assertAlmostEqual(C.TTS_RMS, 0.14)

    def test_voice_agent_level_contains_telephony_dsp(self) -> None:
        va_path = os.path.join(JARVIS_DIR, "voice_agent.py")
        with open(va_path, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("_TTS_BP_SOS = butter(4, [110.0, 3500.0]", src)
        self.assertIn("sosfiltfilt(_TTS_BP_SOS, arr)", src)

    def test_bandpass_and_pause_noise_gate_effectiveness(self) -> None:
        sr = 24000
        rng = np.random.default_rng(42)
        t_speech = np.arange(sr, dtype=np.float32) / sr
        t_pause = np.arange(sr // 2, dtype=np.float32) / sr

        # Active speech (400 Hz) + out-of-band hum (50 Hz) + ultrasonic hiss (7 kHz)
        speech = (
            0.35 * np.sin(2 * np.pi * 400.0 * t_speech)
            + 0.08 * np.sin(2 * np.pi * 50.0 * t_speech)
            + 0.08 * np.sin(2 * np.pi * 7000.0 * t_speech)
        ).astype(np.float32)
        # Pause with broadband hiss (-26 dBFS room noise)
        pause = (0.04 * rng.standard_normal(t_pause.size)).astype(np.float32)
        raw = np.concatenate([pause, speech, pause])

        cleaned = _clean_telephony_level(raw, sr=sr, target_rms=0.14)
        self.assertFalse(np.any(np.isnan(cleaned)))
        self.assertFalse(np.any(np.isinf(cleaned)))
        self.assertLessEqual(float(np.max(np.abs(cleaned))), 0.80)

        pause_in_rms = float(np.sqrt(np.mean(pause ** 2)))
        # Measure interior pause section (well away from transition fade)
        pause_out_rms = float(np.sqrt(np.mean(cleaned[: int(0.35 * sr)] ** 2)))
        attenuation_db = 20.0 * np.log10(max(pause_in_rms, 1e-6) / max(pause_out_rms, 1e-6))
        self.assertGreaterEqual(attenuation_db, 12.0, f"Expected >= 12 dB pause noise reduction, got {attenuation_db:.2f} dB")

        sp_in = raw[sr : 2 * sr]
        sp_out = cleaned[sr + int(0.1 * sr) : 2 * sr - int(0.1 * sr)]
        f_in = np.fft.rfftfreq(sp_in.size, 1.0 / sr)
        p_in = np.abs(np.fft.rfft(sp_in)) ** 2
        f_out = np.fft.rfftfreq(sp_out.size, 1.0 / sr)
        p_out = np.abs(np.fft.rfft(sp_out)) ** 2

        hi_in = float(np.sum(p_in[f_in >= 4500]) / (np.sum(p_in) + 1e-9))
        hi_out = float(np.sum(p_out[f_out >= 4500]) / (np.sum(p_out) + 1e-9))
        lo_in = float(np.sum(p_in[f_in <= 70]) / (np.sum(p_in) + 1e-9))
        lo_out = float(np.sum(p_out[f_out <= 70]) / (np.sum(p_out) + 1e-9))

        self.assertGreater(hi_in / max(hi_out, 1e-12), 100.0, "High-freq aliasing band (>4.5 kHz) must be strongly attenuated")
        self.assertGreater(lo_in / max(lo_out, 1e-12), 10.0, "Sub-70 Hz hum must be strongly attenuated")


if __name__ == "__main__":
    unittest.main(verbosity=2)
