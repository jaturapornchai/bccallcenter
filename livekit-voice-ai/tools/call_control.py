"""Call Center Tools: SIP REFER Transfer, Auto Hangup, Caller History, Knowledge Search & Ticket."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from livekit import api
from livekit.agents import function_tool
from livekit.protocol import room as proto_room
from livekit.protocol import sip as proto_sip

logger = logging.getLogger("bc-callcenter.tools")


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"[^\d+]", "", str(raw or "").strip())
    if digits.startswith("+66"):
        return "0" + digits[3:]
    if digits.startswith("66") and len(digits) >= 10:
        return "0" + digits[2:]
    return digits


def _memory_path() -> Path:
    return Path(os.getenv("CALL_MEMORY_PATH", "../data/call-memory.json")).resolve()


def _tickets_path() -> Path:
    return Path(os.getenv("TICKETS_PATH", "../data/tickets.json")).resolve()


def _crm_db_path() -> Path:
    return Path(os.getenv("CRM_DB_PATH", "../data/crm-db.json")).resolve()


def load_caller_language(phone: str) -> str | None:
    """โหลดภาษาประจำของลูกค้าจาก crm-db.json (None หากเป็นเบอร์ใหม่)."""
    key = normalize_phone(phone)
    p = _crm_db_path()
    if not key or not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        cust = (data.get("customers") or {}).get(key)
        lang = cust.get("preferredLanguage") if isinstance(cust, dict) else None
        return str(lang) if lang else None
    except Exception:
        return None


def save_caller_language(phone: str, language: str | None) -> None:
    """บันทึกภาษาที่ลูกค้าเลือก/เปลี่ยนกลางสายลง crm-db.json เพื่อใช้ต่อเนื่องในสายถัดไป."""
    key = normalize_phone(phone)
    if not key:
        return
    p = _crm_db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"customers": {}, "tickets": []}
    except Exception:
        data = {"customers": {}, "tickets": []}
    customers = data.setdefault("customers", {})
    now = datetime.now(timezone.utc).isoformat()
    cust = customers.get(key) or {
        "phone": key,
        "name": "",
        "company": "",
        "tier": "new",
        "stage": "lead",
        "preferredLanguage": None,
        "tags": [],
        "notes": "",
        "avgCsat": None,
        "csatCount": 0,
        "lastSentiment": None,
        "totalCalls": 1,
        "createdAt": now,
        "updatedAt": now,
        "lastCallAt": now,
    }
    cust["preferredLanguage"] = language or None
    cust["updatedAt"] = now
    customers[key] = cust
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_caller_summary(phone: str) -> str | None:
    """โหลดประวัติการคุยเดิมของเบอร์ที่โทรเข้ามาจากไฟล์ call-memory.json ที่ใช้ร่วมกับเว็บ BC Call Center."""
    key = normalize_phone(phone)
    p = _memory_path()
    if not key or not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        rec = (data.get("callers") or {}).get(key)
        if not rec or not rec.get("sessions"):
            return None
        latest = rec["sessions"][0]
        analysis = latest.get("analysis") or {}
        if analysis.get("summary"):
            return (
                f"เคยโทรมาแล้ว {rec.get('totalCalls', 1)} ครั้ง "
                f"เรื่องล่าสุด: {analysis.get('topic', '-')} — {analysis['summary']}"
            )
        msgs = latest.get("messages") or []
        if not msgs:
            return None
        excerpt = " | ".join(
            f"{'ลูกค้า' if m.get('role') == 'caller' else 'J.A.R.V.I.S'}: {m.get('text', '')}"
            for m in msgs[-4:]
        )
        return f"เคยโทรมาแล้ว {rec.get('totalCalls', 1)} ครั้ง บทสนทนาก่อนหน้า: {excerpt}"
    except Exception as exc:
        logger.warning("Failed to load caller history: %s", exc)
        return None


def save_call_transcript(
    phone: str,
    messages: list[dict[str, str]],
    duration_sec: int,
    analysis: dict[str, Any] | None = None,
) -> None:
    """บันทึกประวัติการคุยและผลวิเคราะห์ QA ลงไฟล์ call-memory.json."""
    key = normalize_phone(phone) or "unknown"
    p = _memory_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"callers": {}}
    except Exception:
        data = {"callers": {}}

    callers = data.setdefault("callers", {})
    now = datetime.now(timezone.utc).isoformat()
    rec = callers.get(key) or {
        "phone": key,
        "totalCalls": 0,
        "lastCallAt": now,
        "lastDirection": "inbound",
        "sessions": [],
    }
    rec["totalCalls"] = int(rec.get("totalCalls", 0)) + 1
    rec["lastCallAt"] = now
    rec["sessions"].insert(
        0,
        {
            "id": f"lk-{int(datetime.now(timezone.utc).timestamp())}",
            "startedAt": now,
            "endedAt": now,
            "durationSec": duration_sec,
            "status": "answered",
            "direction": "inbound",
            "messages": messages,
            "analysisStatus": "done" if analysis and not analysis.get("error") else "idle",
            "analysis": analysis,
        },
    )
    rec["sessions"] = rec["sessions"][:30]
    callers[key] = rec
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


@dataclass
class CallCenterContext:
    room_name: str
    sip_participant_identity: str
    caller_phone: str
    livekit_api: api.LiveKitAPI
    language: str | None = None
    on_language_change: Any = None
    transcript: list[dict[str, str]] = field(default_factory=list)
    transfer_targets: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.transfer_targets:
            self.transfer_targets = {
                "operator": os.getenv("TRANSFER_OPERATOR_URI", "sip:100@asterisk-registrar:5060"),
                "sales": os.getenv("TRANSFER_SALES_URI", "sip:101@asterisk-registrar:5060"),
                "support": os.getenv("TRANSFER_SUPPORT_URI", "sip:102@asterisk-registrar:5060"),
            }

    async def execute_transfer(self, department: str) -> str:
        target = self.transfer_targets.get(department.lower(), self.transfer_targets["operator"])
        req = proto_sip.TransferSIPParticipantRequest(
            participant_identity=self.sip_participant_identity,
            room_name=self.room_name,
            transfer_to=target,
            play_dialtone=True,
        )
        await self.livekit_api.sip.transfer_sip_participant(req)
        return target

    async def execute_hangup(self, delay_sec: float = 1.5) -> None:
        await asyncio.sleep(delay_sec)
        await self.livekit_api.room.delete_room(
            proto_room.DeleteRoomRequest(room=self.room_name)
        )


def build_call_tools(ctx: CallCenterContext) -> list[Any]:
    """สร้างชุดเครื่องมือ Call Center สำหรับ LiveKit AgentSession."""

    @function_tool()
    async def lookup_caller_history() -> str:
        """ตรวจสอบประวัติการสนทนาก่อนหน้าของเบอร์โทรศัพท์ที่กำลังโทรเข้ามา."""
        summary = load_caller_summary(ctx.caller_phone)
        return summary or "ลูกค้าโทรมาเป็นครั้งแรก ยังไม่มีประวัติเดิมในระบบ"

    @function_tool()
    async def search_knowledge_base(query: str) -> str:
        """ค้นหาข้อมูลสินค้า ราคา วิธีใช้งานโปรแกรมบัญชี BC Ai Account จากคลังความรู้."""
        mcp_url = os.getenv("BC_BRAIN_MCP_URL", "https://brain.bcsmethai.com/mcp")
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                res = await client.post(
                    mcp_url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": "search_notes", "arguments": {"query": query, "limit": 3}},
                    },
                )
                if res.status_code == 200:
                    body = res.json()
                    content = body.get("result", {}).get("content", [])
                    texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("text")]
                    if texts:
                        return "\n".join(texts)[:1200]
        except Exception as exc:
            logger.warning("Knowledge search failed: %s", exc)
        return "ไม่สามารถดึงข้อมูลจากคลังความรู้ได้ชั่วคราว กรุณาแจ้งลูกค้าว่าสามารถฝากเรื่องหรือโอนสายให้เจ้าหน้าที่ช่วยดูแลได้ครับ"

    @function_tool()
    async def create_callback_ticket(
        customer_name: str,
        company_name: str = "",
        callback_phone: str = "",
        issue_summary: str = "",
        urgency: str = "normal",
    ) -> str:
        """บันทึกข้อมูลฝากเรื่องติดต่อกลับเมื่อลูกค้าต้องการให้เจ้าหน้าที่โทรกลับหรือเปิดเคส."""
        phone = normalize_phone(callback_phone or ctx.caller_phone)
        ticket_id = f"BC-{datetime.now().strftime('%y%m%d-%H%M%S')}"
        record = {
            "ticketId": ticket_id,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "customerName": customer_name.strip(),
            "companyName": company_name.strip(),
            "phone": phone,
            "issueSummary": issue_summary.strip(),
            "urgency": urgency,
        }
        p = _tickets_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            items = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        except Exception:
            items = []
        items.insert(0, record)
        p.write_text(json.dumps(items[:200], ensure_ascii=False, indent=2), encoding="utf-8")
        return f"บันทึกเคสหมายเลข {ticket_id} เรียบร้อยแล้ว แจ้งลูกค้าว่าเจ้าหน้าที่จะรีบติดต่อกลับที่เบอร์ {phone}"

    @function_tool()
    async def transfer_to_human(department: str = "operator") -> str:
        """โอนสาย (SIP REFER) ไปยังเจ้าหน้าที่มนุษย์ แผนกที่รองรับ: operator, sales, support."""
        dept = department.lower() if department.lower() in ("operator", "sales", "support") else "operator"
        try:
            await ctx.execute_transfer(dept)
            return f"เริ่มโอนสายไปยังแผนก {dept} แล้ว"
        except Exception as exc:
            logger.error("SIP REFER transfer failed: %s", exc)
            return "โอนสายไม่สำเร็จในขณะนี้ กรุณาขอฝากชื่อและเบอร์ติดต่อกลับผ่าน create_callback_ticket แทน"

    @function_tool()
    async def set_conversation_language(language: str) -> str:
        """ตั้งค่าหรือเปลี่ยนภาษาสนทนากลางสาย (รหัส ISO: th, en, zh, ja, ko, lo, my, vi, km) และบันทึกลง CRM."""
        code = str(language or "").strip().lower()[:2]
        if code not in {"th", "en", "zh", "ja", "ko", "lo", "my", "vi", "km"}:
            return "ไม่รองรับรหัสภาษานี้ กรุณาใช้ th, en, zh, ja, ko, lo, my, vi หรือ km"
        ctx.language = code
        save_caller_language(ctx.caller_phone, code)
        if callable(ctx.on_language_change):
            ctx.on_language_change(code)
        return f"สลับภาษาการฟังและการตอบเป็น {code} และบันทึกลง CRM เรียบร้อยแล้ว ให้ตอบลูกค้าเป็นภาษา {code} ต่อทันที"

    @function_tool()
    async def hangup_call() -> str:
        """วางสายโทรศัพท์อัตโนมัติอย่างสุภาพหลังกล่าวลาลูกค้าเรียบร้อยแล้ว."""
        asyncio.create_task(ctx.execute_hangup(delay_sec=1.8))
        return "กำลังวางสายอย่างสุภาพภายใน 2 วินาที"

    return [
        lookup_caller_history,
        search_knowledge_base,
        create_callback_ticket,
        set_conversation_language,
        transfer_to_human,
        hangup_call,
    ]
