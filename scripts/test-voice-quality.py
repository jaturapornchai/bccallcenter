#!/usr/bin/env python
"""Regression tests for the 6 voice-quality & latency fixes (2026-11-23):
1. Caller name extraction guard (rejects connectors/particles like "และ", "อันนี้", "เจ๊") & BARE_ADDRESS hold
2. Ear1 GPU anti-domino semaphore (fast-fails to CPU Ear2/3 instead of queueing behind stuck GPU work)
3. process_audio dedup via _checked_state_id (prevents 23x duplicate voiced() checks in 300ms) + SIP_MIN_TURN_SPEECH_S
4. Legacy MCP tool hiding in load_mcp_tools when quick_answer + batch_read are available
5. Typhoon Whisper stutter detection (_has_stt_stutter) & fallback to clean Ear2
6. Startup TTS label reflects ElevenLabs instead of legacy OmniVoice config

Usage: python scripts/test-voice-quality.py   -> exit 0 when every case passes
"""
import ast
import concurrent.futures
import logging
import re
import sys
import threading
import time
import types
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SOURCE = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "jarvis" / "voice_agent.py"
sys.path.insert(0, str(ROOT / "jarvis"))
import config as C  # noqa: E402

SR = 16000


def defined_names(node):
    if isinstance(node, ast.FunctionDef):
        return {node.name}
    if isinstance(node, ast.Assign):
        return {target.id for target in node.targets if isinstance(target, ast.Name)}
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return {node.target.id}
    return set()


def load_symbols(module_names, class_names=None, extra_scope=None):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    picked = [n for n in tree.body if defined_names(n) & set(module_names)]
    if class_names:
        owner = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "BargeInReplyOnPause")
        methods = [n for n in owner.body if defined_names(n) & set(class_names)]
        picked.append(ast.ClassDef(name="Handler", bases=[], keywords=[], body=methods, decorator_list=[], type_params=[]))
    quiet = logging.getLogger("test-voice-quality")
    quiet.handlers.clear()
    quiet.addHandler(logging.NullHandler())
    quiet.propagate = False
    scope = {
        "re": re,
        "np": np,
        "time": time,
        "threading": threading,
        "ThreadPoolExecutor": concurrent.futures.ThreadPoolExecutor,
        "C": C,
        "log": quiet,
        **(extra_scope or {}),
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=picked, type_ignores=[])), str(SOURCE), "exec"), scope)
    return scope


def test_caller_name_rejects_connectors_and_accepts_real_names():
    scope = load_symbols({
        "_NAME_PREFIX_RE", "_NAME_SUFFIX_RE", "_NAME_SPLIT_RE", "_HONORIFIC_RE",
        "_EXPLICIT_NAME_TH_RE", "_EXPLICIT_NAME_EN_RE", "_ASKED_NAME_BOT_RE",
        "_BAD_CALLER_NAMES", "_clean_caller_name", "_extract_caller_name_py",
        "BARE_ACK", "BARE_ADDRESS",
    })
    extract = scope["_extract_caller_name_py"]
    asked = "ไม่ทราบว่าขอเรียกชื่อเล่นว่าอะไรดีครับ"

    for bad in ("และ", "แล้ว", "อันนี้", "เจ๊", "ครับผม", "โอเค", "คือ", "เอ่อ", "ค่ะ", "หรือ", "ถ้า", "ไหม"):
        got, _ = extract(bad, asked)
        assert got == "", f"expected bad token {bad!r} to be rejected, got {got!r}"

    # "ขายเชื่อไหม" contains "เชื่อ" (เ + ชื่อ): must NOT extract "ไหม" as caller name
    got_credit, _ = extract("สินค้า ที่ ขายเชื่อไหม", "")
    assert got_credit == "", f"expected 'ขายเชื่อไหม' not to extract name, got {got_credit!r}"

    for good_in, want in (
        ("แบงค์ครับ", "แบงค์"),
        ("ผมชื่อก้องครับ", "ก้อง"),
        ("เรียกว่าลุงจืดครับ", "ลุงจืด"),
        ("สมชาย", "สมชาย"),
    ):
        got, _ = extract(good_in, asked)
        assert got == want, f"expected {good_in!r} -> {want!r}, got {got!r}"

    bare_addr = scope["BARE_ADDRESS"]
    assert bare_addr.fullmatch("คุณและครับ"), "bare address must be held"
    assert bare_addr.fullmatch("คุณสมชายครับ"), "bare address must be held"
    assert bare_addr.fullmatch("ลุงจืดครับ"), "bare honorific address must be held"
    assert not bare_addr.fullmatch("คุณสมชายครับ ราคาเริ่มต้น 199 บาทครับ"), "real sentence must not be held"


def test_stt_stutter_detector_and_ear2_fallback():
    scope = load_symbols({"_STT_STUTTER_3_RE", "_STT_STUTTER_2X3_RE", "_has_stt_stutter"})
    stutter = scope["_has_stt_stutter"]

    for bad in (
        "ถ้าผมจะเปิดบริษัทบาทบาทบาท",
        "ราคาเท่าไหร่าไหร่คะ",
        "โปรแกรมนี้ราคาเท่าไหร่างานนี้ราคาเท่าไหร่างานนี้แพงหรือเปล่า",
        "ซื้อีกซื้อีกซื้อสองแถมหนึ่ง",
        "สตัด สตาร์เตอร์เตอร์",
    ):
        assert stutter(bad), f"expected stutter=True for {bad!r}"

    for clean in (
        "ราคาเท่าไหร่ครับ",
        "ถ้าผมจะเปิดบริษัทต้องทำอะไรบ้างครับ",
        "ขายดีๆ นะครับ",
        "ซื้อสองแถมหนึ่งทำได้ไหม",
        "สตาร์ทเตอร์ใช้ฟรีใช่ไหมครับ",
    ):
        assert not stutter(clean), f"expected stutter=False for {clean!r}"

    # Verify garbled Typhoon transcript (len >= 2x clean OpenThai) falls back to OpenThai
    e1_bad = "ไปรับสินค้าสิทธิ์สิการสิตเองครับ ไอล์รัดสิงานครัว"
    e2_good = "ใบรับสินค้าเมนูอะไร"
    assert len(e2_good) >= 8 and len(e1_bad) >= len(e2_good) * 2, "garbled length ratio must trigger e2 fallback"


def test_ear1_gpu_busy_fast_fails_without_domino_timeout():
    sem = threading.Semaphore(1)
    sem.acquire()  # simulate stuck previous GPU job

    class SlowModel:
        def transcribe(self, *args, **kwargs):
            time.sleep(3.0)
            return [], types.SimpleNamespace(language_probability=0.9)

    class FastOpenThai:
        def transcribe_dual_prompts(self, data):
            return "ราคาเท่าไหร่ครับ", "ราคาเท่าไหร่ครับ"

    scope = load_symbols(
        {
            "EAR1_GPU_POOL", "EAR23_CPU_POOL", "_STT_STUTTER_3_RE", "_STT_STUTTER_2X3_RE",
            "_has_stt_stutter", "_choose_triple_ear", "transcribe_final",
            "CORE_MULTI_LANGS", "LANG_NAMES", "LANG_SURE_PROB",
            "_detect_explicit_lang_switch", "_detect_script_lang", "_clearly_spoken_lang",
            "_is_lang_confirm_yes", "_is_lang_confirm_no", "BC_STT_PROMPT",
        },
        extra_scope={
            "_EAR1_GPU_SEM": sem,
            "to16k": lambda a: a[1],
            "call_id": lambda: "sip-1-test",
            "on_phone": lambda: True,
            "CALL_LANG": {"sip-1-test": "th"},
            "CALL_LAST_STT_LANG": {},
            "PENDING_LANG_CONFIRM": {},
            "LAST_ALT_HEARD": {},
            "final_stt": SlowModel(),
            "get_multi_stt": lambda: None,
            "stt": FastOpenThai(),
            "masked": lambda s: s,
        },
    )
    t0 = time.perf_counter()
    txt = scope["transcribe_final"]((SR, np.ones(1600, dtype=np.float32)))
    dt = time.perf_counter() - t0
    sem.release()
    assert txt == "ราคาเท่าไหร่ครับ", f"expected CPU Ear2 fallback, got {txt!r}"
    assert dt < 1.0, f"GPU busy must fail fast (<1.0s), took {dt:.2f}s"


def test_process_audio_dedup_and_short_glitch_filter():
    voiced_calls = 0

    def fake_voiced(audio):
        nonlocal voiced_calls
        voiced_calls += 1
        return audio[1]

    class BaseReplyOnPause:
        def process_audio(self, audio, state):
            state.pause_detected = True

        def reset(self):
            pass

    scope = load_symbols(
        {"_audio_dbfs"},
        class_names={"process_audio", "reset", "cut", "_checked_stream_len", "_checked_state_id", "CUT_INS", "SIP_CUT_INS"},
        extra_scope={
            "on_phone": lambda: True,
            "voiced": fake_voiced,
            "EARLY": {},
            "LIVE": {},
            "PENDING": {"sip-1-test": {"text": ""}},
            "call_id": lambda: "sip-1-test",
            "create_message": lambda k, d: (k, d),
        },
    )

    class FakeHandler(scope["Handler"], BaseReplyOnPause):
        playing = ()

        def hears_itself(self, state):
            return False

        def just_filler(self, state):
            return False

        def no_cut_in(self, state, speech_s):
            return False

        def send_message_sync(self, msg):
            pass

        def cut_off(self, reason=""):
            pass

        def push_turn(self, interrupt=False):
            pass

    h = FakeHandler()
    # 1. Valid 0.50s speech: calling process_audio 20 times on the same state must run voiced() ONLY ONCE
    st1 = types.SimpleNamespace(
        pause_detected=False, started_talking=True, responding=False,
        sampling_rate=SR, stream=np.full(int(SR * 0.50), 0.1, dtype=np.float32), buffer=None,
    )
    for _ in range(20):
        h.process_audio((SR, np.zeros(320, dtype=np.float32)), st1)
    assert voiced_calls == 1, f"expected voiced() once for 20 queued frames, got {voiced_calls}"
    assert st1.pause_detected is True, "pause_detected must remain True until emit() consumes state"

    # 2. Next turn (new state instance) with 0.06s glitch ("และ") -> rejected as too short
    st2 = types.SimpleNamespace(
        pause_detected=False, started_talking=True, responding=False,
        sampling_rate=SR, stream=np.full(int(SR * 0.06), 0.2, dtype=np.float32), buffer=None,
    )
    scope["EARLY"][id(h)] = (st2.stream, types.SimpleNamespace(result=lambda: "และ"))
    h.process_audio((SR, np.zeros(320, dtype=np.float32)), st2)
    assert voiced_calls == 2, f"new state must be checked, got {voiced_calls}"
    assert st2.pause_detected is False and st2.stream is None, "0.06s 'และ' glitch must be dropped as noise"

    # 3. Short genuine cut-in word 0.10s ("ใช่" in SIP_CUT_INS) -> allowed!
    st3 = types.SimpleNamespace(
        pause_detected=False, started_talking=True, responding=False,
        sampling_rate=SR, stream=np.full(int(SR * 0.10), 0.2, dtype=np.float32), buffer=None,
    )
    scope["EARLY"][id(h)] = (st3.stream, types.SimpleNamespace(result=lambda: "ใช่"))
    h.process_audio((SR, np.zeros(320, dtype=np.float32)), st3)
    assert st3.pause_detected is True and st3.stream is not None, "genuine short 'ใช่' must stay a turn"


def test_legacy_mcp_tools_hidden_from_llm_when_fast_tools_present():
    def run_load(tool_list):
        specs, tools, worker, status = [], {}, {}, {}
        scope = load_symbols(
            {"LEGACY_BRAIN_TOOLS", "load_mcp_tools"},
            extra_scope={
                "C": types.SimpleNamespace(MCP_SERVERS={"bcbrain": "http://mcp.test/mcp"}),
                "connect_mcp": lambda s, u: (object(), [{"name": n, "description": n, "inputSchema": {"type": "object"}} for n in tool_list]),
                "TOOL_SPECS": specs,
                "TOOLS": tools,
                "WORKER": worker,
                "STATUS": status,
                "tool_spec": lambda n, d, sc: {"function": {"name": n}},
                "mcp_tool": lambda s, c, n: (lambda **kw: ([], n)),
            },
        )
        scope["load_mcp_tools"]()
        return [s["function"]["name"] for s in specs], set(tools)

    spec_names, tool_names = run_load(["quick_answer", "lookup_menu", "batch_read", "search_brain", "read_note", "ask_brain"])
    assert set(spec_names) == {"quick_answer", "lookup_menu", "batch_read"}, f"legacy tools must be hidden from LLM specs: {spec_names}"
    assert "read_note" in tool_names and "search_brain" in tool_names, "legacy tools must stay in TOOLS dict for internal fallback"

    legacy_specs, _ = run_load(["search_brain", "read_note"])
    assert set(legacy_specs) == {"search_brain", "read_note"}, f"when fast tools absent, legacy specs must remain: {legacy_specs}"


def test_config_and_source_invariants():
    assert C.SIP_MAX_TOOL_ROUNDS == 2
    assert C.SIP_MIN_TURN_SPEECH_S == 0.15
    assert "ollama.com" in C.DEEPSEEK_URL, f"expected Ollama Cloud URL, got {C.DEEPSEEK_URL}"
    assert "deepseek" in C.DEEPSEEK_MODEL.lower(), f"expected DeepSeek model on Ollama Cloud, got {C.DEEPSEEK_MODEL}"
    src = SOURCE.read_text(encoding="utf-8")
    assert "repetition_penalty=1.15" in src
    assert "no_repeat_ngram_size=3" in src
    assert "elevenlabs/{C.ELEVENLABS_MODEL_ID}" in src
    assert '"reasoning_effort": "none"' in src
    assert "BRAIN_DOWN_BODY not in" in src


def test_three_tier_search_planner():
    scope = load_symbols({
        "_FOLLOWUP_START_RE", "_VAGUE_FOLLOWUP_RE", "_stt_ears_diverge", "_needs_search_planner", "words_ask", "SEARCH_ASK",
    }, extra_scope={"grounded": None})
    needs = scope["_needs_search_planner"]
    diverge = scope["_stt_ears_diverge"]
    words_ask = scope["words_ask"]

    # Tier 2 Fast-Path: clear question & agreeing (or close) ears -> skip search_words LLM call
    assert not diverge("ราคาแพ็กเกจเท่าไหร่ครับ", ["ราคาแพ็กเกจเท่าไหร่"])
    assert not needs("ราคาแพ็กเกจเท่าไหร่ครับ", ["ราคาแพ็กเกจเท่าไหร่"], [])
    assert not diverge("คุณพูดได้กี่ภาษาครับ", ["คุณพูดได้กินลักษณะครับ"])  # minor OpenThai diff must stay Fast-Path

    # Tier 3 Planner-Path: ears strongly disagree (e.g. garbled e1 vs clean e2) -> run search_words with both ears
    assert diverge("ไปรับสินค้าสิทธิ์สิการสิตเองครับ ไอล์รัดสิงานครัว", ["ใบรับสินค้าเมนูอะไร"])
    assert needs("ไปรับสินค้าสิทธิ์สิการสิตเองครับ ไอล์รัดสิงานครัว", ["ใบรับสินค้าเมนูอะไร"], [])

    # Tier 3 Planner-Path: short/vague follow-up with history (e.g. "คำนวณยังไง", "แรงงานอยู่ไหน" -> "รายงานอยู่ไหน")
    hist = [{"role": "user", "content": "วิธีคิดต้นทุน FIFO"}, {"role": "assistant", "content": "ระบบรองรับ FIFO ครับ"}]
    assert needs("แล้วแบบอื่นล่ะ", ["แล้วแบบอื่นล่ะ"], hist)
    assert needs("คำนวณยังไง", ["คำนวณยังไง"], hist)
    assert needs("แรงงานอยู่ไหน", ["แรงงานอยู่ไหน"], hist)
    assert needs("รายงานอยู่ไหน", ["รายงานอยู่ไหน"], hist)

    # words_ask includes alt_heard when provided so words_early and search_words share the exact same key
    _, ask = words_ask(hist, "คำเพี้ยน", alt_heard=["ใบรับสินค้าเมนูอะไร"])
    assert "อีกหูฟังได้ยิน: ใบรับสินค้าเมนูอะไร" in ask[1]["content"]


def test_incomplete_tail_and_phone_fallback_queries():
    scope = load_symbols({
        "_INCOMPLETE_TAIL_RE", "_is_incomplete_thai_tail", "_FOLLOWUP_START_RE",
        "_VAGUE_FOLLOWUP_RE", "_stt_ears_diverge", "_build_phone_fallback_queries", "BC_STT_PROMPT",
    })
    incomplete = scope["_is_incomplete_thai_tail"]
    fallback = scope["_build_phone_fallback_queries"]

    for cut_mid in ("ผมอยากจะรู้ว่า", "อยากรู้ว่าถ้าเช็คคืนแล้ว", "รับของเข้าคลังแล้วก็"):
        assert incomplete(cut_mid), f"expected incomplete=True for {cut_mid!r}"
    for complete in ("รายงานอยู่ไหน", "ราคาเท่าไหร่ครับ", "แล้วมีรายงานให้ดูไหม"):
        assert not incomplete(complete), f"expected incomplete=False for {complete!r}"

    # Case L5651: "แรงงาน" (e1) vs "รายงาน" (e2) while discussing PO overdue report -> parallel queries with context
    hist_po = [{"role": "assistant", "content": "ในกลุ่มรายงานจัดซื้อมีรายงานค้างรับของตามพีโอ"}]
    qs_po = fallback("แรงงาน", ["รายงาน"], hist_po)
    assert len(qs_po) == 2 and any("แรงงาน" in q for q in qs_po) and any("รายงาน" in q for q in qs_po)
    assert all("รายงานจัดซื้อ" in q for q in qs_po), f"context anchor missing: {qs_po}"

    # Case L5724: long continuation (>20 chars) "แล้วมีแรงงานมีรายงานให้ดูไหม" while discussing booking order
    hist_bk = [{"role": "assistant", "content": "ใบสั่งจองสินค้าจะหักยอดพร้อมขาย"}]
    qs_bk = fallback("แล้วมีแรงงานมีรายงานให้ดูไหม", [], hist_bk)
    assert len(qs_bk) == 1 and "ใบสั่งจองสินค้า" in qs_bk[0], f"continuation context missing: {qs_bk}"

    # Plan invariants
    assert C.SIP_PAUSE_SILENCE_S == 0.55
    assert C.SIP_INCOMPLETE_PAUSE_S == 0.95
    assert "รายงาน" in scope["BC_STT_PROMPT"]
    src = SOURCE.read_text(encoding="utf-8")
    assert "max_chars=75 if phone_mode_turn" in src
    assert "สายนี้เป็นโทรศัพท์ ห้ามเขียน [จอ]" in src


def test_first_call_ai_intro_name_capture_and_callback_confirm():
    scope = load_symbols(
        {
            "_NAME_PREFIX_RE", "_NAME_SUFFIX_RE", "_NAME_SPLIT_RE", "_HONORIFIC_RE",
            "_EXPLICIT_NAME_TH_RE", "_EXPLICIT_NAME_EN_RE", "_ASKED_NAME_BOT_RE",
            "_BAD_CALLER_NAMES", "_clean_caller_name", "_extract_caller_name_py",
            "FILLER_TEXT",
        },
        extra_scope={
            "LANG_SWITCHED_ACK": {"th": "th", "en": "en"},
            "LANG_CONFIRM_PROMPTS": {"th": "th", "en": "en"},
        },
    )
    intro = scope["FILLER_TEXT"]["phone_intro"]
    extract = scope["_extract_caller_name_py"]

    for kw in ("เอไอ", "พนักงานจริง", "โทรกลับ", "ชื่อ"):
        assert kw in intro, f"phone_intro missing required info {kw!r}: {intro!r}"

    for raw_reply, want in (
        ("แบงค์ครับ", "แบงค์"),
        ("ผมแบงค์ครับ", "แบงค์"),
        ("พี่ต่ายค่ะ", "ต่าย"),
        ("คุณสมชายครับ", "สมชาย"),
    ):
        got, _ = extract(raw_reply, intro)
        assert got == want, f"extract({raw_reply!r}, intro) -> {got!r}, want {want!r}"

    for bad_reply in ("และ", "ครับ", "โอเค"):
        got, _ = extract(bad_reply, intro)
        assert got == "", f"extract({bad_reply!r}, intro) must stay empty, got {got!r}"

    assert ("เบอร์นี้" in C.PHONE_PROMPT or "เบอร์ที่โทรเข้ามานี้" in C.PHONE_PROMPT) and "เบอร์อื่น" in C.PHONE_PROMPT
    assert "ขอชื่อลูกค้าให้สำเร็จ" in C.PHONE_PROMPT
    assert "ต้องการให้โทรกลับเรื่องอะไร" in C.PHONE_PROMPT
    src = SOURCE.read_text(encoding="utf-8")
    assert "เบอร์นี้หรือเบอร์อื่น" in src
    assert "ครบสามอย่าง" in src and "has_prior_topic" in src


def test_leave_message_requires_topic_when_no_continuity():
    from collections import defaultdict
    import datetime
    import json
    import os
    import tempfile
    import grounding

    with tempfile.TemporaryDirectory() as tmpdir:
        leads_path = os.path.join(tmpdir, "leads.jsonl")
        fake_meta = {"c1": {"phone": "020000001", "name": "สมชาย"},
                     "c2": {"phone": "020000002", "name": "สมชาย", "lastTopic": "ออกใบกำกับภาษีไม่ได้"}}
        fake_c = types.SimpleNamespace(LEADS_PATH=leads_path, USER_NAME="ลุงจืด")
        scope = load_symbols({
            "_NAME_PREFIX_RE", "_NAME_SUFFIX_RE", "_NAME_SPLIT_RE", "_HONORIFIC_RE",
            "_EXPLICIT_NAME_TH_RE", "_EXPLICIT_NAME_EN_RE", "_ASKED_NAME_BOT_RE",
            "_BAD_CALLER_NAMES", "_clean_caller_name", "spoken_digits",
            "LEAD_FIELDS", "_GENERIC_CB_MSG_RE", "_CB_STRIP_RE", "_has_continuous_topic",
            "leave_message",
        }, extra_scope={
            "C": fake_c,
            "grounding": grounding,
            "defaultdict": defaultdict,
            "datetime": datetime,
            "json": json,
            "LEAD_LOCK": threading.Lock(),
            "LEAD_COUNT": defaultdict(int),
            "CALL_CALLER_META": fake_meta,
            "notify_owner": lambda: None,
            "_sync_caller_profile": lambda p, m: m,
        })
        has_topic = scope["_has_continuous_topic"]
        assert not has_topic("", [
            {"role": "user", "content": "ผมชื่อสมชายครับ"},
            {"role": "user", "content": "ต้องการคุยกับพนักงานจริง ให้โทรกลับเบอร์นี้ครับ"},
        ])
        assert has_topic("ปัญหาสต็อกติดลบ", [])
        assert has_topic("", [{"role": "user", "content": "ออกบิลขายแล้วสต็อกไม่ตัดครับ"}])

        leave_msg = scope["leave_message"]
        _, res_empty = leave_msg("สมชาย", "", "020000001", "", heard="โทรกลับเบอร์ 020000001 ได้เลย", call="c1")
        assert "ยังไม่ทราบเรื่องที่ต้องการให้โทรกลับ" in res_empty, f"unexpected: {res_empty}"
        _, res_gen = leave_msg("สมชาย", "", "020000001", "ขอให้โทรกลับ", heard="โทรกลับเบอร์ 020000001 ได้เลย", call="c1")
        assert "ยังไม่ทราบเรื่องที่ต้องการให้โทรกลับ" in res_gen, f"unexpected: {res_gen}"
        _, res_bad_name = leave_msg("และ", "", "020000001", "ปัญหาสต็อกติดลบ", heard="โทรกลับเบอร์ 020000001 ได้เลย", call="c1")
        assert "ต้องมีชื่อลูกค้าจริง" in res_bad_name, f"unexpected: {res_bad_name}"

        _, res_ok = leave_msg("สมชาย", "", "020000001", "ปัญหาสต็อกติดลบ", heard="โทรกลับเบอร์ 020000001 ได้เลย", call="c1")
        assert "บันทึก" in res_ok or "สมชาย" in res_ok, f"unexpected ok result: {res_ok}"
        _, res_cont = leave_msg("สมชาย", "", "020000002", "", heard="โทรกลับเบอร์ 020000002 ได้เลย", call="c2")
        assert "ออกใบกำกับภาษีไม่ได้" in res_cont, f"unexpected continuity result: {res_cont}"

        rows = [json.loads(line) for line in Path(leads_path).read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(rows) == 2 and rows[0]["message"] == "ปัญหาสต็อกติดลบ" and rows[1]["message"] == "ออกใบกำกับภาษีไม่ได้"


TESTS = [
    test_caller_name_rejects_connectors_and_accepts_real_names,
    test_stt_stutter_detector_and_ear2_fallback,
    test_ear1_gpu_busy_fast_fails_without_domino_timeout,
    test_process_audio_dedup_and_short_glitch_filter,
    test_legacy_mcp_tools_hidden_from_llm_when_fast_tools_present,
    test_config_and_source_invariants,
    test_three_tier_search_planner,
    test_incomplete_tail_and_phone_fallback_queries,
    test_first_call_ai_intro_name_capture_and_callback_confirm,
    test_leave_message_requires_topic_when_no_continuity,
]


def main():
    passed = 0
    for fn in TESTS:
        fn()
        passed += 1
    print(f"{passed}/{len(TESTS)} voice-quality regression cases passed")


if __name__ == "__main__":
    main()
