#!/usr/bin/env python
"""Speech pass over a dual-channel call recording (left = caller, right = AI) for Dev on the Fly.

Always returns Silero VAD speech spans per channel, the same VAD the live agent takes its turns from. Unless
--vad-only is given, each utterance is also transcribed on its own with language auto-detect, the way the live
agent hears a turn, so the output shows which language Whisper picks per turn and how sure it is.

The live J.A.R.V.I.S process owns the GPU: --device auto uses it only while enough VRAM is free, otherwise CPU.

Usage: python scripts/wav-transcribe.py <recording.wav> [--vad-only] [--device auto|cpu|cuda] [--threads 12]
       -> JSON on stdout, progress on stderr
"""
import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "jarvis"))
import config as C  # noqa: E402  the live agent's model id, compute type and local fallback dir

import numpy as np  # noqa: E402
from faster_whisper import WhisperModel  # noqa: E402
from faster_whisper.audio import decode_audio  # noqa: E402
from faster_whisper.vad import VadOptions, get_speech_timestamps  # noqa: E402

RATE = 16000
MIN_SPEECH_MS = 160  # shorter bursts are clicks and line pops
MIN_UTTERANCE_S = 0.3  # shorter chunks are breaths and backchannels; Whisper only invents words for them
SLICE_PAD_S = 0.15  # the spans are tight for timing; Whisper wants a little air around the words
# The recorder fills the gaps between turns with digital silence, which Silero never met in training: after a long
# stretch of it the VAD reacts 0.5-2 s late to the next turn or misses a short one. Noise at -60 dBFS, added for
# the VAD pass only, keeps it awake (measured on 4 calls: 60% -> 90% of the caller's speech found; louder noise
# starts inventing speech).
VAD_DITHER = 0.001
# The model commits ~1.3 GB (measured on the live process). The rest is headroom, so a review never pushes the
# live agent's copy out of VRAM.
MIN_FREE_VRAM_MB = 3000
# Phone, tax id and account numbers stay out of the review output (same digit rule as voice_agent.masked).
LONG_NUMBER = re.compile(r"\d[\d -]{6,}\d")


def pick_device(requested):
    if requested != "auto":
        return requested
    if not C.HAS_CUDA:
        return "cpu"
    try:
        query = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                               capture_output=True, text=True, timeout=10)
        return "cuda" if int(query.stdout.split()[0]) >= MIN_FREE_VRAM_MB else "cpu"
    except Exception:
        return "cpu"


def load_model(device, threads):
    options = dict(device=device, compute_type=C.FINAL_STT_COMPUTE_TYPE if device == "cuda" else "int8",
                   cpu_threads=threads)
    try:
        return WhisperModel(C.FINAL_STT_MODEL, local_files_only=True, **options)  # cached copy: a review never downloads
    except Exception:
        return WhisperModel(C.FINAL_STT_MODEL_DIR, **options)


def speech_spans(audio):
    options = VadOptions(min_speech_duration_ms=MIN_SPEECH_MS, min_silence_duration_ms=500, speech_pad_ms=30)
    noise = np.random.default_rng(0).normal(0, VAD_DITHER, audio.shape).astype(np.float32)  # seeded: a review repeats
    return [(c["start"], c["end"]) for c in get_speech_timestamps(audio + noise, options)]


def transcribe_spans(model, audio, spans):
    out = []
    pad = int(SLICE_PAD_S * RATE)
    for start, end in spans:
        if end - start < MIN_UTTERANCE_S * RATE:
            continue
        segments, info = model.transcribe(audio[max(0, start - pad):end + pad], language=None, beam_size=5,
                                          vad_filter=False, condition_on_previous_text=False)
        text = " ".join(s.text.strip() for s in segments).strip()
        if text:
            out.append({"start": round(start / RATE, 2), "end": round(end / RATE, 2), "lang": info.language,
                        "prob": round(float(info.language_probability), 2), "text": LONG_NUMBER.sub("###", text)})
    return out


def expose_cuda_libraries():
    """cuBLAS and cuDNN ship inside torch in this venv. The live agent finds them because it imports torch;
    a review only needs their folder on the DLL search path."""
    spec = importlib.util.find_spec("torch")
    if spec and spec.origin:
        os.environ["PATH"] = os.path.join(os.path.dirname(spec.origin), "lib") + os.pathsep + os.environ["PATH"]


def transcribe_channels(device, threads, channels, spans):
    if device == "cuda":
        expose_cuda_libraries()
    model = load_model(device, threads)
    return {name: transcribe_spans(model, audio, spans[name]) for name, audio in channels.items()}


def yield_cpu_to_live_calls():
    """A review runs while the line is open; the live agent's streaming ASR is on the CPU too and must win."""
    if os.name != "nt":
        return
    import ctypes
    kernel32 = ctypes.windll.kernel32
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.SetPriorityClass.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x00004000)  # BELOW_NORMAL_PRIORITY_CLASS


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wav")
    parser.add_argument("--vad-only", action="store_true")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--threads", type=int, default=12)  # CPU mode; the live CPU ASR keeps the other cores
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    yield_cpu_to_live_calls()

    began = time.monotonic()
    channels = dict(zip(("caller", "ai"), decode_audio(args.wav, sampling_rate=RATE, split_stereo=True)))
    spans = {name: speech_spans(audio) for name, audio in channels.items()}
    result = {"vad": {name: [[round(s / RATE, 2), round(e / RATE, 2)] for s, e in found]
                      for name, found in spans.items()}}
    if not args.vad_only:
        device = pick_device(args.device)
        try:
            transcripts = transcribe_channels(device, args.threads, channels, spans)
        except Exception as error:  # VRAM taken since the check, or a CUDA library that will not load
            if device != "cuda":
                raise
            print(f"cuda failed ({type(error).__name__}: {error}), using cpu", file=sys.stderr)
            device = "cpu"
            transcripts = transcribe_channels(device, args.threads, channels, spans)
        result.update(model=C.FINAL_STT_MODEL, device=device, **transcripts)
        print(f"transcribed on {device} at {time.monotonic() - began:.0f}s", file=sys.stderr)
    result["elapsedSec"] = round(time.monotonic() - began, 1)
    json.dump(result, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
