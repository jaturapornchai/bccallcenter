#!/usr/bin/env python
"""Regression test for Option A: gating phone turn onset in determine_pause() with voiced() + SIP_MIN_SPEECH_DBFS.

Prevents phone line noise bursts (that FastRTC's 100ms VAD catches but Whisper's VAD drops 100%) from:
- starting empty turns and stealing EARLY_STT / GPU slots, and
- clearing state.buffer so subsequent real speech waits up to 0.8s to refill.

Usage: python scripts/test-turn-onset.py [path/to/voice_agent.py]   -> exit 0 when every case passes
"""
import ast
import contextvars
import logging
import re
import sys
import time
import types
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SOURCE = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "jarvis" / "voice_agent.py"
sys.path.insert(0, str(ROOT / "jarvis"))
import config as C  # noqa: E402

MODULE_NAMES = {"_audio_dbfs", "_INCOMPLETE_TAIL_RE", "_is_incomplete_thai_tail"}
CLASS_NAME = "BargeInReplyOnPause"
CLASS_NAMES = {"determine_pause", "_is_phone", "_checked_state_id"}
SR = 16000


def defined_names(node):
    if isinstance(node, ast.FunctionDef):
        return {node.name}
    if isinstance(node, ast.Assign):
        return {target.id for target in node.targets if isinstance(target, ast.Name)}
    return set()


def tone(seconds, dbfs):
    return np.full(int(SR * seconds), 10 ** (dbfs / 20), dtype=np.float32)


def build(phone, voiced_fn):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    owner = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == CLASS_NAME), None)
    if owner is None:
        sys.exit(f"not found in {SOURCE.name}: {CLASS_NAME}")
    methods = [n for n in owner.body if defined_names(n) & CLASS_NAMES]
    picked = [n for n in tree.body if defined_names(n) & MODULE_NAMES]
    picked.append(ast.ClassDef(name="Handler", bases=[], keywords=[], body=methods, decorator_list=[], type_params=[]))

    submitted = []

    class FakePool:
        def submit(self, *args, **kwargs):
            submitted.append(args)
            return types.SimpleNamespace(cancel=lambda: None, add_done_callback=lambda cb: None)

    pool = FakePool()
    scope = {
        "np": np,
        "re": re,
        "time": time,
        "contextvars": contextvars,
        "C": C,
        "log": logging.getLogger("test-turn-onset"),
        "LIVE": {},
        "EARLY": {},
        "EARLY_STT": pool,
        "LIVE_STT_POOL": pool,
        "stt": types.SimpleNamespace(),
        "on_phone": lambda: phone,
        "voiced": voiced_fn,
        "transcribe_final": lambda audio: "",
        "create_message": lambda kind, data: f"{kind}:{data}",
        "session": lambda public: ("", []),
        "reply_public": object(),
        "words_transcribed": lambda job, hist: None,
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=picked, type_ignores=[])), str(SOURCE), "exec"), scope)

    class FakeHandler(scope["Handler"]):
        fn = None
        playing = ()
        model_options = None

        def __init__(self):
            self.sent = []
            self.model = types.SimpleNamespace(
                vad=lambda audio, opts: (0.2, [{"start": 0, "end": int(audio[1].size)}])
            )

        def send_message_sync(self, msg):
            self.sent.append(msg)

        def live_words(self, *args, **kwargs):
            pass

    return FakeHandler(), submitted


def make_state():
    return types.SimpleNamespace(started_talking=False, responding=False, stream=None, buffer=None)


WIN_S = C.SIP_PAUSE_SILENCE_S + 0.45


def test_phone_empty_vad_keeps_buffer_warm():
    h, sub = build(phone=True, voiced_fn=lambda audio: np.zeros(0, dtype=np.float32))
    st = make_state()
    paused = h.determine_pause(tone(WIN_S, -25.0), SR, st)
    assert paused is False, "empty STT VAD must not pause"
    assert st.started_talking is False, "empty STT VAD must NOT start a turn"
    assert st.buffer is not None and st.buffer.size > 0, "state.buffer must stay warm"
    assert len(sub) == 0, "must not submit to EARLY_STT"


def test_phone_quiet_noise_keeps_buffer_warm():
    h, sub = build(phone=True, voiced_fn=lambda audio: audio[1])
    st = make_state()
    paused = h.determine_pause(tone(WIN_S, C.SIP_MIN_SPEECH_DBFS - 5.0), SR, st)
    assert paused is False
    assert st.started_talking is False, "noise below SIP_MIN_SPEECH_DBFS must NOT start a turn"
    assert st.buffer is not None and st.buffer.size > 0, "state.buffer must stay warm"
    assert len(sub) == 0


def test_phone_quiet_then_loud_starts_next_step_with_word1():
    h, _ = build(phone=True, voiced_fn=lambda audio: audio[1])
    st = make_state()
    quiet = tone(WIN_S, C.SIP_MIN_SPEECH_DBFS - 4.0)
    h.determine_pause(quiet, SR, st)
    assert st.started_talking is False and st.buffer is not None
    # Next 100ms step arrives loud -> buffer + new step immediately crosses threshold with word 1 intact
    combined = np.concatenate((st.buffer, tone(0.15, -22.0)))
    h.determine_pause(combined, SR, st)
    assert st.started_talking is True, "loud step must immediately start turn"
    assert st.stream is not None and st.stream.size >= int(SR * 0.75), "word 1 preserved in stream"


def test_phone_real_speech_starts_immediately():
    h, sub = build(phone=True, voiced_fn=lambda audio: audio[1])
    st = make_state()
    h.determine_pause(tone(WIN_S, -26.0), SR, st)
    assert st.started_talking is True
    assert st.stream is not None
    assert len(sub) >= 1


def test_phone_voiced_error_fails_open():
    def boom(_audio):
        raise RuntimeError("vad error")

    h, _ = build(phone=True, voiced_fn=boom)
    st = make_state()
    h.determine_pause(tone(WIN_S, -26.0), SR, st)
    assert st.started_talking is True, "VAD exception must fail open"


def test_browser_skips_phone_gate():
    h, _ = build(phone=False, voiced_fn=lambda audio: np.zeros(0, dtype=np.float32))
    st = make_state()
    h.determine_pause(tone(1.2, -50.0), SR, st)
    assert st.started_talking is True, "browser mode keeps original onset behavior"


CHECKS = [
    ("phone: empty STT VAD does not start turn and keeps buffer warm", test_phone_empty_vad_keeps_buffer_warm),
    ("phone: quiet noise below threshold does not start turn", test_phone_quiet_noise_keeps_buffer_warm),
    ("phone: quiet word 1 followed by loud step starts immediately with buffer intact", test_phone_quiet_then_loud_starts_next_step_with_word1),
    ("phone: clear foreground speech starts turn immediately", test_phone_real_speech_starts_immediately),
    ("phone: voiced() error fails open so call never goes deaf", test_phone_voiced_error_fails_open),
    ("browser: keeps original onset without phone gate", test_browser_skips_phone_gate),
]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    failed = 0
    for name, fn in CHECKS:
        try:
            fn()
        except AssertionError as e:
            failed += 1
            print(f"FAIL {name}: {e}")
    print(f"{len(CHECKS) - failed}/{len(CHECKS)} turn-onset cases passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
