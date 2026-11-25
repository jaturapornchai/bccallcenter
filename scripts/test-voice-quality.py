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
import json
import logging
import re
import sys
import tempfile
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
    if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
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
        "json": json,
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
        "_BAD_CALLER_NAMES", "_GARBLED_NAME_RE", "_BARE_TONE_CUT_RE",
        "_is_plausible_name", "_clean_caller_name", "_extract_caller_name_py",
        "BARE_ACK", "BARE_ADDRESS",
    })
    extract = scope["_extract_caller_name_py"]
    asked = "ไม่ทราบว่าขอเรียกชื่อเล่นว่าอะไรดีครับ"

    for bad in ("และ", "แล้ว", "อันนี้", "เจ๊", "ครับผม", "โอเค", "คือ", "เอ่อ", "ค่ะ", "หรือ", "ถ้า", "ไหม", "การผลัดหรือการร้", "คุณการผลัดหรือการร้", "สวัสดีครับผม", "หวัดดีครับ", "ทักทาย"):
        got, _ = extract(bad, asked)
        assert got == "", f"expected bad token {bad!r} to be rejected, got {got!r}"

    got_exp_bad, _ = extract("ผมชื่อการผลัดหรือการร้ครับ", "")
    assert got_exp_bad == "", f"expected garbled explicit name rejected, got {got_exp_bad!r}"

    # "ขายเชื่อไหม", "เชื่อมกันได้หรือเปล่า", "เชื่อมโยงกันยังไง", "เชื่อว่า..." contain "ชื่อ" inside Thai words:
    # must NOT extract garbled caller names (regression: call 1795527470144-wok98)
    for phrase in (
        "สินค้า ที่ ขายเชื่อไหม",
        "ให้เซลฟเวอร์อยู่คนละที่อะ คนลักสาขาเชื่อมกันได้หรือเปล่า",
        "แล้วสาขากับคลางสินค้าเชื่อมโยงกันยังไง",
        "ผมเชื่อว่าโปรแกรมนี้ใช้ดี",
        "ชื่อเมนูอะไรครับ",
        "ชื่อสาขาต้องกรอกยังไง",
    ):
        got_bad, _ = extract(phrase, "")
        assert got_bad == "", f"expected {phrase!r} not to extract name, got {got_bad!r}"

    is_plausible = scope["_is_plausible_name"]
    assert not is_plausible("มโยงกันยังไง", explicit=True), "garbled sync name must be rejected"
    assert not is_plausible("มกันได้หรือเปล่า", explicit=True), "garbled sync name must be rejected"
    assert is_plausible("น้องไหม", explicit=True), "real Thai name containing ไหม must stay allowed"

    got_self, exp_self = extract("ผมชื่อก้องครับ", "")
    assert (got_self, exp_self) == ("ก้อง", True), f"self-intro must be explicit=True, got {(got_self, exp_self)!r}"
    got_bare, exp_bare = extract("เรียกว่าสมชายครับ", "")
    assert (got_bare, exp_bare) == ("สมชาย", False), f"bare เรียกว่า must be explicit=False, got {(got_bare, exp_bare)!r}"

    # _sync_caller_profile must reject garbled incoming names and filter corrupted stored names
    with tempfile.TemporaryDirectory() as prof_tmp:
        prof_path = str(Path(prof_tmp) / "caller_profiles.json")
        sync_scope = load_symbols(
            {
                "_NAME_PREFIX_RE", "_NAME_SUFFIX_RE", "_NAME_SPLIT_RE",
                "_BAD_CALLER_NAMES", "_GARBLED_NAME_RE", "_BARE_TONE_CUT_RE",
                "_normalize_phone", "_clean_caller_name", "_is_plausible_name",
                "_load_all_caller_profiles", "_save_all_caller_profiles", "_sync_caller_profile",
            },
            extra_scope={
                "CALLER_PROFILES_PATH": prof_path,
                "CALLER_PROFILES_LOCK": __import__("threading").Lock(),
                "_CALLER_PROFILES_CACHE": None,
                "datetime": __import__("datetime"),
                "json": __import__("json"),
                "os": __import__("os"),
                "threading": __import__("threading"),
                "C": type("_C", (), {"SESSIONS_DIR": prof_tmp})(),
            },
        )
        sync_fn = sync_scope["_sync_caller_profile"]
        m1 = sync_fn("020000098", {"name": "สมชาย", "totalCalls": 3})
        assert m1["name"] == "สมชาย"
        m2 = sync_fn("020000098", {"name": "มโยงกันยังไง", "totalCalls": 3})
        assert m2["name"] == "สมชาย", f"garbled sync must not overwrite existing name, got {m2['name']!r}"
        m3 = sync_fn("020000097", {"name": "มโยงกันยังไง", "totalCalls": 1})
        assert m3["name"] == "", f"garbled sync for unknown caller must become empty, got {m3['name']!r}"

    for good_in, want in (
        ("แบงค์ครับ", "แบงค์"),
        ("ผมชื่อก้องครับ", "ก้อง"),
        ("เรียกว่าลุงจืดครับ", "ลุงจืด"),
        ("สมชาย", "สมชาย"),
        ("ตี๋ครับ", "ตี๋"),
        ("โอ๋ค่ะ", "โอ๋"),
        ("สมคิดครับ", "สมคิด"),
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
                "MCP_CLIENTS": {},
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

    spec_names, tool_names = run_load(["quick_answer", "batch_ask", "lookup_menu", "batch_read", "search_brain", "read_note", "ask_brain"])
    assert set(spec_names) == {"quick_answer", "batch_ask", "lookup_menu", "batch_read"}, f"legacy tools must be hidden from LLM specs: {spec_names}"
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
        "_VAGUE_FOLLOWUP_RE", "_stt_ears_diverge", "_PHONE_INTENT_HINTS",
        "_expand_phone_intent_queries", "_STANDALONE_TOPIC_RE", "_BOILERPLATE_CTX_RE",
        "_extract_recent_topic", "_build_phone_fallback_queries", "BC_STT_PROMPT",
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
            "_BAD_CALLER_NAMES", "_GARBLED_NAME_RE", "_BARE_TONE_CUT_RE",
            "_is_plausible_name", "_clean_caller_name", "_extract_caller_name_py",
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
    assert "ห้ามถามเรื่องโทรกลับ" in C.PHONE_PROMPT
    assert "บีซีแอคเคาท์เวอร์ชันเก่า" in C.PHONE_PROMPT and "บีซีเอไอแอคเคาท์" in C.PHONE_PROMPT
    assert "บีซีเอไอแอคเคาท์" in intro and "บีซีแอคเคาท์เวอร์ชันเก่า" in intro
    src = SOURCE.read_text(encoding="utf-8")
    assert "เบอร์นี้หรือเบอร์อื่น" in src
    assert "ครบสามอย่าง" in src and "has_prior_topic" in src
    assert "cb_offered_recently" in src and "cb_already_saved" in src
    assert "CALL_GREET_SENT_AT" in src and "ถามเจาร์วิสได้ในเวอร์ชันใหม่" in src


def test_callback_restraint_qa_cache_fast_chat_and_batch():
    import qa_cache

    with tempfile.TemporaryDirectory() as tmpdir:
        qc = qa_cache.QACache(str(Path(tmpdir) / "qa.json"), ttl_s=3600, max_entries=50)
        assert not qc.store("ขั้นตอนหลังรับโปรแกรมต้องทำยังไง", ["เรื่องนี้ผมยังไม่มีข้อมูลยืนยันในระบบครับ"])
        assert not qc.store("มีระบบขนส่งไหม", ["ขอให้ฝ่ายขายโทรกลับไปตรวจสอบให้นะครับ"])
        assert not qc.store("แนะนำโปรแกรมวัสดุก่อสร้างหน่อย", ["เข้าใจแล้วครับคุณเพ็ญ ร้านนี้เหมาะกับค้าส่งครับ"])
        assert not qc.store("ดูดวงดูโด่งได้ไหม", ["เรื่องดูดวงนี่ผมไม่มีข้อมูลยืนยันครับ"])
        assert qc.store(
            "โปรแกรม BC Ai Account มีกี่รุ่นครับ",
            ["มี 4 รุ่น ได้แก่ Starter ฟรีศูนย์บาท และรุ่น Mini Standard Pro ครับ"],
        )
        assert len(qc._entries) == 1

    scope = load_symbols(
        {"FAST_CHAT", "MCP_CACHEABLE_TOOLS", "MCP_CACHE_TTL_S", "MCP_CACHE_MAX", "MCPClient"},
        extra_scope={"httpx": types.SimpleNamespace(HTTPStatusError=RuntimeError)},
    )
    fc = scope["FAST_CHAT"]
    for ack in ("โอเคขอบคุณ", "โอเคขอบคุณครับ", "รับทราบครับ", "เข้าใจแล้วค่ะ"):
        assert fc.match(ack), f"FAST_CHAT must match {ack!r}"
    assert not fc.match("โอเคขอบคุณแล้วราคาเท่าไหร่")

    post_calls = []

    class FakeResp:
        def __init__(self, payload, status_code=200):
            self._payload = payload
            self.status_code = status_code
            self.headers = {"content-type": "application/json"}
            self.text = json.dumps(payload)

        def raise_for_status(self):
            pass

    class FakeHttp:
        def post(self, url, json=None, headers=None):
            post_calls.append(json)
            if isinstance(json, list):
                return FakeResp([
                    {"jsonrpc": "2.0", "id": req["id"], "result": {"content": [{"type": "text", "text": f"batch:{req['params']['name']}"}]}}
                    for req in json
                ])
            method = json.get("method")
            if method == "initialize":
                return FakeResp({"jsonrpc": "2.0", "id": json["id"], "result": {"serverInfo": {"name": "bcbrain"}}})
            return FakeResp({"jsonrpc": "2.0", "id": json["id"], "result": {}})

    MCPClient = scope["MCPClient"]
    client = object.__new__(MCPClient)
    client.url = "https://brain.bcsmethai.com/mcp"
    client.http = FakeHttp()
    client.n = 0
    client.headers = {}
    client._cache = {}
    client._cache_lock = threading.Lock()
    client._rpc_lock = threading.RLock()

    res1 = client.call_batch([("quick_answer", {"question": "q1"}), ("lookup_menu", {"query": "4013"})])
    assert res1 == ["batch:quick_answer", "batch:lookup_menu"]
    assert len(post_calls) == 1 and isinstance(post_calls[0], list) and len(post_calls[0]) == 2
    res2 = client.call_batch([("quick_answer", {"question": "q1"}), ("lookup_menu", {"query": "4013"})])
    assert res2 == res1 and len(post_calls) == 1, "second call_batch must hit RAM cache with 0 HTTP calls"


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

        cards_ok, res_ok = leave_msg("สมชาย", "", "020000001", "ปัญหาสต็อกติดลบ", heard="โทรกลับเบอร์ 020000001 ได้เลย", call="c1")
        assert "บันทึก" in res_ok or "สมชาย" in res_ok, f"unexpected ok result: {res_ok}"
        assert cards_ok and isinstance(cards_ok[0].get("lead"), dict), "leave_message must return structured lead dict"
        lead_obj = cards_ok[0]["lead"]
        for k in ("name", "company", "phone", "callerPhone", "message", "at"):
            assert k in lead_obj, f"missing lead field {k}"
        assert lead_obj["name"] == "สมชาย" and lead_obj["phone"] == "020000001" and lead_obj["callerPhone"] == "020000001"
        assert lead_obj["message"] == "ปัญหาสต็อกติดลบ"

        _, res_cont = leave_msg("สมชาย", "", "020000002", "", heard="โทรกลับเบอร์ 020000002 ได้เลย", call="c2")
        assert "ออกใบกำกับภาษีไม่ได้" in res_cont, f"unexpected continuity result: {res_cont}"

        rows = [json.loads(line) for line in Path(leads_path).read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(rows) == 2 and rows[0]["message"] == "ปัญหาสต็อกติดลบ" and rows[1]["message"] == "ออกใบกำกับภาษีไม่ได้"
        assert '"callback_request"' in SOURCE.read_text(encoding="utf-8")


def test_bcbrain_v230_zone_abstain_and_ultra_mode():
    scope = load_symbols({
        "SQL_ASK", "brain_audience", "_is_brain_abstain",
        "MCP_CACHE_TTL_S", "MCP_CACHEABLE_TOOLS", "BRAIN_AUDIENCE_TOOLS",
    })
    assert scope["brain_audience"](True, "ขอดูตาราง SQL ic_trans") == {"audience": "user"}
    assert scope["brain_audience"](False, "ขอดูตาราง SQL ic_trans") == {"audience": "dev"}
    assert scope["brain_audience"](False, "ออกใบกำกับภาษีเข้าเมนูไหน") == {"audience": "user"}
    assert scope["_is_brain_abstain"]("🛑 Abstain: ไม่มีข้อมูลยืนยันในระบบ") is True
    assert scope["_is_brain_abstain"](
        "[batch_ask | count=2]\n\n## Q1: raw\n🛑 Abstain\n\n## Q2: hint\n[quick_answer:ultra | audience=user | confidence=high | slug: start-first-day]\n# วันแรก"
    ) is False
    assert scope["_is_brain_abstain"]("เมนู 4013 ขายสินค้า/บริการ") is False
    assert "batch_ask" in scope["MCP_CACHEABLE_TOOLS"]
    assert scope["MCP_CACHE_TTL_S"] >= 300
    assert Path(C.LOCAL_BRAIN_DIR).is_dir(), f"embedded local brain vault must exist: {C.LOCAL_BRAIN_DIR}"
    assert C.MCP_SERVERS == {}, f"external MCP servers must be off by default: {C.MCP_SERVERS}"
    assert C.BRAIN_BOOTSTRAP_TTL_S >= 1800
    src = SOURCE.read_text(encoding="utf-8")
    assert '"ultra"' in src and "_pin_user_audience" in src and "load_bootstrap_pack" in src and "LocalBrainClient" in src


def test_vault_mirror_intent_expander_planner_and_ice_filter():
    from brain_mirror import BrainVaultMirror

    mirror = BrainVaultMirror("https://brain.bcsmethai.com/api/vault-pack", ttl_s=1800)
    mirror.load_notes_payload(
        {
            "version": "2.3.0",
            "syncCommit": "test",
            "notes": [
                {
                    "slug": "menu-cat-4-sales",
                    "folder": "start",
                    "raw": "---\ntitle: เมนูหมวดขาย\ntags: [menu]\n---\n| **4013** | **4.13** | **ขายสินค้า/บริการ**<br>ขาย > ขายสินค้า/บริการ | ทุกรุ่น (Starter+) |\n",
                },
                {
                    "slug": "start-first-day",
                    "folder": "start",
                    "raw": "---\ntitle: วันแรกที่เริ่มใช้ BC Ai Account\ntags: [เริ่มต้น]\naliases: [onboarding]\nsources:\n  - secret/path.go:1-10\n---\n# วันแรกที่เริ่มใช้ BC Ai Account\nตั้งค่ากิจการและนำเข้าสินค้าครับ",
                },
                {
                    "slug": "api-mcp-gateway",
                    "folder": "api",
                    "raw": "---\ntitle: MCP Dev Gateway\naudience: dev\nsources:\n  - backend/mcp.go:1\n---\nSQL table ic_trans secret dev doc",
                },
            ],
        },
        etag='"test-etag"',
    )
    assert mirror.stats()["notes"] == 3 and mirror.stats()["menus"] == 1
    menu_out = mirror.lookup_menu("เข้าเมนู 4013 ยังไง", audience="user")
    assert menu_out and "4013" in menu_out and "ขายสินค้า/บริการ" in menu_out
    assert mirror.lookup_menu("4013", audience="dev") is None

    br_user = mirror.batch_read(["start-first-day", "api-mcp-gateway"], audience="user")
    assert br_user and "ตั้งค่ากิจการ" in br_user
    assert "ic_trans" not in br_user and "secret/path.go" not in br_user
    assert mirror.read_note("api-mcp-gateway", audience="user") is None
    assert "ic_trans" in (mirror.read_note("api-mcp-gateway", audience="dev") or "")

    scope = load_symbols({
        "_FOLLOWUP_START_RE", "_VAGUE_FOLLOWUP_RE", "_stt_ears_diverge",
        "_PHONE_INTENT_HINTS", "_expand_phone_intent_queries",
        "_needs_search_planner", "_filter_bindable_ice_addresses",
    })
    needs = scope["_needs_search_planner"]
    expand = scope["_expand_phone_intent_queries"]
    ice_filter = scope["_filter_bindable_ice_addresses"]

    # Clear 50-char Thai sentence with agreeing ears must use Fast-Path (no planner wait)
    clear_50 = "ระบบคิดต้นทุนสินค้าแบบไฟโฟทำยังไงบ้างครับ"
    assert not needs(clear_50, [clear_50], [])
    assert needs("ก" * 82, [], [])

    for phrase, expected_kw in (
        ("มีระบบขนส่งแบบช้อปปี้หรือเปล่า", "TMS"),
        ("หลังจากตกลงรับโปรแกรมแล้วต้องเตรียมอะไร", "วันแรก"),
        ("ถ้าฟรีศูนย์บาทแล้วคุณจะได้เงินยังไง", "Starter"),
        ("สต็อกสินค้าไม่ตรงต้องทำยังไง", "สต็อกไม่ตรง"),
    ):
        ex = expand(phrase)
        assert ex and any(expected_kw in x for x in ex), f"expand({phrase!r}) missing {expected_kw}: {ex}"

    filtered = ice_filter(["169.254.10.5", "fe80::1ff:fe23:4567:890a", "192.168.1.50", "10.0.0.2"])
    assert filtered == ["192.168.1.50", "10.0.0.2"]
    assert ice_filter(["169.254.10.5"]) == ["169.254.10.5"], "must keep fallback list if all addresses filtered"


def test_log_and_audio_6_improvements():
    scope = load_symbols({
        "_STT_STUTTER_3_RE", "_STT_STUTTER_2X3_RE", "_has_stt_stutter",
        "_STT_DOMAIN_FIX_RE", "_prefer_openthai_domain",
        "_URL_LINE_TTS_RULES", "_THAI_TERMS", "_TERM_RE", "_TERM_MAP", "_LETTER_TH", "_thai_pronounce",
        "_phone_grounding_numbers", "_NON_SEARCH_STRIP_RE", "_CB_INTENT_ONLY_RE", "_REAL_Q_OR_PRODUCT_RE",
        "_CALL_META_ASK_RE", "_CLOSING_ONLY_RE", "_BOILERPLATE_CTX_RE", "_extract_recent_topic",
        "_is_non_search_phone_turn", "_FOLLOWUP_START_RE", "_VAGUE_FOLLOWUP_RE", "_stt_ears_diverge",
        "_PHONE_INTENT_HINTS", "_expand_phone_intent_queries", "_STANDALONE_TOPIC_RE", "_build_phone_fallback_queries",
        "FILLER_TEXT", "LEAD_PROMISE",
    }, extra_scope={
        "LANG_SWITCHED_ACK": {"th": "th", "en": "en"},
        "LANG_CONFIRM_PROMPTS": {"th": "th", "en": "en"},
    })

    # 1 & 2. Phantom callback guard + phone grounding numbers
    assert scope["LEAD_PROMISE"].search("บันทึกไว้ให้แล้ว เดี๋ยวเจ้าหน้าที่ติดต่อกลับครับ")
    nums = scope["_phone_grounding_numbers"]("+66812345678")
    assert {"0812345678", "66812345678", "081", "234", "5678"} <= nums

    # 3. Cached returning-caller greeting notices & fast timeout
    assert "version_notice" in scope["FILLER_TEXT"] and "returning_ask" in scope["FILLER_TEXT"]
    src = SOURCE.read_text(encoding="utf-8")
    assert "cb_already_saved = LEAD_COUNT.get(cid, 0) > 0" in src
    assert "bot_claimed_saved" in src and "LEAD_COUNT.get(cid, 0) == 0" in src
    assert "early.result(timeout=0.05 if phone_now else None)" in src
    assert "prev_busy = prev_job is not None" in src

    # 4. Non-search phone turns & standalone fallback query
    non_search = scope["_is_non_search_phone_turn"]
    assert non_search("คุณภูมิครับ", "ภูมิ", []) is True
    assert non_search("ผมชื่อสมชายครับ อยากทราบราคาแพ็กเกจ", "สมชาย", []) is False
    assert non_search("พอดีอยากติดต่อเจ้าหน้าที่ขออนุญาตฝากเบอร์โทรกลับครับ", "", []) is True
    assert non_search(
        "ใช่เบอร์นี้แหละแล้วก็อยากให้ติดต่อกับเรื่องสินค้าที่ BC ขายทั้งหมด",
        "",
        [{"role": "assistant", "content": "จะให้โทรกลับที่เบอร์ที่โทรเข้ามานี้... ใช่ไหมครับ แล้วอยากให้ติดต่อกลับเรื่องอะไรครับ"}],
    ) is True
    assert non_search("ถามเรื่องต้นทุนหน่อย", "", []) is False

    qs = scope["_build_phone_fallback_queries"](
        "แล้วก็เบอร์โทรติดต่อเบอร์อะไร", [], [{"role": "assistant", "content": "เว็บไซต์ดูได้ที่ www.bcaccount.com"}]
    )
    assert len(qs) == 1 and "www.bcaccount.com" not in qs[0]

    # 5. OpenThai domain keyword preference
    prefer_e2 = scope["_prefer_openthai_domain"]
    assert prefer_e2("ของคุณมีกี่วันซะ", "ของคุณมีกี่เวอร์ชั่น", "ของคุณมีกี่เวอร์ชั่น") is True
    assert prefer_e2("ติดต่อเบอร์โทรอะไร", "ติดต่อเบอร์อะไร", "ติดต่อเบอร์อะไร") is False

    # 6. URL/LINE TTS pronunciation, barge-in thresholds, 4 tiers prompt & Next-Action 404
    pron = scope["_thai_pronounce"]("ดูที่ www.bcaccount.com หรือไลน์ @bcaccount")
    assert "ดอท คอม" in pron and "แอด บี ซี แอค เคานต์" in pron
    assert C.SIP_BARGE_MIN_DBFS == -31.0
    assert C.SIP_EARLY_STT_SILENCE_S == 0.35
    assert "ให้เอ่ยชื่อรุ่นที่มีในผลค้นให้ครบทุกชื่อ" in C.PHONE_PROMPT
    assert 'req.headers["next-action"]' in (ROOT / "server.mjs").read_text(encoding="utf-8")


def test_abstain_nearby_fallback_zero_hallucination():
    from brain_mirror import BrainVaultMirror

    mirror = BrainVaultMirror("https://brain.bcsmethai.com/api/vault-pack", ttl_s=1800)
    mirror.load_notes_payload(
        {
            "version": "2.3.0",
            "syncCommit": "test",
            "notes": [
                {
                    "slug": "sys-companybrain-briefing",
                    "folder": "systems",
                    "raw": "---\ntitle: ศูนย์แจ้งเตือนยามเช้า\ntags: [แจ้งเตือน]\nsources:\n  - secret/briefing.go:1\n---\n# ศูนย์แจ้งเตือนยามเช้า\n## 1. การ์ดสรุป 6 ด้าน\nรวมลูกหนี้เกินกำหนดชำระ บิลขายรอวางบิล และเจ้าหนี้ครบกำหนดใน 7 วัน",
                },
                {
                    "slug": "howto-setup-branch-dept",
                    "folder": "howto",
                    "raw": "---\ntitle: ตั้งค่าสาขา สำนักงานใหญ่ แผนก\ntags: [สาขา]\n---\n# ตั้งค่าสาขา\n## 1. กำหนดรายละเอียดสาขา\nช่วยให้แยกยอดขาย ต้นทุน และกำไรขาดทุนตามสาขาได้ชัดเจนที่เมนู 1007",
                },
                {
                    "slug": "rpt-sale-so",
                    "folder": "reports",
                    "raw": "---\ntitle: รายงานระบบขาย\ntags: [ยอดขาย]\n---\n# รายงานระบบขาย\n## 2. สรุปยอดขายและบิลขาย\nดูรายงานสรุปยอดขายสินค้าตามวันที่ ใบสั่งขาย และกำไรขั้นต้น",
                },
                {
                    "slug": "sales-overview",
                    "folder": "sales",
                    "raw": "---\ntitle: ภาพรวมจุดเด่น BC Ai Account\ntags: [คลาวด์, นิติบุคคล]\n---\n# ภาพรวม BC Ai Account\n## รุ่น Enterprise\nทำงานบนเว็บ รองรับกลุ่มบริษัทหลายนิติบุคคล และ Private Cloud",
                },
                {
                    "slug": "menu-cat-4-sales",
                    "folder": "start",
                    "raw": "---\ntitle: ผังเมนูหมวดขาย\ntags: [menu]\n---\n| **4052** | **4.23.3** | **รายงานสรุปยอดขายสินค้า-สรุปตามวันที่**<br>ขาย > รายงานสรุปยอดขาย | ทุกรุ่น (Starter+) |\n| **9063** | **9.24.8** | **งบกำไรขาดทุน (แยกสาขา)**<br>บัญชี > งบการเงิน | รุ่น Standard+ |\n",
                },
                {
                    "slug": "api-secret-cloud-branch",
                    "folder": "api",
                    "raw": "---\ntitle: Internal Cloud SQL Schema\naudience: dev\nsources:\n  - backend/db.go:99\n---\nSELECT branch_id FROM ic_trans FOR แจ้งเตือน คลาวด์ ยอดขาย สาขา",
                },
            ],
        },
        etag='"nearby-test"',
    )

    n1 = mirror.search_nearby("มีเตือนถึงดีลเก็บเงินจ่ายเงินไหม", audience="user")
    assert n1 and "sys-companybrain-briefing" in n1["slugs"]
    assert "ic_trans" not in n1["text"] and "secret/" not in n1["text"] and "api-secret-cloud-branch" not in n1["slugs"]

    n2 = mirror.search_nearby("อีกที่มีสาขาจะมีปัญหาเรื่องคราวกไหมเพราะเป็นนิติบุคคล", audience="user")
    assert n2 and ("sales-overview" in n2["slugs"] or "howto-setup-branch-dept" in n2["slugs"])
    assert "ic_trans" not in n2["text"]

    n3 = mirror.search_nearby("ออกบินแล้วสรุปยอดขายแยกสาขาได้ไหม", audience="user")
    assert n3 and ("rpt-sale-so" in n3["slugs"] or "howto-setup-branch-dept" in n3["slugs"])
    assert "4052" in n3["menus"] or "9063" in n3["menus"]

    for neg in ("ผมโทรหาคุณกี่รอบแล้วจำได้หรือเปล่า", "คุณสรุปให้หน่อยว่าผมเป็นคนนิสัยยังไง", "วันนี้อากาศดีไหม"):
        assert mirror.search_nearby(neg, audience="user") is None, f"off-topic query must not match nearby note: {neg}"

    # Verify mcp_tool Abstain path attaches nearby info while keeping strict Abstain guard
    class StubClient:
        def call(self, name, args):
            return "🛑 Abstain — Insufficient Grounded Evidence in BC Brain"

    scope = load_symbols(
        {"LEGACY_BRAIN_TOOLS", "MENU_CODE_ASK", "_is_brain_abstain", "_mcp_nearby_fallback", "mcp_tool"},
        extra_scope={"BRAIN_MIRROR": mirror},
    )
    qa_fn = scope["mcp_tool"]("bcbrain", StubClient(), "quick_answer")
    _, res_txt = qa_fn(question="มีเตือนถึงดีลเก็บเงินจ่ายเงินไหม", audience="user")
    assert scope["_is_brain_abstain"](res_txt) is True, "nearby fallback must keep Abstain marker so QA cache skips it"
    assert "[ข้อมูลใกล้เคียงจากคลังความรู้ BC" in res_txt and "ศูนย์แจ้งเตือนยามเช้า" in res_txt and "ห้ามเดา" in res_txt
    assert "ic_trans" not in res_txt

    # Verify caller-meta skip, boilerplate context filter, and intent expanders
    s2 = load_symbols({
        "_NON_SEARCH_STRIP_RE", "_CB_INTENT_ONLY_RE", "_REAL_Q_OR_PRODUCT_RE",
        "_CALL_META_ASK_RE", "_CLOSING_ONLY_RE", "_BOILERPLATE_CTX_RE",
        "_extract_recent_topic", "_is_non_search_phone_turn",
        "_FOLLOWUP_START_RE", "_VAGUE_FOLLOWUP_RE", "_stt_ears_diverge",
        "_PHONE_INTENT_HINTS", "_expand_phone_intent_queries", "_STANDALONE_TOPIC_RE",
        "_build_phone_fallback_queries", "TALK_WORDS",
    })
    assert s2["_is_non_search_phone_turn"]("ผมโทรหาคุณกี่รอบแล้วจำได้หรือเปล่า", "", []) is True
    assert s2["_is_non_search_phone_turn"]("แล้วเราคุยอะไรกันมาบ้าง", "", []) is True
    assert s2["_is_non_search_phone_turn"]("โอเคค่ะขอบคุณนะคะไม่มีอะไรแล้วครับ", "", []) is True
    assert s2["_is_non_search_phone_turn"]("เบอร์โทรติดต่อบริษัทเบอร์อะไร", "", []) is False
    assert s2["_is_non_search_phone_turn"]("โปรแกรมนี้มีรายงานสรุปยอดขายแยกสาขาไหม", "", []) is False

    hist = [
        {"role": "assistant", "content": "สวัสดีครับ ลุงจืด เจาร์วิสครับ ตอนนี้เวอร์ชันล่าสุดคือบีซีเอไอแอคเคาท์"},
        {"role": "user", "content": "เรื่องสต็อกสินค้าและซีเรียลนัมเบอร์"},
        {"role": "assistant", "content": "เรื่องนี้ผมยังไม่มีข้อมูลยืนยันในระบบครับ"},
    ]
    assert "ซีเรียล" in s2["_extract_recent_topic"](hist)
    qs = s2["_build_phone_fallback_queries"]("แล้วดูยังไง", [], hist)
    assert qs and "ซีเรียล" in qs[0] and "เจาร์วิสครับ" not in qs[0] and "ยังไม่มีข้อมูลยืนยัน" not in qs[0]

    for phrase in ("เตือนถึงดีลเก็บเงินจ่ายเงิน", "มีสาขาอีกที่นึงปัญหาเรื่องคราวก", "ออกบินแล้วมาสรุปยอดขายแยกสาขา"):
        assert s2["_expand_phone_intent_queries"](phrase), f"expected intent hint for {phrase!r}"

    assert "ใกล้เคียง" in s2["TALK_WORDS"]
    assert "ใกล้เคียง" in C.SYSTEM_PROMPT and "ใกล้เคียง" in C.PHONE_PROMPT
    assert "BRAIN-NEARBY-FALLBACK" in SOURCE.read_text(encoding="utf-8")
    assert "used_nearby_only" in SOURCE.read_text(encoding="utf-8")
    lk_tools_src = (ROOT / "livekit-voice-ai" / "tools" / "call_control.py").read_text(encoding="utf-8")
    assert "brain.search_nearby" in lk_tools_src and "get_local_brain" in lk_tools_src
    assert "ข้อมูลใกล้เคียง" in (ROOT / "livekit-voice-ai" / "llm" / "thai_llm.py").read_text(encoding="utf-8")


def test_wok98_log_5_improvements():
    import qa_cache
    from brain_mirror import BrainVaultMirror

    # 1. QA Cache rejects vague context-dependent requests without a product/topic anchor & auto-prunes on load
    assert qa_cache.is_cacheable_question("เล่าภาพรวมมาเลย") is False
    assert qa_cache.is_cacheable_question("อธิบายเพิ่มหน่อย") is False
    assert qa_cache.is_cacheable_question("เล่าภาพรวมโปรแกรม BC Ai Account หน่อย") is True
    with tempfile.TemporaryDirectory() as tmpdir:
        qpath = Path(tmpdir) / "qa.json"
        qpath.write_text(
            json.dumps(
                {
                    "th|phone|เล่าภาพรวมมา": {
                        "norm": "เล่าภาพรวมมา",
                        "question": "เล่าภาพรวมมาเลย",
                        "lang": "th",
                        "phone_mode": True,
                        "taught": False,
                        "saved_at": time.time(),
                        "sentences": ["ระบบบีซีออโต้แคร์มีห้าสายงานหลัก"],
                    },
                    "th|phone|มาคุยเรื่องเงินสดธนาคาร": {
                        "norm": "มาคุยเรื่องเงินสดธนาคาร",
                        "question": "มาคุยเรื่องเงินสดธนาคารดีกว่าทำงานยังไง",
                        "lang": "th",
                        "phone_mode": True,
                        "taught": False,
                        "saved_at": time.time(),
                        "sentences": ["หมวดเงินสดธนาคารมีเมนูบันทึกเงินสดและเช็คครับ"],
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        qc = qa_cache.QACache(str(qpath), ttl_s=3600, max_entries=50)
        assert qc.lookup("เล่าภาพรวมมาเลย", lang="th", phone_mode=True) is None
        assert "th|phone|เล่าภาพรวมมา" not in qc._entries
        assert "th|phone|มาคุยเรื่องเงินสดธนาคาร" in qc._entries

    # 2. Mirror STT typo normalization + domain synonyms for server / autocare / warehouse / location
    mirror = BrainVaultMirror("https://brain.bcsmethai.com/api/vault-pack", ttl_s=1800)
    mirror.load_notes_payload(
        {
            "version": "2.3.0",
            "syncCommit": "wok98",
            "notes": [
                {
                    "slug": "sales-accounting-partner",
                    "folder": "sales",
                    "raw": "---\ntitle: พาร์ตเนอร์สำนักงานบัญชี\n---\nใช้ฟรี แยก Server เฉพาะสำนักงานได้",
                },
                {
                    "slug": "sys-autocare-garage",
                    "folder": "systems",
                    "raw": "---\ntitle: ระบบอู่ซ่อมรถ AutoCare\n---\nเหมาะกับร้านยาง อู่ซ่อมรถ เปลี่ยนน้ำมันเครื่อง",
                },
                {
                    "slug": "rpt-stock-cost",
                    "folder": "reports",
                    "raw": "---\ntitle: รายงานยอดคงเหลือสินค้าและต้นทุน\n---\nดูยอดคงเหลือคลังสินค้าและจุดสั่งซื้อ",
                },
                {
                    "slug": "howto-wms-pick-pack-bin",
                    "folder": "howto",
                    "raw": "---\ntitle: แผนผังจุดเก็บ WMS\n---\nจัดเก็บตามโซน แร็ค ชั้นวาง (Bin Location)",
                },
            ],
        },
        etag='"wok98"',
    )
    assert "sales-accounting-partner" in mirror.search_nearby("โปรแกรมคุณแยกเซิร์ฟเวอร์ได้หรือเปล่า", audience="user")["slugs"]
    assert "sys-autocare-garage" in mirror.search_nearby("ธุรกิจขายอย่างรถยนต์ใช้ได้หรือเปล่า", audience="user")["slugs"]
    assert "rpt-stock-cost" in mirror.search_nearby("ยอดโครงเรือรวมคังดูยังไง", audience="user")["slugs"]
    assert "howto-wms-pick-pack-bin" in mirror.search_nearby("โลเคชั่นที่เป็นชั้นทำยังไง", audience="user")["slugs"]

    # 3. Conditional incomplete-tail wait + topic-switch guard in fallback queries
    s = load_symbols({
        "_INCOMPLETE_TAIL_RE", "_is_incomplete_thai_tail", "_FOLLOWUP_START_RE",
        "_VAGUE_FOLLOWUP_RE", "_stt_ears_diverge", "_PHONE_INTENT_HINTS",
        "_expand_phone_intent_queries", "_STANDALONE_TOPIC_RE", "_BOILERPLATE_CTX_RE",
        "_extract_recent_topic", "_build_phone_fallback_queries", "BC_STT_PROMPT",
    })
    assert s["_is_incomplete_thai_tail"]("แล้วถ้ามี") is True
    assert s["_is_incomplete_thai_tail"]("แล้วถ้าเป็นธุรกิจขายยางรถยนต์") is True
    assert s["_is_incomplete_thai_tail"]("แล้วถ้าเป็นธุรกิจขายยางรถยนต์จะใช้ได้หรือเปล่า") is False

    hist_auto = [{"role": "user", "content": "ระบบบีซีออโต้แคร์ร้านยาง"}, {"role": "assistant", "content": "เมนู 1001 นัดหมายช่าง"}]
    for topic_q in ("แล้วมีรายงานสินค้าคงเหลือกี่แบบ", "แล้วถ้าเป็นธุรกิจขายยางรถยนต์", "มาคุยเรื่องเงินสดธนาคารดีกว่าทำงานยังไง"):
        qs = s["_build_phone_fallback_queries"](topic_q, [], hist_auto)
        assert qs and "ออโต้แคร์" not in qs[0] and "1001" not in qs[0], f"topic leak in {topic_q!r}: {qs}"

    # 4. Source invariants for cross-thread carry & BRAIN-NEARBY-SUPPLEMENT standalone guard
    src = SOURCE.read_text(encoding="utf-8")
    assert "carry_cid = cid or call_id()" in src and 'PENDING[carry_cid]["carry"]' in src
    assert "not _STANDALONE_TOPIC_RE.search(heard)" in src
    assert "speech_s < 0.08 or short_heard not in self.SIP_CUT_INS" in src

    # 5. Sub-80ms glitch ("ครับ" 0.03s in SIP_CUT_INS) must be dropped as noise in process_audio
    class BaseReplyOnPause:
        def process_audio(self, audio, state):
            state.pause_detected = True

        def reset(self):
            pass

    pa_scope = load_symbols(
        {"_audio_dbfs"},
        class_names={"process_audio", "reset", "cut", "_checked_stream_len", "_checked_state_id", "CUT_INS", "SIP_CUT_INS"},
        extra_scope={
            "on_phone": lambda: True,
            "voiced": lambda a: a[1],
            "EARLY": {},
            "LIVE": {},
            "PENDING": {"sip-1-tiny": {"text": ""}},
            "call_id": lambda: "sip-1-tiny",
            "create_message": lambda k, d: (k, d),
        },
    )

    class TinyHandler(pa_scope["Handler"], BaseReplyOnPause):
        playing = ()
        def hears_itself(self, state): return False
        def just_filler(self, state): return False
        def no_cut_in(self, state, speech_s): return False
        def send_message_sync(self, msg): pass
        def cut_off(self, reason=""): pass
        def push_turn(self, interrupt=False): pass

    h = TinyHandler()
    st_tiny = types.SimpleNamespace(
        pause_detected=False, started_talking=True, responding=False,
        sampling_rate=SR, stream=np.full(int(SR * 0.03), 0.2, dtype=np.float32), buffer=None,
    )
    pa_scope["EARLY"][id(h)] = (st_tiny.stream, types.SimpleNamespace(result=lambda: "ครับ"))
    h.process_audio((SR, np.zeros(320, dtype=np.float32)), st_tiny)
    assert st_tiny.pause_detected is False and st_tiny.stream is None, "0.03s 'ครับ' glitch must be rejected"


def test_embedded_local_bcbrain_no_mcp_api():
    """Verify bcbrain/vault runs 100% in-process with zero MCP HTTP API, OOV abstain, isolation & hot-reload."""
    import tempfile
    from brain_mirror import BrainVaultMirror, LocalBrainClient

    brain = BrainVaultMirror(vault_dir=C.LOCAL_BRAIN_DIR)
    st = brain.load_from_dir(C.LOCAL_BRAIN_DIR, force=True)
    assert st["mode"] == "local", f"expected local mode: {st}"
    assert st["notes"] >= 350 and st["menus"] >= 700, f"expected full embedded vault loaded: {st}"

    client = LocalBrainClient(brain)
    tool_names = {t["name"] for t in client.read_only_tools()}
    assert {"quick_answer", "batch_ask", "lookup_menu", "lookup_error", "batch_read", "read_note", "search_brain", "traverse_brain", "list_notes", "find_media"} <= tool_names

    # Direct local queries (0 HTTP)
    m4013 = client.call("lookup_menu", {"query": "4013", "audience": "user"})
    assert "4013" in m4013 and "ขาย" in m4013, f"expected menu 4013: {m4013}"
    qa_price = client.call("quick_answer", {"question": "ราคาแพ็กเกจ BC Ai Account เท่าไหร่", "audience": "user", "mode": "ultra"})
    assert "🛑" not in qa_price and "quick_answer:ultra" in qa_price, f"expected grounded price answer: {qa_price[:200]}"
    ba = client.call("batch_ask", {"questions": ["เมนู 4013 คืออะไร", "สต็อกติดลบตั้งค่าตรงไหน"], "audience": "user"})
    assert "[batch_ask | count=2" in ba and "4013" in ba

    # OOV trap & strict user/dev isolation
    oov_menu = client.call("quick_answer", {"question": "เมนู 9999 ทำอะไร", "audience": "user"})
    assert "🛑" in oov_menu, f"unknown 4-digit menu must abstain: {oov_menu}"
    oov_nonsense = client.call("quick_answer", {"question": "ซุปเปอร์โนวาดาวพฤหัสสีม่วง", "audience": "user"})
    assert "🛑" in oov_nonsense, f"out-of-domain query must abstain: {oov_nonsense}"
    dev_note_as_user = client.call("read_note", {"slug": "api-overview", "audience": "user"})
    assert "🛑" in dev_note_as_user, f"dev note must be blocked for user audience: {dev_note_as_user}"
    dev_note_as_dev = client.call("read_note", {"slug": "api-overview", "audience": "dev"})
    assert "🛑" not in dev_note_as_dev and len(dev_note_as_dev) > 50

    # Local bootstrap pack (0 HTTP, strict user-zone isolation)
    bp = brain.get_bootstrap_pack("user")
    assert len(bp) > 3000 and "4013" in bp and "sales-pricing-packages" in bp and "ic_trans" not in bp

    # Hot-reload test on temp dir
    with tempfile.TemporaryDirectory() as td:
        p1 = Path(td) / "faq"
        p1.mkdir(parents=True, exist_ok=True)
        (p1 / "faq-hot-test.md").write_text("---\ntitle: ทดสอบฮอตรีโหลด\naudience: user\n---\nเนื้อหาเดิม", encoding="utf-8")
        hb = BrainVaultMirror(vault_dir=td, watch_interval_s=1.0)
        assert hb.load_from_dir(td, force=True)["notes"] == 1
        (p1 / "faq-hot-new.md").write_text("---\ntitle: โน้ตมาใหม่สดๆ\naudience: user\n---\nข้อมูลใหม่ทันที", encoding="utf-8")
        hb._last_watch_m = 0.0
        hb.maybe_refresh()
        assert hb.stats()["notes"] == 2 and "ข้อมูลใหม่ทันที" in (hb.read_note("faq-hot-new", audience="user") or "")


def test_voice_first_big_brain_and_zyx59_fixes():
    """Regression from 2026-11-25 calls (zyx59, bzy7y, 8y14z) + voice-first media-free big brain."""
    from brain_mirror import BrainVaultMirror

    brain = BrainVaultMirror(vault_dir=C.LOCAL_BRAIN_DIR)
    brain.load_from_dir(C.LOCAL_BRAIN_DIR, force=True)

    # 1. STT spelling "โปรโมชั่น" (with tone mark) must match "โปรโมชัน" note and include "10 รูปแบบ" with big voice budget
    qa_promo = brain.quick_answer("สรุปว่าโปรโมชั่นมีทั้งหมดกี่แบบอะไรบ้าง", audience="user", mode="ultra", max_chars=2200)
    assert "🛑" not in qa_promo, f"promotion query must not abstain: {qa_promo[:250]}"
    assert "10 รูปแบบ" in qa_promo, f"big voice brain must include '10 รูปแบบ': {qa_promo[:500]}"
    assert "youtube.com" not in qa_promo.lower() and "![" not in qa_promo, "voice brain must strip images/YouTube"

    # 2. Filler/frustration turns ("ไม่เป็นอย่างงั้น") must NOT pull unrelated deposit notes via low nearby score
    assert brain.search_nearby("ไม่เป็นอย่างงั้น", audience="user") is None, "filler turn must not match deposit note"
    assert brain.search_nearby("คุยต่อ", audience="user") is None, "bare continuation must not match random note"

    # 3. Bootstrap pack must be large, voice-ready, and free of YouTube URLs or images
    bp = brain.get_bootstrap_pack("user")
    assert len(bp) > 8000 and "youtube.com" not in bp.lower() and "![" not in bp

    # 4. Context isolation & callback detection from zyx59/8y14z
    scope = load_symbols({
        "_FOLLOWUP_START_RE", "_VAGUE_FOLLOWUP_RE", "_STANDALONE_TOPIC_RE",
        "_BOILERPLATE_CTX_RE", "_NON_SEARCH_STRIP_RE", "_CB_INTENT_ONLY_RE",
        "_REAL_Q_OR_PRODUCT_RE", "_CALL_META_ASK_RE", "_CLOSING_ONLY_RE",
        "_PHONE_INTENT_HINTS", "_expand_phone_intent_queries",
        "_stt_ears_diverge", "_extract_recent_topic", "_build_phone_fallback_queries",
        "_is_non_search_phone_turn",
    })
    hist = [
        {"role": "user", "content": "อธิบายต่อเลย"},
        {"role": "assistant", "content": "ได้เลยครับลุงจืด ระบบตั้งบาร์โค้ดผูกกับหน่วยนับได้ครับ แต่ละบาร์โค้ดกำหนดราคาขายได้สูงสุดสี่ระดับ"},
    ]
    qs_promo = scope["_build_phone_fallback_queries"]("แล้วมีโปรโมชั่นกี่แบบ", [], hist)
    assert all("บาร์โค้ด" not in q for q in qs_promo), f"standalone promotion question must not inherit barcode reply: {qs_promo}"
    assert scope["_is_non_search_phone_turn"]("งั้นคุณบอกให้พนักงานโทรหาผมด้วยแล้วกัน", "", hist) is True


def test_thai_custom_segmentation_and_pgvector_hybrid():
    """Verify pythainlp custom BC dict segmentation + PostgreSQL pgvector HNSW Hybrid RRF + user/dev isolation + fail-soft."""
    from brain_mirror import BrainVaultMirror, segment_thai_text, _tokenize_query
    from brain_pgvector import BrainPgVectorStore

    # 1. Custom BC Thai dictionary keeps domain compounds intact (no bad fragmentation)
    seg = segment_thai_text("ตั้งค่าใบกำกับภาษีอย่างย่อ พักบิลหน้าร้าน สต็อกติดลบ เข้าทีหลังออกก่อน โปรโมชั่น")
    for expected in ("ใบกำกับภาษีอย่างย่อ", "พักบิล", "สต็อกติดลบ", "เข้าทีหลังออกก่อน", "โปรโมชัน"):
        assert expected in seg, f"missing custom Thai token {expected!r} in: {seg}"
    promo_toks = _tokenize_query("ซื้อสองแถมหนึ่งตั้งค่ายังไง", expand_synonyms=False)
    assert "โปรโมชัน" in promo_toks and "ซื้อครบแถม" in promo_toks, f"buy-get normalization failed: {promo_toks}"

    # 2. Fail-soft when pgvector disabled or unreachable (never crashes or blocks phone calls)
    disabled_pg = BrainPgVectorStore(enabled=False)
    assert disabled_pg.hybrid_search("โปรโมชั่น", tokens=["โปรโมชัน"], audience="user")["ok"] is False

    # 3. Live pgvector Hybrid RRF verification when local bccallcenter-pg is running
    brain = BrainVaultMirror(vault_dir=C.LOCAL_BRAIN_DIR, enable_pgvector=True)
    brain.load_from_dir(C.LOCAL_BRAIN_DIR, force=True, sync_pgvector=False)
    pg = brain._get_pg()
    if pg is not None and pg.stats().get("ready"):
        res = brain.search_with_meta("ซื้อสองแถมหนึ่งตั้งค่ายังไง", limit=5, audience="user")
        assert res["hybrid"] is True and res["hits"], f"expected pgvector hybrid hits: {res}"
        assert res["hits"][0]["slug"] in ("howto-pos-hardware-display-promo", "howto-price-center-discount-formula")
        assert all(h["audience"] == "user" for h in res["hits"]), "pgvector user search leaked dev note!"

        # Semantic OOV gate blocks unrelated queries with accidental keyword overlap
        oov_gold = brain.quick_answer("ราคาทองคำแท่งวันนี้บาทละเท่าไหร่ครับ", audience="user")
        oov_weather = brain.quick_answer("อากาศที่เชียงใหม่พรุ่งนี้ฝนจะตกไหม", audience="user")
        assert "🛑 Abstain" in oov_gold, f"gold price must abstain: {oov_gold[:200]}"
        assert "🛑 Abstain" in oov_weather, f"weather must abstain: {oov_weather[:200]}"

        # Strict SQL audience isolation check directly on pgvector store
        pg_user = pg.hybrid_search("ic_trans sql api endpoint", tokens=["ic_trans", "sql", "api"], audience="user", limit=8)
        assert pg_user["ok"] is True and all(h["audience"] == "user" for h in pg_user["hits"]), "pgvector SQL leaked dev chunk!"


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
    test_bcbrain_v230_zone_abstain_and_ultra_mode,
    test_callback_restraint_qa_cache_fast_chat_and_batch,
    test_vault_mirror_intent_expander_planner_and_ice_filter,
    test_log_and_audio_6_improvements,
    test_abstain_nearby_fallback_zero_hallucination,
    test_wok98_log_5_improvements,
    test_embedded_local_bcbrain_no_mcp_api,
    test_voice_first_big_brain_and_zyx59_fixes,
    test_thai_custom_segmentation_and_pgvector_hybrid,
]


def main():
    passed = 0
    for fn in TESTS:
        fn()
        passed += 1
    print(f"{passed}/{len(TESTS)} voice-quality regression cases passed")


if __name__ == "__main__":
    main()
