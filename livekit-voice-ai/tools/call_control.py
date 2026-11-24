"""Call Center Tools: SIP REFER Transfer, Auto Hangup, Caller History, Knowledge Search & Ticket."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from livekit import api
from livekit.agents import function_tool
from livekit.protocol import room as proto_room
from livekit.protocol import sip as proto_sip

logger = logging.getLogger("bc-callcenter.tools")

_MCP_HTTP_CLIENT: httpx.AsyncClient | None = None
_MCP_CACHE: dict[str, tuple[float, str]] = {}
_MCP_CACHE_TTL_S = 300.0
_MCP_CACHE_MAX = 256
_FAST_REST_ETAG: dict[str, tuple[str, str]] = {}


def _get_mcp_http_client() -> httpx.AsyncClient:
    global _MCP_HTTP_CLIENT
    if _MCP_HTTP_CLIENT is None or _MCP_HTTP_CLIENT.is_closed:
        _MCP_HTTP_CLIENT = httpx.AsyncClient(
            timeout=8.0,
            limits=httpx.Limits(max_keepalive_connections=8, keepalive_expiry=120.0),
        )
    return _MCP_HTTP_CLIENT


def _cache_get(key: str) -> str | None:
    hit = _MCP_CACHE.get(key)
    if hit and (time.monotonic() - hit[0]) < _MCP_CACHE_TTL_S:
        return hit[1]
    return None


def _cache_put(key: str, value: str) -> None:
    if not value:
        return
    if len(_MCP_CACHE) >= _MCP_CACHE_MAX:
        _MCP_CACHE.pop(next(iter(_MCP_CACHE)), None)
    _MCP_CACHE[key] = (time.monotonic(), value)


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
    mcp_url = os.getenv("BC_BRAIN_MCP_URL", "https://brain.bcsmethai.com/mcp")

    @function_tool()
    async def lookup_caller_history() -> str:
        """ตรวจสอบประวัติการสนทนาก่อนหน้าของเบอร์โทรศัพท์ที่กำลังโทรเข้ามา."""
        summary = load_caller_summary(ctx.caller_phone)
        return summary or "ลูกค้าโทรมาเป็นครั้งแรก ยังไม่มีประวัติเดิมในระบบ"

    async def _fast_rest_fallback(tool_name: str, arguments: dict[str, Any]) -> str | None:
        base_url = mcp_url.removesuffix("/mcp").rstrip("/")
        if tool_name == "quick_answer":
            qs = urlencode({
                "q": str(arguments.get("question") or ""),
                "audience": str(arguments.get("audience") or "user"),
                "mode": str(arguments.get("mode") or "ultra"),
            })
        elif tool_name == "lookup_menu":
            qs = urlencode({
                "menu": str(arguments.get("query") or ""),
                "audience": str(arguments.get("audience") or "user"),
            })
        else:
            return None
        rest_url = f"{base_url}/api/fast?{qs}"
        headers = {"Accept": "text/plain, application/json"}
        cached_etag, cached_body = _FAST_REST_ETAG.get(rest_url, ("", ""))
        if cached_etag:
            headers["If-None-Match"] = cached_etag
        try:
            client = _get_mcp_http_client()
            res = await client.get(rest_url, headers=headers)
            if res.status_code == 304 and cached_body:
                return cached_body
            if res.status_code == 200 and res.text.strip():
                body_txt = res.text.strip()[:2200]
                if etag := res.headers.get("etag"):
                    if len(_FAST_REST_ETAG) >= 128:
                        _FAST_REST_ETAG.pop(next(iter(_FAST_REST_ETAG)), None)
                    _FAST_REST_ETAG[rest_url] = (etag, body_txt)
                return body_txt
        except Exception as rest_exc:
            logger.warning("BC Brain /api/fast fallback %s failed: %s", tool_name, rest_exc)
        return None

    async def _call_bc_brain_mcp(tool_name: str, arguments: dict[str, Any], timeout: float = 8.0) -> str:
        cache_key = f"{tool_name}:{json.dumps(arguments, ensure_ascii=False, sort_keys=True)}"
        if cached := _cache_get(cache_key):
            return cached
        try:
            client = _get_mcp_http_client()
            res = await client.post(
                mcp_url,
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": arguments},
                },
                timeout=timeout,
            )
            if res.status_code == 200:
                raw = res.text
                if "event-stream" in res.headers.get("content-type", ""):
                    data_lines = [ln[5:] for ln in raw.splitlines() if ln.startswith("data:")]
                    raw = data_lines[-1] if data_lines else raw
                body = json.loads(raw)
                content = body.get("result", {}).get("content", [])
                texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("text")]
                if texts:
                    joined = "\n".join(texts)
                    if "🛑" in joined or "abstain" in joined.lower():
                        return "ไม่พบข้อมูลยืนยันในระบบ (Abstain): ห้ามเดาหรือแต่งข้อมูลเอง ให้แจ้งลูกค้าตรงๆ ว่ายังไม่มีข้อมูลยืนยันในระบบ แล้วชวนคุยตอบเรื่องอื่นต่ออย่างเป็นธรรมชาติ (ไม่ต้องเสนอโทรกลับทุกครั้ง เสนอเฉพาะเมื่อลูกค้าขอ จำเป็นจริงๆ หรือเป็น BC Account เวอร์ชันเก่า)"
                    limit_chars = 2400 if tool_name == "batch_read" else 1400
                    out = joined[:limit_chars]
                    _cache_put(cache_key, out)
                    return out
        except Exception as exc:
            logger.warning("BC Brain MCP %s failed: %s", tool_name, exc)
        if rest_txt := await _fast_rest_fallback(tool_name, arguments):
            if "🛑" in rest_txt or "abstain" in rest_txt.lower():
                return "ไม่พบข้อมูลยืนยันในระบบ (Abstain): ห้ามเดาหรือแต่งข้อมูลเอง ให้แจ้งลูกค้าตรงๆ ว่ายังไม่มีข้อมูลยืนยันในระบบ แล้วชวนคุยต่ออย่างเป็นธรรมชาติ"
            _cache_put(cache_key, rest_txt)
            return rest_txt
        return "ไม่สามารถดึงข้อมูลจากคลังความรู้ได้ชั่วคราว กรุณาแจ้งลูกค้าตามจริงและชวนคุยต่อ หรือให้ฝากเรื่องเฉพาะหากจำเป็นครับ"

    @function_tool()
    async def search_knowledge_base(query: str) -> str:
        """ค้นหาข้อมูลสินค้า ราคา วิธีใช้งานโปรแกรมบัญชี BC Ai Account จากคลังความรู้ (quick_answer ultra / audience user)."""
        return await _call_bc_brain_mcp(
            "quick_answer",
            {"question": query, "audience": "user", "mode": "ultra", "include_media": False},
        )

    @function_tool()
    async def lookup_menu(query: str) -> str:
        """ค้นหารหัสเมนู 4 หลัก (1001-9070) หรือชื่อเมนูของ BC Ai Account แบบทันที."""
        return await _call_bc_brain_mcp(
            "lookup_menu",
            {"query": query, "audience": "user", "limit": 5},
        )

    @function_tool()
    async def batch_ask_knowledge(questions: list[str]) -> str:
        """ตอบคำถามหลายประเด็นพร้อมกันในรอบเดียว (สูงสุด 5 ข้อ) จาก BC Brain."""
        clean_qs = [str(q).strip() for q in (questions or []) if str(q).strip()][:5]
        if not clean_qs:
            return "กรุณาระบุคำถามอย่างน้อย 1 ข้อ"
        return await _call_bc_brain_mcp(
            "batch_ask",
            {"questions": clean_qs, "audience": "user", "mode": "ultra", "include_media": False},
        )

    @function_tool()
    async def batch_read_knowledge(slugs: list[str], query: str = "") -> str:
        """อ่านรายละเอียดโน้ต 2-5 ใบพร้อมกันในรอบเดียวตาม slug ของ BC Brain (โซน user)."""
        clean_slugs = [str(s).strip() for s in (slugs or []) if str(s).strip()][:5]
        if not clean_slugs:
            return "กรุณาระบุ slug อย่างน้อย 1 รายการ"
        args: dict[str, Any] = {
            "slugs": clean_slugs,
            "audience": "user",
            "max_chars_per_note": 2000,
        }
        if query and query.strip():
            args["query"] = query.strip()
        return await _call_bc_brain_mcp("batch_read", args)

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
        lookup_menu,
        batch_ask_knowledge,
        batch_read_knowledge,
        create_callback_ticket,
        set_conversation_language,
        transfer_to_human,
        hangup_call,
    ]
