#!/usr/bin/env python
"""Regression test for J.A.R.V.I.S stopping its answer when a phone caller talks over it (barge-in).

live_words() and hears_itself() are lifted out of BargeInReplyOnPause in jarvis/voice_agent.py by name, because
importing that module loads the speech models; the ears, the clock of the phrases and the levels are scripted here.

Usage: python scripts/test-barge-in.py [path/to/voice_agent.py]   -> exit 0 when every case passes
"""
import ast
import json
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
import config as C  # noqa: E402  (the live thresholds)
import grounding  # noqa: E402  (the real word match)

MODULE_NAMES = {"_audio_dbfs", "LIVE_ERROR_LOG_GAP_S"}
CLASS_NAME = "BargeInReplyOnPause"
CLASS_NAMES = {"live_words", "hears_itself", "HESITATIONS", "BACKCHANNELS", "CUT_INS", "SIP_CUT_INS",
               "_BARGE_KEYWORDS", "_is_phone", "_live_error_at", "cut"}
SR = 16000
ASKING = "คุณถึงกำลังลองใช้อยู่ใช่ไหมครับ"
TELLING = "เรื่องระบบบัญชีแยกประเภทที่คุยค้างไว้ครั้งก่อนนะครับ"


def defined_names(node):
    if isinstance(node, ast.FunctionDef):
        return {node.name}
    if isinstance(node, ast.Assign):
        return {target.id for target in node.targets if isinstance(target, ast.Name)}
    return set()


class Records(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def tone(seconds, dbfs):
    """`seconds` of sound at a steady level."""
    return np.full(int(SR * seconds), 10 ** (dbfs / 20), dtype=np.float32)


def build(phone, final_text=""):
    """The handler class as the source has it, over scripted ears: (scope, log lines)."""
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    owner = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == CLASS_NAME), None)
    if owner is None:
        sys.exit(f"not found in {SOURCE.name}: {CLASS_NAME}")
    methods = [n for n in owner.body if defined_names(n) & CLASS_NAMES]
    if not {"live_words", "hears_itself"} <= set().union(*map(defined_names, methods)):
        sys.exit(f"not found in {SOURCE.name}: live_words / hears_itself")
    picked = [n for n in tree.body if defined_names(n) & MODULE_NAMES]
    picked.append(ast.ClassDef(name="Handler", bases=[], keywords=[], body=methods, decorator_list=[], type_params=[]))
    records = Records()
    quiet = logging.getLogger(f"test-barge-in-{id(records)}")
    quiet.addHandler(records)
    quiet.setLevel(logging.INFO)
    quiet.propagate = False

    def transcribe(audio):
        return ""

    scope = {
        "np": np, "re": re, "time": time, "json": json, "C": C, "grounding": grounding, "log": quiet,
        "LIVE": {}, "EARLY": {}, "CALL_LANG": {}, "call_id": lambda: "test", "masked": lambda text: text,
        "to16k": lambda audio: audio[1],
        "voiced": lambda audio: audio[1][np.abs(audio[1]) > 1e-4],  # digital silence is no speech
        "transcribe": transcribe, "_DEFAULT_TRANSCRIBE": transcribe,
        "transcribe_final": lambda audio: final_text, "on_phone": lambda: phone,
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=picked, type_ignores=[])), str(SOURCE), "exec"), scope)
    return scope, records.lines


class Ear:
    """The streaming ear: one scripted text per pass."""

    def __init__(self, texts):
        self.texts = list(texts)

    def accept(self, pcm):
        text = self.texts.pop(0)
        if isinstance(text, Exception):
            raise text
        return text


def talk_over(phone, bot_phrase, passes):
    """Feed `passes` of (text heard so far, seconds of new sound, its dBFS) to live_words while `bot_phrase`
    plays: (cut-in reasons, partials sent, log lines)."""
    scope, lines = build(phone)
    cuts, sent = [], []

    class Fake(scope["Handler"]):
        def cut_off(self, reason):
            cuts.append(reason)
            self.cut = True

        def send_message_sync(self, message):
            sent.append(message)

    handler = Fake()
    now = time.monotonic()
    handler.playing = ((now - 1.0, now + 30.0, bot_phrase, False),)
    live = scope["LIVE"][id(handler)] = {"t": 0.0, "fed": 0, "text": "", "busy": True,
                                        "stream": Ear(text for text, _, _ in passes)}
    state = types.SimpleNamespace(started_talking=True, responding=True, stream=np.zeros(0, dtype=np.float32))
    for _, seconds, dbfs in passes:
        new = tone(seconds, dbfs)
        state.stream = np.concatenate((state.stream, new))
        live["fed"] = state.stream.size
        handler.live_words(SR, new, state, phone)
    return cuts, sent, lines


def turn_is_echo(phone, bot_phrase, heard):
    scope, _ = build(phone, final_text=heard)
    handler = scope["Handler"]()
    now = time.monotonic()
    handler.playing = ((now - 2.0, now + 1.0, bot_phrase, False),)
    return handler.hears_itself(types.SimpleNamespace(stream=tone(0.5, -21), sampling_rate=SR))


def check_live(name, phone, bot_phrase, passes, want_cut, want_log=None, want_sent=None):
    cuts, sent, lines = talk_over(phone, bot_phrase, passes)
    problems = []
    if bool(cuts) != want_cut:
        problems.append(f"cut-ins {cuts}, want {'one' if want_cut else 'none'}")
    if want_log is not None and sum(want_log in line for line in lines) != 1:
        problems.append(f"log has no single '{want_log}' line: {lines}")
    if want_sent is not None and len(sent) != want_sent:
        problems.append(f"{len(sent)} partials sent, want {want_sent}")
    return name, problems


def check_turn(name, phone, bot_phrase, heard, want):
    got = turn_is_echo(phone, bot_phrase, heard)
    return name, [] if got == want else [f"echo={got}, want {want}"]


LOUD, FAR = -24.0, -40.0  # a caller at the phone; a voice the line or the room keeps far off
CHECKS = [
    # the caller's first word is one the secretary is saying: no echo on a phone line, so it is the caller
    lambda: check_live("phone: cut-in starting with the secretary's own word", True, TELLING,
                       [("เรื่อง", 0.8, LOUD)], True),
    lambda: check_live("phone: cut-in repeating the question's words", True, ASKING,
                       [("ใช่", 0.8, LOUD), ("ใช่ลองใช้อยู่", 0.3, LOUD)], True),
    # 2026-11-23 16:18: the line turned the caller down mid-sentence and the answer was never stopped
    lambda: check_live("phone: caller turned down by the line mid-sentence", True, TELLING,
                       [("นะครับ", 0.8, LOUD), ("นะครับราคาโปรแกรม", 0.5, FAR)], True),
    lambda: check_live("phone: far-off voice does not cut in, logged once", True, TELLING,
                       [("ราคา", 0.8, FAR), ("ราคาโปรแกรม", 0.3, FAR), ("ราคาโปรแกรมเท่าไหร่", 0.3, FAR)], False,
                       want_log="too quiet to cut in"),
    lambda: check_live("phone: loud clear words cut in at once", True, TELLING, [("หยุดก่อน", 0.8, LOUD)], True),
    lambda: check_live("phone: a hesitation alone does not cut in", True, TELLING, [("เอ่อ", 0.8, LOUD)], False),
    lambda: check_live("phone: an error in the live ear is logged", True, TELLING,
                       [(RuntimeError("ear down"), 0.8, LOUD)], False, want_log="live words failed"),
    # the browser has a speaker next to the mic: its echo guard stays
    lambda: check_live("browser: its own words back from the speaker are not shown", False, TELLING,
                       [("เรื่องระบบบัญชี", 0.8, LOUD)], False, want_sent=0),
    lambda: check_live("browser: the caller's words are shown", False, TELLING,
                       [("ขอถามราคาหน่อย", 0.8, LOUD)], False, want_sent=1),
    # 2026-11-24: 1-syllable glitches ("แห่"), bare digits ("660"), and -35 dBFS side chatter must not cut off
    lambda: check_live("phone: 1-syllable misheard glitch does not cut in", True, TELLING,
                       [("แห่", 0.8, -28.0)], False, want_log="too quiet to cut in"),
    lambda: check_live("phone: bare digits alone do not cut in", True, TELLING,
                       [("660", 0.8, -25.0)], False, want_log="too quiet to cut in"),
    lambda: check_live("phone: -35 dBFS side chatter rejected by -31 dBFS threshold", True, TELLING,
                       [("ราคาโปรแกรม", 0.8, -35.0)], False, want_log="too quiet to cut in"),
    # 2026-11-23 17:00:00: the caller's "ใช่" to "...ใช่ไหมครับ" was dropped as the secretary's own voice
    lambda: check_turn("phone: a yes made of the question's word is a turn", True, ASKING, "ใช่", False),
    lambda: check_turn("browser: its own words back are still echo", False, ASKING, "ใช่ไหมครับ", True),
]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    failed = 0
    for check in CHECKS:
        name, problems = check()
        if problems:
            failed += 1
            print(f"FAIL {name}: {'; '.join(problems)}")
    print(f"{len(CHECKS) - failed}/{len(CHECKS)} barge-in cases passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
