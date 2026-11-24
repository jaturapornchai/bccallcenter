import fs from "node:fs";
import path from "node:path";
import { normalizePhone } from "./call-memory.mjs";

const VALID_TIERS = new Set(["new", "regular", "vip", "at_risk"]);
const VALID_STAGES = new Set(["lead", "prospect", "customer", "churn_risk"]);
const VALID_TICKET_STATUS = new Set(["open", "in_progress", "waiting_customer", "resolved", "closed"]);
const VALID_PRIORITIES = new Set(["low", "medium", "high", "urgent"]);
const VALID_SOURCES = new Set(["ai_callback", "ai_auto", "manual"]);
const PRIORITY_RANK = { low: 1, medium: 2, high: 3, urgent: 4 };
export const VALID_LANGUAGES = new Set(["th", "en", "zh", "ja", "ko", "lo", "my", "vi", "km"]);

export function isActiveTicket(t) {
  return Boolean(t && t.status !== "closed" && t.status !== "resolved");
}

function ticketPhoneSet(t) {
  const out = new Set();
  if (!t) return out;
  for (const raw of [t.phone, t.callbackPhone, t.callerPhone]) {
    const k = normalizePhone(raw);
    if (k) out.add(k);
  }
  return out;
}

export function cleanTopicText(raw) {
  let s = String(raw || "").replace(/\r?\n+/g, " ").trim();
  for (let i = 0; i < 3; i++) {
    const next = s
      .replace(/^(?:ขอให้โทรกลับ(?:\s*\([^)]*\))?\s*[:：\-]?\s*|เรื่องที่ให้โทรกลับ\s*[:：\-]?\s*|สรุป(?:การสนทนา)?\s*[:：\-]?\s*)+/i, "")
      .trim();
    if (next === s) break;
    s = next;
  }
  return s.replace(/\s{2,}/g, " ").slice(0, 500);
}

function mergeTopicsList(existingList = [], incomingList = [], maxItems = 12) {
  const list = [];
  const addOne = (raw) => {
    const cleaned = cleanTopicText(raw);
    if (!cleaned || cleaned === "ลูกค้าขอให้พนักงานโทรกลับ" || cleaned === "ลูกค้าขอให้โทรกลับ") return;
    const norm = cleaned.replace(/[\s.,:;\-•]/g, "").toLowerCase();
    if (!norm) return;
    for (let i = 0; i < list.length; i++) {
      const exNorm = list[i].replace(/[\s.,:;\-•]/g, "").toLowerCase();
      if (exNorm === norm || exNorm.includes(norm)) return;
      if (norm.includes(exNorm)) {
        list[i] = cleaned;
        return;
      }
    }
    list.push(cleaned);
  };
  for (const item of existingList || []) addOne(item);
  for (const item of incomingList || []) addOne(item);
  return list.length > maxItems ? list.slice(-maxItems) : list;
}

function extractTopicsFromTicket(t) {
  if (!t) return [];
  const extracted = [];
  if (Array.isArray(t.topics) && t.topics.length > 0) {
    extracted.push(...t.topics);
  } else {
    const lines = String(t.description || "").split(/\r?\n/);
    for (const line of lines) {
      const mNum = line.match(/^\d+\.\s+(.+)/);
      const mCb = line.match(/^เรื่องที่ให้โทรกลับ\s*[:：]\s*(.+)/);
      if (mNum) extracted.push(mNum[1]);
      else if (mCb) extracted.push(mCb[1]);
    }
    if (extracted.length === 0 && t.subject) {
      extracted.push(t.subject);
    }
    if (extracted.length === 0) {
      for (const line of lines) {
        const mQa = line.match(/^สรุป(?:การสนทนา)?\s*[:：]\s*(.+)/);
        if (mQa) extracted.push(mQa[1]);
      }
    }
  }
  return mergeTopicsList([], extracted);
}

function extractExtraDescriptionLines(t) {
  const lines = String(t?.description || "").split(/\r?\n/);
  const kept = [];
  const seen = new Set();
  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) continue;
    if (
      /^เรื่องที่ให้โทรกลับ\s*[:：]/.test(line) ||
      /^ประเด็นที่ลูกค้าแจ้ง/.test(line) ||
      /^\d+\.\s+/.test(line) ||
      /^เบอร์ให้โทรกลับ\s*[:：]/.test(line) ||
      /^โทรเข้าจากเบอร์\s*[:：]/.test(line) ||
      /^ชื่อลูกค้า\s*[:：]/.test(line) ||
      /^บริษัท\s*[:：]/.test(line) ||
      /^ข้อมูลติดต่อ\s*[:：]/.test(line)
    ) {
      continue;
    }
    const norm = line.replace(/\s+/g, "").toLowerCase();
    if (!seen.has(norm)) {
      seen.add(norm);
      kept.push(line);
    }
  }
  return kept.slice(-6);
}

/** สร้างคำแนะนำเชิงปฏิบัติสำหรับพนักงานก่อนและระหว่างโทรกลับลูกค้า */
export function buildCallbackAdvice(ticket) {
  if (!ticket) return "";
  const topics = Array.isArray(ticket.topics) ? ticket.topics : [];
  const combined = [ticket.subject, ticket.description, ...topics].filter(Boolean).join(" ");
  const mergedCount = Math.max(1, Number(ticket.mergedCount || 1));
  const lines = [];

  if (mergedCount > 1) {
    lines.push(
      `• ลูกค้าติดต่อเข้ามาซ้ำรวม ${mergedCount} ครั้ง ควรรีบโทรกลับเป็นลำดับแรก ขออภัยที่ให้รอ และช่วยดูแลให้ครบทุกประเด็นในสายเดียว`
    );
  } else if (ticket.priority === "urgent") {
    lines.push("• เคสเร่งด่วน — รีบโทรกลับทันที ทักทายสุภาพและทวนประเด็นที่ลูกค้าฝากไว้เพื่อไม่ต้องเล่าซ้ำ");
  } else {
    lines.push("• ทักทายและยืนยันประเด็นที่ลูกค้าฝากไว้เพื่อให้คำแนะนำตรงจุด");
  }

  if (ticket.callbackPhone && ticket.callerPhone && ticket.callbackPhone !== ticket.callerPhone) {
    lines.push(`• โทรกลับที่เบอร์ปลายทางที่ลูกค้าระบุ (${ticket.callbackPhone}) แทนเบอร์ที่โทรเข้ามา (${ticket.callerPhone})`);
  }

  let matchedDomain = false;
  if (/ต้นทุน|คำนวณ|เพี้ยน|เสียหาย|สต็อก|ติดลบ|FIFO|เฉลี่ย/i.test(combined)) {
    matchedDomain = true;
    lines.push(
      "• เตรียมรีโมต (AnyDesk / TeamViewer) เพื่อตรวจรายการรับ-จ่ายสินค้าและสูตรคำนวณต้นทุน พร้อมสอบถามชื่อฐานข้อมูล รหัสสินค้า และช่วงวันที่ที่พบยอดผิดปกติ"
    );
  }
  if (/รหัสสินค้า|ลูกหนี้|ชำระเงิน|หลายบิล|ใบเสร็จ|วางบิล|รับชำระ/i.test(combined)) {
    matchedDomain = true;
    lines.push(
      "• เตรียมอธิบายขั้นตอนการตั้งค่ารหัสสินค้า/ลูกหนี้ และการรับชำระเงินหลายบิลในใบเสร็จเดียว พร้อมเตรียมส่งคู่มือหรือวิดีโอสอนหลังวางสาย"
    );
  }
  if (/พิมพ์|ฟอร์ม|ใบกำกับ|เครื่องพิมพ์|กระดาษ/i.test(combined)) {
    matchedDomain = true;
    lines.push("• เตรียมตรวจแบบฟอร์มเอกสารและตั้งค่าเครื่องพิมพ์ผ่านรีโมต เพื่อทดสอบพิมพ์ให้ลูกค้าเห็นผลจริง");
  }
  if (/ราคา|แพ็กเกจ|ใบเสนอราคา|ซื้อ|ทดลอง|Demo|รุ่น/i.test(combined)) {
    matchedDomain = true;
    lines.push("• เตรียมตารางเปรียบเทียบแพ็กเกจ (Mini / Standard / Pro / Enterprise) และส่งใบเสนอราคาตามความต้องการใช้งาน");
  }
  if (/ภาษี|VAT|ภ\.พ\.|หัก ณ ที่จ่าย|WHT|e-Tax|ปิดงบ|บัญชี/i.test(combined)) {
    matchedDomain = true;
    lines.push("• เตรียมตรวจผังบัญชี/รายงานภาษีซื้อ-ขาย และสอบถามเวอร์ชันโปรแกรมที่ลูกค้าใช้อยู่ก่อนแนะนำเมนู");
  }
  if (!matchedDomain) {
    lines.push("• ทบทวนรายละเอียดที่ลูกค้าฝากไว้และประวัติการคุยก่อนหน้า เพื่อให้คำตอบครบถ้วนโดยไม่ต้องให้ลูกค้าเล่าซ้ำ");
  }

  lines.push('• เมื่อคุยจบและแก้ไขเรียบร้อยแล้ว กด "ปิด Ticket (พนักงานรับรู้แล้ว)" บนหน้าจอเพื่อเคลียร์การแจ้งเตือน');
  return lines.join("\n").slice(0, 1200);
}

export const LANGUAGE_LABELS = {
  th: "ภาษาไทย (Thai)",
  en: "ภาษาอังกฤษ (English)",
  zh: "ภาษาจีน (中文)",
  ja: "ภาษาญี่ปุ่น (日本語)",
  ko: "ภาษาเกาหลี (한국어)",
  lo: "ภาษาลาว (ລາວ)",
  my: "ภาษาเมียนมา (မြန်မာ)",
  vi: "ภาษาเวียดนาม (Tiếng Việt)",
  km: "ภาษากัมพูชา (ខ្មែរ)",
};

const BAD_CUSTOMER_NAMES = new Set([
  "อะไร", "เล่น", "จริง", "โปรแกรม", "ระบบ", "บริษัท", "ร้าน", "ลูกค้า", "เมนู", "สินค้า",
  "ไม่", "ครับ", "ค่ะ", "คะ", "คับ", "ไม่สะดวก", "ไม่เป็นไร", "ไม่บอก", "ไม่ต้อง", "เบอร์",
  "ราคา", "ใช่", "ไม่ใช่", "โอเค", "ได้เลย", "ดี", "ครับผม", "นะครับ", "นะคะ", "เปลี่ยนเลย", "เรื่องเดิม",
  "และ", "แล้ว", "ก็", "นะ", "นี้", "นั้น", "นี่", "นั่น", "อันนี้", "อันนั้น", "คือ", "หรือ", "แต่", "เพราะ", "ถ้า",
  "กับ", "จาก", "เป็น", "อยู่", "ที่", "ใน", "มา", "ไป", "ได้", "เอา", "ดู", "ลอง", "ถาม", "พูด", "ฟัง", "ช่วย",
  "เอ่อ", "อ่า", "อ้า", "อืม", "อือ", "ฮึ", "หึ", "อ๋อ", "อ้อ", "จ้า", "จ้ะ", "เหรอ", "หรอ", "เลย", "มาก", "หน่อย", "ด้วย",
  "เดี๋ยว", "แป๊บ", "หยุด", "พอ", "เจ๊", "เฮีย", "พี่", "น้อง", "คุณ", "ผม", "ฉัน", "หนู", "ดิฉัน", "มัน", "เขา", "เรา",
  "ไหม", "มั้ย", "ยังไง", "อย่างไร", "เท่าไหร่", "กี่", "บ้าง", "รึเปล่า", "เปล่า", "เหรอครับ", "เหรอคะ",
  "สวัสดี", "สวัสดีครับ", "สวัสดีค่ะ", "หวัดดี", "ฮัลโหล", "ทักทาย", "ขอบคุณ",
  "calling", "asking", "looking", "trying", "interested", "using", "not", "here", "fine", "good", "okay",
  "hello", "hi", "yes", "no", "thanks", "thank", "and",
]);

const GARBLED_CUSTOMER_NAME_RE = /(?:หรือ|แล้ว|แต่|เพราะ|ถ้า|ว่า|ยังไง|อย่างไร|หรือเปล่า|รึเปล่า|ได้ไหม|ได้มั้ย|มั้ย|อะไร|เท่าไหร่|กันได้|โยงกัน|การ[ก-๙]{2,}|ความ[ก-๙]{2,}|เรื่อง|ระบบ|โปรแกรม|สต๊อก|สต็อก|สินค้า|ราคา|บัญชี|ภาษี|สาขา|คลัง|เมนู|รายงาน|เซิร์ฟเวอร์)/;
const BARE_TONE_CUT_NAME_RE = /(?<![าำะัิีึืุูเแโใไ])[ก-ฮ][่-๋]$/;

export function cleanCustomerName(raw) {
  let s = String(raw || "").trim();
  if (!s) return "";
  // Strip accidental leading/trailing particles like "ว่าลุงจืด" -> "ลุงจืด", "แบงค์ชื่อแบงค์" -> "แบงค์"
  for (let i = 0; i < 3; i++) {
    const next = s
      .replace(/^(?:ชื่อเล่น|ชื่อว่า|ชื่อ|เรียกว่า|ว่า|คือ|เป็น)\s*/g, "")
      .replace(/(?:ชื่อเล่น|ชื่อว่า|ชื่อ|ครับผม|นะครับ|นะคะ|ครับ|ค่ะ|คะ|คับ|ก็ได้|นะ|เอง)$/g, "")
      .trim();
    if (next === s) break;
    s = next;
  }
  const firstPart = s.split(/(?:ชื่อเล่น|ชื่อว่า|ชื่อ|เรียกว่า|ครับผม|นะครับ|นะคะ|ครับ|ค่ะ|คะ|คับ)/)[0]?.trim();
  return (firstPart || s).slice(0, 100);
}

export function isPlausibleCustomerName(raw, { explicit = false } = {}) {
  const cand = cleanCustomerName(raw);
  if (!cand || cand.length < 2 || BAD_CUSTOMER_NAMES.has(cand.toLowerCase())) return false;
  if (GARBLED_CUSTOMER_NAME_RE.test(cand)) return false;
  if (!/[เแโใไ][ก-ฮ]{1,2}[่-๋]$/.test(cand) && BARE_TONE_CUT_NAME_RE.test(cand)) return false;
  if (!explicit && /^[ก-๙]+$/.test(cand) && cand.length > 10) return false;
  return true;
}

export class CrmStore {
  constructor(dataDir = path.resolve(process.cwd(), "data")) {
    this.dataDir = path.resolve(dataDir);
    this.crmPath = path.join(this.dataDir, "crm-db.json");
    this.customers = new Map();
    this.tickets = [];
    this.load();
  }

  load() {
    try {
      if (!fs.existsSync(this.crmPath)) return;
      const raw = fs.readFileSync(this.crmPath, "utf8");
      const parsed = JSON.parse(raw);
      let sanitizedOnLoad = false;
      if (parsed?.customers && typeof parsed.customers === "object") {
        for (const [phone, cust] of Object.entries(parsed.customers)) {
          if (cust && typeof cust.name === "string" && cust.name.trim() && !isPlausibleCustomerName(cust.name, { explicit: true })) {
            cust.name = "";
            sanitizedOnLoad = true;
          }
          this.customers.set(phone, cust);
        }
      }
      if (Array.isArray(parsed?.tickets)) {
        for (const t of parsed.tickets) {
          if (t && typeof t.customerName === "string" && t.customerName.trim() && !isPlausibleCustomerName(t.customerName, { explicit: true })) {
            t.customerName = "";
            if (typeof t.description === "string") {
              t.description = t.description.replace(/\n?ชื่อลูกค้า\s*[:：][^\n]*/g, "").trim();
            }
            sanitizedOnLoad = true;
          }
        }
        this.tickets = parsed.tickets;
      }
      const consolidated = this.consolidateOpenTicketsByPhone();
      if (sanitizedOnLoad && !consolidated) {
        this.save();
      }
    } catch (err) {
      console.warn("[CRM] Failed to load:", err.message);
    }
  }

  _findOpenTicketByPhones(phones) {
    const keys = new Set((phones || []).map((p) => normalizePhone(p)).filter(Boolean));
    if (keys.size === 0) return null;
    for (const t of this.tickets) {
      if (!isActiveTicket(t)) continue;
      for (const tp of ticketPhoneSet(t)) {
        if (keys.has(tp)) return t;
      }
    }
    return null;
  }

  _rebuildConsolidatedFields(ticket, { qaNotes = [], aiAdvice = "" } = {}) {
    if (!ticket) return ticket;
    ticket.topics = mergeTopicsList([], extractTopicsFromTicket(ticket));
    ticket.mergedCount = Math.max(1, Number(ticket.mergedCount || 1), ticket.topics.length || 1);

    const topics = ticket.topics;
    const latestTopic = topics[topics.length - 1] || "";

    if (ticket.source === "ai_callback") {
      if (ticket.mergedCount > 1 || topics.length > 1) {
        const topicBadge = topics.length > 1 ? ` • ${topics.length} ประเด็น` : "";
        const prefix = `ขอให้โทรกลับ (รวม ${ticket.mergedCount} ครั้ง${topicBadge})`;
        ticket.subject = (latestTopic ? `${prefix}: ${latestTopic}` : prefix).slice(0, 160);
      } else if (latestTopic) {
        ticket.subject = `ขอให้โทรกลับ: ${latestTopic}`.slice(0, 160);
      } else {
        ticket.subject = "ลูกค้าขอให้พนักงานโทรกลับ";
      }
    } else if (ticket.mergedCount > 1 && ticket.subject && !ticket.subject.startsWith("(รวม ")) {
      ticket.subject = `(รวม ${ticket.mergedCount} ครั้ง) ${ticket.subject}`.slice(0, 160);
    }

    const extraLines = extractExtraDescriptionLines(ticket);
    for (const note of qaNotes || []) {
      const cleanNote = String(note || "").trim();
      if (!cleanNote) continue;
      const norm = cleanNote.replace(/\s+/g, "").toLowerCase();
      if (!extraLines.some((l) => l.replace(/\s+/g, "").toLowerCase() === norm)) {
        extraLines.push(cleanNote);
      }
    }
    const recentExtras = extraLines.slice(-4);

    const blocks = [];
    if (topics.length > 1) {
      blocks.push(`ประเด็นที่ลูกค้าแจ้ง (รวม ${ticket.mergedCount} ครั้ง):`);
      blocks.push(...topics.map((tp, idx) => `${idx + 1}. ${tp}`));
    } else if (topics.length === 1) {
      blocks.push(`เรื่องที่ให้โทรกลับ: ${topics[0]}`);
    }

    if (recentExtras.length > 0) {
      if (blocks.length > 0) blocks.push("");
      blocks.push(...recentExtras);
    }

    const cbPhone = normalizePhone(ticket.callbackPhone || ticket.phone);
    const inPhone = normalizePhone(ticket.callerPhone || ticket.phone);
    const contactLines = [
      cbPhone ? `เบอร์ให้โทรกลับ: ${cbPhone}` : "",
      inPhone && cbPhone && inPhone !== cbPhone ? `โทรเข้าจากเบอร์: ${inPhone}` : "",
      ticket.customerName ? `ชื่อลูกค้า: ${ticket.customerName}` : "",
      ticket.company ? `บริษัท: ${ticket.company}` : "",
    ].filter(Boolean);

    if (contactLines.length > 0) {
      if (blocks.length > 0) blocks.push("");
      blocks.push(...contactLines);
    }

    ticket.description = blocks.join("\n").slice(0, 2000);

    const cleanAiAdvice = String(aiAdvice || "").trim();
    if (cleanAiAdvice) {
      ticket.aiCallbackAdvice = cleanAiAdvice.slice(0, 900);
    }
    if (ticket.aiCallbackAdvice) {
      const ruleLead =
        ticket.mergedCount > 1 && !ticket.aiCallbackAdvice.includes("ติดต่อเข้ามาซ้ำ")
          ? `• ลูกค้าติดต่อเข้ามาซ้ำรวม ${ticket.mergedCount} ครั้ง ควรรีบโทรกลับและตอบให้ครบทุกประเด็นในสายเดียว\n`
          : "";
      ticket.callbackAdvice = `${ruleLead}${ticket.aiCallbackAdvice}`.trim().slice(0, 1200);
    } else {
      ticket.callbackAdvice = buildCallbackAdvice(ticket);
    }
    return ticket;
  }

  consolidateOpenTicketsByPhone() {
    if (!Array.isArray(this.tickets) || this.tickets.length === 0) return false;
    let changed = false;

    const activeIndices = [];
    for (let i = 0; i < this.tickets.length; i++) {
      if (isActiveTicket(this.tickets[i])) activeIndices.push(i);
    }

    // Group active tickets sharing any phone via Union-Find
    const parent = activeIndices.map((_, i) => i);
    const find = (x) => (parent[x] === x ? x : (parent[x] = find(parent[x])));
    const union = (a, b) => {
      const ra = find(a);
      const rb = find(b);
      if (ra !== rb) parent[rb] = ra;
    };

    const phoneOwner = new Map();
    for (let pos = 0; pos < activeIndices.length; pos++) {
      const t = this.tickets[activeIndices[pos]];
      for (const p of ticketPhoneSet(t)) {
        if (phoneOwner.has(p)) {
          union(phoneOwner.get(p), pos);
        } else {
          phoneOwner.set(p, pos);
        }
      }
    }

    const components = new Map();
    for (let pos = 0; pos < activeIndices.length; pos++) {
      const t = this.tickets[activeIndices[pos]];
      if (ticketPhoneSet(t).size === 0) continue;
      const root = find(pos);
      if (!components.has(root)) components.set(root, []);
      components.get(root).push(activeIndices[pos]);
    }

    const removedIds = new Set();
    for (const idxs of components.values()) {
      if (idxs.length < 2) continue;
      changed = true;
      const group = idxs
        .map((idx) => this.tickets[idx])
        .sort((a, b) => (Date.parse(a.createdAt || "") || 0) - (Date.parse(b.createdAt || "") || 0));

      const callbackGroup = group.filter((t) => t.source === "ai_callback");
      const autoGroup = group.filter((t) => t.source !== "ai_callback");
      const primary = callbackGroup.length > 0 ? callbackGroup[callbackGroup.length - 1] : group[group.length - 1];

      let collectedTopics = [];
      for (const t of autoGroup) collectedTopics = mergeTopicsList(collectedTopics, extractTopicsFromTicket(t));
      for (const t of callbackGroup) collectedTopics = mergeTopicsList(collectedTopics, extractTopicsFromTicket(t));

      const extraNotes = [];
      for (const t of group) {
        for (const line of extractExtraDescriptionLines(t)) {
          const norm = line.replace(/\s+/g, "").toLowerCase();
          if (!extraNotes.some((ex) => ex.replace(/\s+/g, "").toLowerCase() === norm)) {
            extraNotes.push(line);
          }
        }
      }

      const mergedCount = group.reduce((sum, t) => sum + Math.max(1, Number(t.mergedCount || 1)), 0);
      const sessionSet = new Set();
      for (const t of group) {
        if (t.sessionId) sessionSet.add(t.sessionId);
        if (Array.isArray(t.sessionIds)) {
          for (const sid of t.sessionIds) if (sid) sessionSet.add(sid);
        }
      }

      let bestPriority = primary.priority || "medium";
      for (const t of group) {
        if ((PRIORITY_RANK[t.priority] || 0) > (PRIORITY_RANK[bestPriority] || 0)) {
          bestPriority = t.priority;
        }
      }

      const pickLatestNonEmpty = (field) => {
        for (let i = group.length - 1; i >= 0; i--) {
          if (group[i][field]) return group[i][field];
        }
        return "";
      };

      const hasUnackOpen = callbackGroup.some((t) => t.status === "open" && !t.acknowledgedAt) ||
        group.some((t) => t.status === "open" && !t.acknowledgedAt);

      primary.source = callbackGroup.length > 0 ? "ai_callback" : primary.source;
      primary.priority = callbackGroup.length > 0 ? "urgent" : bestPriority;
      primary.assignee = callbackGroup.length > 0 ? "ทีมโทรกลับลูกค้า" : (primary.assignee || "ทีมดูแลลูกค้า");
      primary.callbackPhone = pickLatestNonEmpty("callbackPhone") || primary.phone;
      primary.callerPhone = pickLatestNonEmpty("callerPhone") || primary.phone;
      primary.phone = primary.callbackPhone || primary.callerPhone || primary.phone;
      primary.customerName = cleanCustomerName(pickLatestNonEmpty("customerName")) || primary.customerName || "";
      primary.company = pickLatestNonEmpty("company") || primary.company || "";
      for (let i = group.length - 1; i >= 0; i--) {
        if (Number.isInteger(group[i].lineId)) {
          primary.lineId = group[i].lineId;
          break;
        }
      }
      primary.sessionIds = Array.from(sessionSet).slice(-50);
      primary.sessionId = primary.sessionId || primary.sessionIds[primary.sessionIds.length - 1] || null;
      primary.topics = collectedTopics;
      primary.mergedCount = mergedCount;
      primary.aiCallbackAdvice = pickLatestNonEmpty("aiCallbackAdvice") || "";

      const latestTime = group.reduce((maxIso, t) => {
        const cur = t.updatedAt || t.createdAt || "";
        return cur > maxIso ? cur : maxIso;
      }, primary.updatedAt || primary.createdAt || "");
      if (latestTime) primary.updatedAt = latestTime;

      if (hasUnackOpen) {
        primary.status = "open";
        primary.acknowledgedAt = null;
        primary.acknowledgedBy = null;
      }

      primary.description = extraNotes.join("\n");
      this._rebuildConsolidatedFields(primary);

      for (const t of group) {
        if (t !== primary) removedIds.add(t.id);
      }
    }

    if (removedIds.size > 0) {
      this.tickets = this.tickets.filter((t) => !removedIds.has(t.id));
    }

    // Ensure every active ticket has topics, mergedCount, and callbackAdvice populated
    for (const t of this.tickets) {
      if (!isActiveTicket(t)) continue;
      if (!t.callbackAdvice || !Array.isArray(t.topics)) {
        changed = true;
        this._rebuildConsolidatedFields(t);
      }
    }

    if (changed) {
      this.tickets.sort((a, b) => {
        const aClosed = isActiveTicket(a) ? 0 : 1;
        const bClosed = isActiveTicket(b) ? 0 : 1;
        if (aClosed !== bClosed) return aClosed - bClosed;
        const aTime = String(a.updatedAt || a.createdAt || "");
        const bTime = String(b.updatedAt || b.createdAt || "");
        return bTime.localeCompare(aTime);
      });
      this.save();
    }
    return changed;
  }

  save() {
    try {
      if (!fs.existsSync(this.dataDir)) {
        fs.mkdirSync(this.dataDir, { recursive: true });
      }
      const payload = {
        customers: Object.fromEntries(this.customers.entries()),
        tickets: this.tickets.slice(0, 300),
      };
      fs.writeFileSync(this.crmPath, JSON.stringify(payload, null, 2), "utf8");
    } catch (err) {
      console.warn("[CRM] Failed to save:", err.message);
    }
  }

  getCustomer(phone) {
    const key = normalizePhone(phone);
    return key ? this.customers.get(key) || null : null;
  }

  ensureCustomerOnCall(phone) {
    const key = normalizePhone(phone);
    if (!key) return null;
    const now = new Date().toISOString();
    let cust = this.customers.get(key);
    if (!cust) {
      cust = {
        phone: key,
        name: "",
        company: "",
        tier: "new",
        stage: "lead",
        preferredLanguage: null,
        tags: [],
        notes: "",
        avgCsat: null,
        csatCount: 0,
        lastSentiment: null,
        totalCalls: 0,
        createdAt: now,
        updatedAt: now,
        lastCallAt: now,
      };
      this.customers.set(key, cust);
    }
    cust.totalCalls = Number(cust.totalCalls || 0) + 1;
    if (cust.totalCalls >= 3 && cust.tier === "new") {
      cust.tier = "regular";
    }
    cust.lastCallAt = now;
    cust.updatedAt = now;
    this.save();
    return cust;
  }

  upsertCustomer(input) {
    const key = normalizePhone(input?.phone);
    if (!key) throw new Error("กรุณาระบุเบอร์โทรศัพท์ลูกค้า");
    const now = new Date().toISOString();
    const existing = this.customers.get(key);
    const tags = Array.isArray(input.tags)
      ? input.tags.map((t) => String(t).trim()).filter(Boolean).slice(0, 12)
      : typeof input.tags === "string"
        ? input.tags.split(",").map((t) => t.trim()).filter(Boolean).slice(0, 12)
        : existing?.tags || [];

    const cust = {
      phone: key,
      name: input.name !== undefined ? cleanCustomerName(input.name) : cleanCustomerName(existing?.name || ""),
      company: input.company !== undefined ? String(input.company).trim().slice(0, 120) : existing?.company || "",
      tier: VALID_TIERS.has(input.tier) ? input.tier : existing?.tier || "new",
      stage: VALID_STAGES.has(input.stage) ? input.stage : existing?.stage || "lead",
      preferredLanguage:
        input.preferredLanguage === null || input.preferredLanguage === ""
          ? null
          : VALID_LANGUAGES.has(input.preferredLanguage)
            ? input.preferredLanguage
            : existing?.preferredLanguage ?? null,
      tags,
      notes: input.notes !== undefined ? String(input.notes).trim().slice(0, 1000) : existing?.notes || "",
      avgCsat: existing?.avgCsat ?? null,
      csatCount: existing?.csatCount ?? 0,
      lastSentiment: existing?.lastSentiment ?? null,
      totalCalls: existing?.totalCalls ?? 0,
      createdAt: existing?.createdAt || now,
      updatedAt: now,
      lastCallAt: existing?.lastCallAt || now,
    };

    this.customers.set(key, cust);
    this.save();
    return cust;
  }

  setCustomerLanguage(phone, language) {
    const key = normalizePhone(phone);
    if (!key) return null;
    const lang = language && VALID_LANGUAGES.has(language) ? language : null;
    const cust = this.getCustomer(key) || this.ensureCustomerOnCall(key);
    if (!cust) return null;
    cust.preferredLanguage = lang;
    cust.updatedAt = new Date().toISOString();
    this.save();
    return cust;
  }

  applyPostCallAnalysis(phone, sessionId, analysis) {
    const key = normalizePhone(phone);
    if (!key || !analysis || analysis.error) return null;

    const now = new Date().toISOString();
    const cust = this.getCustomer(key) || this.ensureCustomerOnCall(key);
    if (!cust) return null;

    if (typeof analysis.csatScore === "number") {
      const prevCount = Number(cust.csatCount || 0);
      const prevAvg = Number(cust.avgCsat || 0);
      const nextCount = prevCount + 1;
      cust.avgCsat = Math.round(((prevAvg * prevCount + analysis.csatScore) / nextCount) * 10) / 10;
      cust.csatCount = nextCount;
    }

    if (analysis.sentiment) {
      cust.lastSentiment = analysis.sentiment;
    }

    if (analysis.sentiment === "negative" || analysis.sentiment === "urgent" || (analysis.csatScore && analysis.csatScore <= 5)) {
      cust.tier = "at_risk";
      if (cust.stage === "customer") cust.stage = "churn_risk";
    }

    if (analysis.callerName) {
      const cleanAnalysisName = cleanCustomerName(analysis.callerName);
      if (
        cleanAnalysisName &&
        isPlausibleCustomerName(cleanAnalysisName, { explicit: true }) &&
        (!cust.name || cust.name.startsWith("ว่า") || !isPlausibleCustomerName(cust.name, { explicit: true }))
      ) {
        cust.name = cleanAnalysisName;
      }
    }
    if (analysis.callerCompany && !cust.company) {
      cust.company = String(analysis.callerCompany).trim().slice(0, 120);
    }
    if (analysis.topic && !cust.tags.includes(analysis.topic) && cust.tags.length < 8) {
      cust.tags = [...cust.tags, String(analysis.topic).slice(0, 32)];
    }
    cust.updatedAt = now;

    // Auto-create or consolidate follow-up ticket when AI flags unresolved or needs human follow-up
    const qaNotes = [
      analysis.summary ? `สรุปการสนทนา: ${analysis.summary}` : "",
      analysis.followUpAction ? `สิ่งที่ต้องทำต่อ: ${analysis.followUpAction}` : "",
      analysis.resolutionSteps ? `แนวทางแก้ไขเบื้องต้น: ${analysis.resolutionSteps}` : "",
    ].filter(Boolean);
    const aiAdvice =
      String(analysis.callbackAdvice || "").trim() ||
      [analysis.followUpAction, analysis.resolutionSteps]
        .filter(Boolean)
        .map((s) => `• ${s}`)
        .join("\n");

    const needsFollowUp = Boolean(analysis.needsHumanFollowUp || analysis.resolved === false);
    const alreadyHasSession = Boolean(
      sessionId &&
        this.tickets.some(
          (t) => t.sessionId === sessionId || (Array.isArray(t.sessionIds) && t.sessionIds.includes(sessionId))
        )
    );
    const existingOpen = this._findOpenTicketByPhones([key]);
    const analysisPriority =
      analysis.urgency === "high" || analysis.sentiment === "urgent"
        ? "urgent"
        : analysis.urgency === "medium" || analysis.sentiment === "negative"
          ? "high"
          : "medium";

    let createdTicket = null;
    if (existingOpen) {
      const sameSessionOnOpen = Boolean(
        sessionId &&
          (existingOpen.sessionId === sessionId ||
            (Array.isArray(existingOpen.sessionIds) && existingOpen.sessionIds.includes(sessionId)))
      );
      if (sameSessionOnOpen || needsFollowUp) {
        if (sessionId) {
          existingOpen.sessionIds = Array.from(
            new Set([...(Array.isArray(existingOpen.sessionIds) ? existingOpen.sessionIds : []), sessionId])
          ).slice(-50);
          if (!existingOpen.sessionId) existingOpen.sessionId = sessionId;
        }
        if (cust.name && !existingOpen.customerName) existingOpen.customerName = cust.name;
        if (cust.company && !existingOpen.company) existingOpen.company = cust.company;
        if ((PRIORITY_RANK[analysisPriority] || 0) > (PRIORITY_RANK[existingOpen.priority] || 0)) {
          existingOpen.priority = analysisPriority;
        }
        if (!sameSessionOnOpen && needsFollowUp) {
          const newTopic = cleanTopicText(analysis.topic || analysis.summary || "");
          existingOpen.topics = mergeTopicsList(
            extractTopicsFromTicket(existingOpen),
            newTopic ? [newTopic] : []
          );
          existingOpen.mergedCount = Math.max(1, Number(existingOpen.mergedCount || 1)) + 1;
        }
        this._rebuildConsolidatedFields(existingOpen, { qaNotes, aiAdvice });
        existingOpen.updatedAt = now;
      }
    } else if (needsFollowUp && !alreadyHasSession) {
      const firstTopic = cleanTopicText(analysis.topic || "ติดตามลูกค้าจากสายสนทนา");
      createdTicket = this.createTicket({
        phone: key,
        customerName: cust.name,
        company: cust.company,
        subject: analysis.topic || "ติดตามลูกค้าจากสายสนทนา",
        description: qaNotes.join("\n"),
        topics: firstTopic ? [firstTopic] : [],
        mergedCount: 1,
        sessionIds: sessionId ? [sessionId] : [],
        priority: analysisPriority,
        assignee: analysisPriority === "urgent" ? "ฝ่ายซัพพอร์ตเร่งด่วน" : "ทีมดูแลลูกค้า",
        source: "ai_auto",
        sessionId,
        aiCallbackAdvice: aiAdvice,
      });
      this._rebuildConsolidatedFields(createdTicket, { qaNotes, aiAdvice });
    }

    this.save();
    return { customer: cust, ticket: createdTicket };
  }

  createTicket(input) {
    const key = normalizePhone(input?.phone);
    const subject = String(input?.subject || "").trim().slice(0, 160);
    if (!subject) throw new Error("กรุณาระบุหัวข้อเคส");

    const now = new Date();
    const ymd = `${String(now.getFullYear()).slice(-2)}${String(now.getMonth() + 1).padStart(2, "0")}${String(now.getDate()).padStart(2, "0")}`;
    const prefix = `TCK-${ymd}-`;
    let maxSeq = 0;
    for (const t of this.tickets) {
      if (typeof t.id === "string" && t.id.startsWith(prefix)) {
        const n = Number.parseInt(t.id.slice(prefix.length), 10);
        if (Number.isFinite(n) && n > maxSeq) maxSeq = n;
      }
    }
    const seq = String(maxSeq + 1).padStart(3, "0");
    const cust = key ? this.getCustomer(key) : null;
    const topics = Array.isArray(input.topics) ? mergeTopicsList([], input.topics) : [];
    const sessionIds = Array.isArray(input.sessionIds)
      ? Array.from(new Set(input.sessionIds.filter(Boolean))).slice(-50)
      : input.sessionId
        ? [input.sessionId]
        : [];

    const ticket = {
      id: `${prefix}${seq}`,
      phone: key || "",
      callbackPhone: normalizePhone(input?.callbackPhone) || key || "",
      callerPhone: normalizePhone(input?.callerPhone) || key || "",
      customerName: String(input.customerName || cust?.name || "").trim().slice(0, 100),
      company: String(input.company || cust?.company || "").trim().slice(0, 120),
      subject,
      description: String(input.description || "").trim().slice(0, 2000),
      topics,
      mergedCount: Math.max(1, Number(input.mergedCount || 1), topics.length || 1),
      status: VALID_TICKET_STATUS.has(input.status) ? input.status : "open",
      priority: VALID_PRIORITIES.has(input.priority) ? input.priority : "medium",
      assignee: String(input.assignee || "ทีมดูแลลูกค้า").trim().slice(0, 80),
      source: VALID_SOURCES.has(input.source) ? input.source : "manual",
      sessionId: input.sessionId || null,
      sessionIds,
      lineId: Number.isInteger(input.lineId) ? input.lineId : null,
      aiCallbackAdvice: input.aiCallbackAdvice ? String(input.aiCallbackAdvice).trim().slice(0, 900) : "",
      callbackAdvice: input.callbackAdvice ? String(input.callbackAdvice).trim().slice(0, 1200) : "",
      acknowledgedAt: input.acknowledgedAt || null,
      acknowledgedBy: input.acknowledgedBy ? String(input.acknowledgedBy).trim().slice(0, 80) : null,
      closedAt: input.closedAt || null,
      closedBy: input.closedBy ? String(input.closedBy).trim().slice(0, 80) : null,
      createdAt: now.toISOString(),
      updatedAt: now.toISOString(),
    };
    if (!ticket.callbackAdvice) {
      ticket.callbackAdvice = buildCallbackAdvice(ticket);
    }

    this.tickets.unshift(ticket);
    if (this.tickets.length > 300) this.tickets.length = 300;
    this.save();
    return ticket;
  }

  createCallbackTicket(input) {
    const { phone, callbackPhone, callerPhone, customerName, company, message, sessionId, lineId } = input || {};
    const cbPhone = normalizePhone(callbackPhone || phone || callerPhone);
    const inPhone = normalizePhone(callerPhone || phone || cbPhone);
    const msg = String(message || "").trim().slice(0, 500);
    const cleanMsg = cleanTopicText(msg);
    const name = cleanCustomerName(customerName || "");
    const comp = String(company || "").trim().slice(0, 120);

    const custTarget = inPhone || cbPhone;
    if (custTarget && (name || comp)) {
      const custPatch = { phone: custTarget };
      if (name) custPatch.name = name;
      if (comp) custPatch.company = comp;
      try {
        this.upsertCustomer(custPatch);
      } catch {}
    }

    const nowMs = Date.now();
    // 1. Exact duplicate guard within the same call session (or quick retry with no sessionId)
    const existingExact = this.tickets.find((t) => {
      if (!isActiveTicket(t) || t.source !== "ai_callback") return false;
      const tPhones = ticketPhoneSet(t);
      const sharePhone = Boolean((cbPhone && tPhones.has(cbPhone)) || (inPhone && tPhones.has(inPhone)));
      const sameSession = Boolean(
        sessionId && (t.sessionId === sessionId || (Array.isArray(t.sessionIds) && t.sessionIds.includes(sessionId)))
      );
      const recentNoSession = Boolean(
        !sessionId && sharePhone && nowMs - Date.parse(t.updatedAt || t.createdAt || "") <= 60_000
      );
      if (!sameSession && !recentNoSession) return false;
      if (!cleanMsg) return true;
      const tTopics = extractTopicsFromTicket(t);
      return tTopics.some((tp) => tp.includes(cleanMsg) || cleanMsg.includes(tp)) || String(t.description || "").includes(cleanMsg);
    });
    if (existingExact) return { ticket: existingExact, created: false, updated: false };

    // 2. Per-phone open ticket consolidation: keep max 1 active ticket per phone number
    const existingOpen = this._findOpenTicketByPhones([cbPhone, inPhone]);
    if (existingOpen) {
      const nowIso = new Date().toISOString();
      existingOpen.source = "ai_callback";
      existingOpen.priority = "urgent";
      if (!existingOpen.assignee || existingOpen.assignee === "ทีมดูแลลูกค้า" || existingOpen.assignee === "ฝ่ายซัพพอร์ตเร่งด่วน") {
        existingOpen.assignee = "ทีมโทรกลับลูกค้า";
      }
      if (cbPhone) existingOpen.callbackPhone = cbPhone;
      if (inPhone) existingOpen.callerPhone = inPhone;
      existingOpen.phone = cbPhone || inPhone || existingOpen.phone;
      if (name) existingOpen.customerName = name;
      if (comp) existingOpen.company = comp;
      if (Number.isInteger(lineId)) existingOpen.lineId = lineId;
      if (sessionId) {
        existingOpen.sessionId = sessionId;
        existingOpen.sessionIds = Array.from(
          new Set([...(Array.isArray(existingOpen.sessionIds) ? existingOpen.sessionIds : []), sessionId])
        ).slice(-50);
      }
      existingOpen.topics = mergeTopicsList(
        extractTopicsFromTicket(existingOpen),
        cleanMsg ? [cleanMsg] : []
      );
      existingOpen.mergedCount = Math.max(1, Number(existingOpen.mergedCount || 1)) + 1;
      // Re-open so staff receives fresh alert and must confirm/close again
      existingOpen.status = "open";
      existingOpen.acknowledgedAt = null;
      existingOpen.acknowledgedBy = null;
      existingOpen.updatedAt = nowIso;
      existingOpen.aiCallbackAdvice = "";

      this._rebuildConsolidatedFields(existingOpen);

      const idx = this.tickets.indexOf(existingOpen);
      if (idx > 0) {
        this.tickets.splice(idx, 1);
        this.tickets.unshift(existingOpen);
      }
      this.save();
      return { ticket: existingOpen, created: false, updated: true };
    }

    const topics = cleanMsg ? [cleanMsg] : [];
    const ticket = this.createTicket({
      phone: cbPhone || inPhone,
      callbackPhone: cbPhone || inPhone,
      callerPhone: inPhone || cbPhone,
      customerName: name,
      company: comp,
      subject: cleanMsg ? `ขอให้โทรกลับ: ${cleanMsg.slice(0, 120)}` : "ลูกค้าขอให้พนักงานโทรกลับ",
      description: "",
      topics,
      mergedCount: 1,
      status: "open",
      priority: "urgent",
      assignee: "ทีมโทรกลับลูกค้า",
      source: "ai_callback",
      sessionId: sessionId || null,
      sessionIds: sessionId ? [sessionId] : [],
      lineId: Number.isInteger(lineId) ? lineId : null,
    });
    this._rebuildConsolidatedFields(ticket);
    this.save();
    return { ticket, created: true, updated: false };
  }

  acknowledgeTicket(id, actor = "พนักงาน") {
    const ticket = this.tickets.find((t) => t.id === id);
    if (!ticket) throw new Error("ไม่พบเคสที่ระบุ");
    const now = new Date().toISOString();
    const who = String(actor || "พนักงาน").trim().slice(0, 80) || "พนักงาน";
    if (!ticket.acknowledgedAt) ticket.acknowledgedAt = now;
    if (!ticket.acknowledgedBy) ticket.acknowledgedBy = who;
    if (ticket.status === "open") ticket.status = "in_progress";
    ticket.updatedAt = now;
    this.save();
    return ticket;
  }

  closeTicket(id, actor = "พนักงาน", note = "") {
    const ticket = this.tickets.find((t) => t.id === id);
    if (!ticket) throw new Error("ไม่พบเคสที่ระบุ");
    const now = new Date().toISOString();
    const who = String(actor || "พนักงาน").trim().slice(0, 80) || "พนักงาน";
    ticket.status = "closed";
    if (!ticket.acknowledgedAt) ticket.acknowledgedAt = now;
    if (!ticket.acknowledgedBy) ticket.acknowledgedBy = who;
    ticket.closedAt = now;
    ticket.closedBy = who;
    const cleanNote = String(note || "").trim();
    if (cleanNote) {
      ticket.description = [ticket.description, `[ปิดเคสโดย ${who}]: ${cleanNote}`]
        .filter(Boolean)
        .join("\n")
        .slice(0, 2000);
    }
    ticket.updatedAt = now;
    this.save();
    return ticket;
  }

  updateTicket(id, patch, actor = "") {
    const ticket = this.tickets.find((t) => t.id === id);
    if (!ticket) throw new Error("ไม่พบเคสที่ระบุ");
    const now = new Date().toISOString();
    const who = String(actor || "พนักงาน").trim().slice(0, 80) || "พนักงาน";
    if (patch.status && VALID_TICKET_STATUS.has(patch.status)) {
      ticket.status = patch.status;
      if ((patch.status === "closed" || patch.status === "resolved") && !ticket.acknowledgedAt) {
        ticket.acknowledgedAt = now;
        ticket.acknowledgedBy = who;
      }
      if (patch.status === "closed") {
        ticket.closedAt = ticket.closedAt || now;
        ticket.closedBy = ticket.closedBy || who;
      }
      if (patch.status === "in_progress" && actor && !ticket.acknowledgedAt) {
        ticket.acknowledgedAt = now;
        ticket.acknowledgedBy = who;
      }
    }
    if (patch.priority && VALID_PRIORITIES.has(patch.priority)) ticket.priority = patch.priority;
    if (patch.assignee !== undefined) ticket.assignee = String(patch.assignee).trim().slice(0, 80);
    if (patch.subject !== undefined && String(patch.subject).trim()) {
      ticket.subject = String(patch.subject).trim().slice(0, 160);
    }
    if (patch.description !== undefined) {
      ticket.description = String(patch.description).trim().slice(0, 2000);
    }
    ticket.updatedAt = now;
    this.save();
    return ticket;
  }

  deleteTicket(id) {
    const idx = this.tickets.findIndex((t) => t.id === id);
    if (idx >= 0) {
      this.tickets.splice(idx, 1);
      this.save();
    }
  }

  getOpenTicketCount(phone) {
    const key = normalizePhone(phone);
    if (!key) return 0;
    return this.tickets.filter(
      (t) =>
        isActiveTicket(t) &&
        (t.phone === key || t.callerPhone === key || t.callbackPhone === key)
    ).length;
  }

  buildCrmPromptSnippet(phone) {
    const cust = this.getCustomer(phone);
    if (!cust) return "";
    const parts = [];
    if (cust.name) parts.push(`ชื่อลูกค้า: ${cust.name}`);
    if (cust.company) parts.push(`ร้าน/บริษัท: ${cust.company}`);
    if (cust.preferredLanguage && LANGUAGE_LABELS[cust.preferredLanguage]) {
      parts.push(`ภาษาที่ลูกค้าใช้ประจำ: ${LANGUAGE_LABELS[cust.preferredLanguage]}`);
    }
    if (cust.tier === "vip") parts.push("ระดับ: VIP");
    else if (cust.tier === "at_risk") parts.push("ระดับ: ต้องดูแลเป็นพิเศษ");
    if (cust.notes) parts.push(`โน้ต CRM: ${cust.notes.slice(0, 100)}`);
    const openTickets = this.tickets.filter(
      (t) =>
        isActiveTicket(t) &&
        (t.phone === cust.phone || t.callerPhone === cust.phone || t.callbackPhone === cust.phone)
    );
    if (openTickets.length > 0) {
      parts.push(`มีเคสค้าง ${openTickets.length} รายการ (ล่าสุด: ${openTickets[0].subject})`);
    }
    return parts.join(" | ");
  }

  publicState() {
    const customers = Array.from(this.customers.values()).sort((a, b) =>
      String(b.lastCallAt || b.updatedAt).localeCompare(String(a.lastCallAt || a.updatedAt))
    );
    const openTickets = this.tickets.filter((t) => t.status !== "resolved" && t.status !== "closed").length;
    const urgentTickets = this.tickets.filter(
      (t) => (t.priority === "urgent" || t.priority === "high") && t.status !== "resolved" && t.status !== "closed"
    ).length;
    const pendingCallbacks = this.tickets.filter(
      (t) => t.source === "ai_callback" && t.status !== "resolved" && t.status !== "closed"
    ).length;
    const unacknowledgedCallbacks = this.tickets.filter(
      (t) => t.source === "ai_callback" && t.status === "open" && !t.acknowledgedAt
    ).length;
    const vipOrRisk = customers.filter((c) => c.tier === "vip" || c.tier === "at_risk").length;
    const rated = customers.filter((c) => typeof c.avgCsat === "number");
    const avgCsat = rated.length
      ? Math.round((rated.reduce((sum, c) => sum + c.avgCsat, 0) / rated.length) * 10) / 10
      : null;

    return {
      customers: customers.slice(0, 100),
      tickets: this.tickets.slice(0, 100),
      stats: {
        totalCustomers: customers.length,
        vipOrRisk,
        openTickets,
        urgentTickets,
        pendingCallbacks,
        unacknowledgedCallbacks,
        avgCsat,
      },
    };
  }
}
