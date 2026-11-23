"""Full-Duplex Thai Voice AI Call Center Agent (Inbound-Only) บน LiveKit Agents v1.x."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
from livekit import api, rtc
from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins import silero

from llm import build_callcenter_system_prompt, create_thai_llm
from stt import ThaiWhisperSTT
from tools import (
    CallCenterContext,
    build_call_tools,
    load_caller_language,
    load_caller_summary,
    save_call_transcript,
)
from tts import ThaiStreamingTTS

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("bc-callcenter.agent")

# DTMF Fast-Path: กดปุ่มระหว่างสายเพื่อโอนหาเจ้าหน้าที่ทันที
DTMF_TRANSFER_MAP = {
    "0": ("operator", "กำลังโอนสายให้เจ้าหน้าที่โอเปอเรเตอร์สักครู่นะครับ"),
    "1": ("sales", "กำลังโอนสายให้ฝ่ายขายสักครู่นะครับ"),
    "2": ("support", "กำลังโอนสายให้ฝ่ายดูแลลูกค้าและบัญชีสักครู่นะครับ"),
}


async def _run_post_call_qa(phone: str, messages: list[dict[str, str]], duration_sec: int) -> dict | None:
    """ส่งบทสนทนาหลังวางสายวิเคราะห์คะแนน CSAT, อารมณ์ลูกค้า และวิธีแก้ไขผ่าน Ollama Cloud DeepSeek."""
    api_key = os.getenv("LLM_API_KEY") or os.getenv("OLLAMA_API_KEY", "")
    base_url = os.getenv("LLM_BASE_URL", "https://ollama.com/v1").rstrip("/")
    model = os.getenv("LLM_MODEL", "deepseek-v4.1-flash")
    if not api_key or not messages:
        return None

    transcript_text = "\n".join(
        f"{'ลูกค้า' if m['role'] == 'caller' else 'J.A.R.V.I.S'}: {m['text']}"
        for m in messages
    )
    prompt = (
        'วิเคราะห์บทสนทนา Call Center ภาษาไทยแล้วตอบเป็น JSON object เท่านั้น: '
        '{"csatScore":1-10,"aiQualityScore":1-10,"sentiment":"positive|neutral|negative|urgent",'
        '"emotionLabel":"...","emotionTrend":"...","topic":"...","summary":"...",'
        '"resolved":true|false,"resolutionSteps":"...","needsHumanFollowUp":true|false,'
        '"urgency":"low|medium|high","followUpAction":"...","aiCoachingTip":"..."}\n\n'
        f"เบอร์โทร: {phone} | เวลา: {duration_sec} วินาที\nบทสนทนา:\n{transcript_text}"
    )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": "คุณคือผู้เชี่ยวชาญ QA สำหรับ Call Center ภาษาไทย ตอบ JSON เท่านั้น"},
                        {"role": "user", "content": prompt},
                    ],
                },
            )
            res.raise_for_status()
            content = res.json()["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            parsed["analyzedAt"] = datetime.now(timezone.utc).isoformat()
            parsed["model"] = model
            return parsed
    except Exception as exc:
        logger.warning("Post-call QA analysis failed: %s", exc)
        return None


def _extract_caller_phone(participant: rtc.RemoteParticipant) -> str:
    attrs = participant.attributes or {}
    return (
        attrs.get("sip.phoneNumber")
        or attrs.get("sip.callFrom")
        or participant.identity.replace("sip_", "")
    )


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()
    started_at = time.monotonic()

    # รอสาย SIP ขาเข้าเชื่อมต่อเข้ามาในห้อง
    sip_participant = await ctx.wait_for_participant()
    caller_phone = _extract_caller_phone(sip_participant)
    prior_summary = load_caller_summary(caller_phone)
    saved_language = load_caller_language(caller_phone)
    is_new_caller = saved_language is None
    active_language = saved_language or "th"
    logger.info(
        "Inbound call connected: room=%s participant=%s caller=%s lang=%s has_history=%s",
        ctx.room.name,
        sip_participant.identity,
        caller_phone,
        f"{active_language} (new)" if is_new_caller else active_language,
        bool(prior_summary),
    )

    lk_api = api.LiveKitAPI()
    stt_engine = ThaiWhisperSTT(language=active_language)
    call_ctx = CallCenterContext(
        room_name=ctx.room.name,
        sip_participant_identity=sip_participant.identity,
        caller_phone=caller_phone,
        livekit_api=lk_api,
        language=active_language,
        on_language_change=stt_engine.set_language,
    )

    instructions = build_callcenter_system_prompt(
        caller_phone,
        prior_summary,
        preferred_language=active_language,
        is_new_caller=is_new_caller,
    )
    tools = build_call_tools(call_ctx)

    agent = Agent(instructions=instructions, tools=tools)

    session = AgentSession(
        vad=silero.VAD.load(
            min_speech_duration=float(os.getenv("BARGE_MIN_SPEECH_S", "0.25")),
            min_silence_duration=float(os.getenv("ENDPOINT_MIN_DELAY_S", "0.45")),
        ),
        stt=stt_engine,
        llm=create_thai_llm(),
        tts=ThaiStreamingTTS(),
        # True Full-Duplex & Fast Barge-in
        allow_interruptions=True,
        min_interruption_duration=float(os.getenv("BARGE_MIN_SPEECH_S", "0.25")),
        min_endpointing_delay=float(os.getenv("ENDPOINT_MIN_DELAY_S", "0.45")),
        max_endpointing_delay=float(os.getenv("ENDPOINT_MAX_DELAY_S", "1.80")),
    )

    # เก็บบทสนทนาแบบเรียลไทม์สำหรับความจำต่อเนื่องและ Post-Call QA
    @session.on("conversation_item_added")
    def on_item(ev) -> None:
        item = getattr(ev, "item", None)
        if not item:
            return
        role = "caller" if getattr(item, "role", "") == "user" else "ai"
        text = getattr(item, "text_content", "") or ""
        if text.strip():
            call_ctx.transcript.append(
                {
                    "at": datetime.now(timezone.utc).isoformat(),
                    "role": role,
                    "text": text.strip(),
                }
            )

    # รับค่าปุ่มกดโทรศัพท์ (DTMF Fast Path) ระหว่างสาย
    @ctx.room.on("sip_dtmf_received")
    def on_sip_dtmf(dtmf: rtc.SipDTMF) -> None:
        digit = str(dtmf.digit)
        logger.info("DTMF received: digit=%s from=%s", digit, dtmf.participant.identity)
        if digit in DTMF_TRANSFER_MAP:
            dept, phrase = DTMF_TRANSFER_MAP[digit]

            async def _handle_dtmf_transfer() -> None:
                await session.say(phrase, allow_interruptions=False)
                await call_ctx.execute_transfer(dept)

            asyncio.create_task(_handle_dtmf_transfer())

    # Hook เมื่อวางสาย: บันทึกประวัติการคุยและรัน Post-Call QA อัตโนมัติ
    async def on_shutdown() -> None:
        duration_sec = max(0, int(time.monotonic() - started_at))
        if call_ctx.transcript:
            analysis = await _run_post_call_qa(caller_phone, call_ctx.transcript, duration_sec)
            save_call_transcript(caller_phone, call_ctx.transcript, duration_sec, analysis)
            logger.info("Saved transcript (%d turns) and QA for %s", len(call_ctx.transcript), caller_phone)
        await lk_api.aclose()

    ctx.add_shutdown_callback(on_shutdown)

    await session.start(room=ctx.room, agent=agent)

    # ทักทายรับสาย: เบอร์ใหม่ default ภาษาไทยพร้อมบอกว่าคุยได้ทุกภาษา; เบอร์เดิมใช้ภาษาที่บันทึกไว้
    if is_new_caller:
        await session.say(
            "สวัสดีครับ ระบบ บีซี คอลเซ็นเตอร์ เจาร์วิส ยินดีให้บริการครับ สามารถคุยได้ทุกภาษาเลยนะครับ วันนี้ให้ช่วยดูแลเรื่องอะไรดีครับ",
            allow_interruptions=True,
        )
    elif prior_summary or active_language != "th":
        await session.generate_reply(
            instructions=f"ทักทายลูกค้าเดิมเป็นภาษา {active_language} อย่างอบอุ่น แจ้งชื่อ J.A.R.V.I.S จาก BC Call Center และถามต่อเนื่องจากเรื่องเดิมสั้นกระชับ 1–2 ประโยค"
        )
    else:
        await session.say(
            "สวัสดีครับ ระบบ บีซี คอลเซ็นเตอร์ เจาร์วิส ยินดีให้บริการครับ สามารถคุยได้ทุกภาษาเลยนะครับ วันนี้ให้ช่วยดูแลเรื่องอะไรดีครับ",
            allow_interruptions=True,
        )


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name="jarvis-callcenter",
        )
    )
