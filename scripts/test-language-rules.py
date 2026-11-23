#!/usr/bin/env python
"""Regression test for J.A.R.V.I.S's call-language rules: the language changes only on the caller's yes to the
confirm question, misheard Thai never raises that question, and in a Thai call the Thai ears lead.

The rules are lifted out of jarvis/voice_agent.py by name, because importing that module loads the speech models.

Usage: python scripts/test-language-rules.py   -> exit 0 when every case passes
"""
import ast
import re
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / "jarvis" / "voice_agent.py"
RULES = {"CORE_MULTI_LANGS", "LANG_SURE_PROB", "_choose_triple_ear", "_detect_explicit_lang_switch", "_detect_script_lang", "_clearly_spoken_lang",
         "_LANG_YES_START", "_LANG_YES_WHOLE", "_LANG_NO_START", "_is_lang_confirm_yes", "_is_lang_confirm_no",
         "_lang_turn"}


def defined_names(node):
    if isinstance(node, ast.FunctionDef):
        return {node.name}
    if isinstance(node, ast.Assign):
        return {target.id for target in node.targets if isinstance(target, ast.Name)}
    return set()


def load_rules():
    picked = [node for node in ast.parse(SOURCE.read_text(encoding="utf-8")).body if defined_names(node) & RULES]
    missing = RULES - set().union(*map(defined_names, picked))
    if missing:
        sys.exit(f"not found in {SOURCE.name}: {sorted(missing)}")
    scope = {"re": re}
    exec(compile(ast.Module(body=picked, type_ignores=[]), str(SOURCE), "exec"), scope)
    return scope["_lang_turn"], scope["_choose_triple_ear"]


def run_call(lang_turn, lang, turns):
    """The call-state bookkeeping of reply(): the question waits one turn, only switch/adopt change the language."""
    pending, actions = None, []
    for heard, stt_lang, stt_prob in turns:
        action, target = lang_turn(heard, lang, pending, (stt_lang, stt_prob))
        pending = target if action == "ask" else None
        if action in ("switch", "adopt"):
            lang = target
        actions.append(action)
    return actions, lang


ENGLISH = ("Hello, I would like to ask about your accounting software", "en", 0.98)
THAI = ("สวัสดีครับ ขอสอบถามเรื่องโปรแกรมบัญชีหน่อยครับ", "th", 0.98)
CHINESE = ("你好，我想咨询一下你们的会计软件", "zh", 0.97)

# (case, the call's language, turns as (heard, Whisper language, Whisper probability), expected actions, language after)
CASES = [
    # Thai speech misheard as another language, as logged on 2026-11-23: nothing may change and nothing is asked
    ("misheard: one sure English word", "th", [("Okay.", "en", 0.91)], ["keep"], "th"),
    ("misheard: interjection", "th", [("Wow.", "en", 0.97)], ["keep"], "th"),
    ("misheard: trailing word", "th", [("And...", "en", 0.67)], ["keep"], "th"),
    ("misheard: four sure Chinese characters", "th", [("那我们来", "zh", 0.90)], ["keep"], "th"),
    ("misheard: two Chinese characters", "th", [("会论", "zh", 0.84)], ["keep"], "th"),
    ("misheard: unsure English phrase", "th", [("See you later.", "en", 0.50)], ["keep"], "th"),
    ("misheard: unsure English sentence", "th", [("Let me know what we have with hit me", "en", 0.52)], ["keep"], "th"),
    ("misheard: Latin text Whisper calls Vietnamese", "th", [("Hẹn gặp lại các bạn trong video tiếp theo", "vi", 0.59)], ["keep"], "th"),
    ("no Whisper verdict for the turn", "th", [("Thank you for watching the video", None, 0.0)], ["keep"], "th"),
    ("misheard: sure-sounding English sentence", "th", [("He wore it out.", "en", 0.68)], ["keep"], "th"),
    ("just under the confidence bar", "th", [("Thank you for watching the video", "en", 0.79)], ["keep"], "th"),
    ("at the confidence bar", "th", [("Thank you for watching the video", "en", 0.80)], ["ask"], "th"),
    # the question is answered on the next turn or dropped
    ("yes switches", "th", [ENGLISH, ("Yes", "en", 0.95)], ["ask", "switch"], "en"),
    ("a Thai sentence is not a yes", "th", [ENGLISH, ("ครับ ตอนนี้คุณมีกี่ระบบ", "th", 0.95), ("See you later.", "en", 0.50)], ["ask", "keep", "keep"], "th"),
    ("a misheard phrase in the asked language is not a yes", "th", [ENGLISH, ("See you later.", "en", 0.50)], ["ask", "keep"], "th"),
    ("a yes two turns later is not an answer", "th", [ENGLISH, ("อ่า", "th", 0.50), ("ครับ", "th", 0.90)], ["ask", "keep", "keep"], "th"),
    ("no keeps the language", "th", [ENGLISH, ("ไม่ครับ", "th", 0.90)], ["ask", "declined"], "th"),
    ("no in the asked language", "th", [ENGLISH, ("No English", "en", 0.80)], ["ask", "declined"], "th"),
    ("no in Chinese", "th", [CHINESE, ("不用", "zh", 0.90)], ["ask", "declined"], "th"),
    ("answering in the asked language switches", "en", [THAI, ("อยากทราบราคาโปรแกรมครับ", "th", 0.97)], ["ask", "switch"], "th"),
    ("naming the asked language switches", "th", [ENGLISH, ("English please", "en", 0.90)], ["ask", "switch"], "en"),
    ("Chinese caller", "th", [CHINESE, ("好的", "zh", 0.90)], ["ask", "switch"], "zh"),
    # a request for a language is confirmed too: a keyword cannot tell a request from a complaint
    ("request in Thai", "th", [("พูดภาษาอังกฤษได้ไหม", "th", 0.97), ("ได้ครับ", "th", 0.90)], ["ask", "switch"], "en"),
    ("complaint that names a language", "th", [("ทำไมพูดภาษาอังกฤษ", "th", 0.95), ("ไม่เอา", "th", 0.90)], ["ask", "declined"], "th"),
    # the language holds until the next confirmed change
    ("same language, nothing to ask", "en", [("How much is it for five users", "en", 0.97), ("ครับ", "th", 0.50)], ["keep", "keep"], "en"),
    ("changing back", "en", [("ขอเปลี่ยนเป็นภาษาไทยครับ", "th", 0.97), ("ใช่", "th", 0.90)], ["ask", "switch"], "th"),
    ("clear Thai in an English call", "en", [THAI, ("ครับ", "th", 0.80)], ["ask", "switch"], "th"),
    # a call the operator set to auto takes the first clear language
    ("auto call adopts", None, [ENGLISH], ["adopt"], "en"),
    ("auto call ignores a mishearing", None, [("Okay.", "en", 0.91)], ["keep"], None),
]
# (case, (Whisper, OpenThai th-TH, OpenThai th-mix), the call's language, the ear that must lead), as logged on 2026-11-23
EAR_CASES = [
    ("Thai call: Whisper garbles Thai", ("มีรับอบคา สินค้า ที่ต้อง", "มีระบบค่าเสื่อมราคาหรือเปล่า", "มีระบบค่าเสื่อมราคาหรือเปล่า"), "th", "มีระบบค่าเสื่อมราคาหรือเปล่า"),
    ("Thai call: Whisper sure but wrong", ("มีผ่านมันทีตัวอย่างไม่ ไม่อยากที", "มีแผนบัญชีตัวอย่างไหม", "มีแผนบัญชีตัวอย่างไหม"), "th", "มีแผนบัญชีตัวอย่างไหม"),
    ("Thai call: Whisper hears Vietnamese", ("Là khá phố kèm, là khá phố kèm.", "ราคาโปรแกรมราคาโปรแกรม", "ราคา program ราคาโปรแกรม"), "th", "ราคา program ราคาโปรแกรม"),
    ("Thai call: Whisper timed out", ("", "ดูอาคารดีดี", "ดูอาคารดีดี"), "th", "ดูอาคารดีดี"),
    ("Thai call: only Whisper heard anything", ("you", "", ""), "th", "you"),
    ("Thai call: th-mix kept the language request", ("ขอสปีกอิงลิช", "ขอสปีกอิงลิช", "ขอ speak English"), "th", "ขอ speak English"),
    ("English call: Whisper leads", ("How much is it", "ฮาวมัชอีสอิท", "ฮาวมัชอีสอิท"), "en", "How much is it"),
]
YES = ["ครับ", "ใช่ครับ", "ตกลงครับ", "ได้เลยครับ", "โอเคครับ", "ดีครับ", "Yes please", "Okay.", "Sure"]
NOT_YES = ["ได้ยินไหม", "ใช่ไหมครับ", "ดีจังเลย", "ครับ ขอถามหน่อย", "ไม่ทราบว่าราคาเท่าไหร่ครับ", "Nothing", "Yesterday"]
NO = ["ไม่", "ไม่ใช่", "ไม่ต้องครับ", "อย่าเปลี่ยน", "No", "Not now", "Don't"]


def answer(phrase):
    """Whisper is unsure about one-word replies in real calls, so the reply itself has to carry the answer."""
    return (phrase, "th" if re.search("[ก-๙]", phrase) else "en", 0.60)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    lang_turn, choose_ear = load_rules()
    cases = list(CASES)
    cases += [(f"yes: {said}", "th", [ENGLISH, answer(said)], ["ask", "switch"], "en") for said in YES]
    cases += [(f"not a yes: {said}", "th", [ENGLISH, answer(said)], ["ask", "keep"], "th") for said in NOT_YES]
    cases += [(f"no: {said}", "th", [ENGLISH, answer(said)], ["ask", "declined"], "th") for said in NO]
    failed = 0
    for name, lang, turns, want_actions, want_lang in cases:
        got = run_call(lang_turn, lang, turns)
        if got != (want_actions, want_lang):
            failed += 1
            print(f"FAIL {name}: got {got}, want {(want_actions, want_lang)}")
    for name, ears, lang, want in EAR_CASES:
        got = choose_ear(*ears, lang)[0]
        if got != want:
            failed += 1
            print(f"FAIL {name}: picked {got!r}, want {want!r}")
    total = len(cases) + len(EAR_CASES)
    print(f"{total - failed}/{total} language-rule cases passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
