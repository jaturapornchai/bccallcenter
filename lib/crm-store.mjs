import fs from "node:fs";
import path from "node:path";
import { normalizePhone } from "./call-memory.mjs";

const DATA_DIR = path.resolve(process.cwd(), "data");
const CRM_PATH = path.join(DATA_DIR, "crm-db.json");

const VALID_TIERS = new Set(["new", "regular", "vip", "at_risk"]);
const VALID_STAGES = new Set(["lead", "prospect", "customer", "churn_risk"]);
const VALID_TICKET_STATUS = new Set(["open", "in_progress", "waiting_customer", "resolved", "closed"]);
const VALID_PRIORITIES = new Set(["low", "medium", "high", "urgent"]);
export const VALID_LANGUAGES = new Set(["th", "en", "zh", "ja", "ko", "lo", "my", "vi", "km"]);

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

export function cleanCustomerName(raw) {
  let s = String(raw || "").trim();
  if (!s) return "";
  // Strip accidental leading particles like "ว่าลุงจืด" -> "ลุงจืด", "ชื่อสมชาย" -> "สมชาย"
  for (let i = 0; i < 3; i++) {
    const next = s
      .replace(/^(?:ชื่อเล่น|ชื่อว่า|ชื่อ|เรียกว่า|ว่า|คือ|เป็น)\s*/g, "")
      .replace(/(?:ครับผม|นะครับ|นะคะ|ครับ|ค่ะ|คะ|คับ|ก็ได้|นะ|เอง)$/g, "")
      .trim();
    if (next === s) break;
    s = next;
  }
  return s.slice(0, 100);
}

export class CrmStore {
  constructor() {
    this.customers = new Map();
    this.tickets = [];
    this.load();
  }

  load() {
    try {
      if (!fs.existsSync(CRM_PATH)) return;
      const raw = fs.readFileSync(CRM_PATH, "utf8");
      const parsed = JSON.parse(raw);
      if (parsed?.customers && typeof parsed.customers === "object") {
        for (const [phone, cust] of Object.entries(parsed.customers)) {
          this.customers.set(phone, cust);
        }
      }
      if (Array.isArray(parsed?.tickets)) {
        this.tickets = parsed.tickets;
      }
    } catch (err) {
      console.warn("[CRM] Failed to load:", err.message);
    }
  }

  save() {
    try {
      if (!fs.existsSync(DATA_DIR)) {
        fs.mkdirSync(DATA_DIR, { recursive: true });
      }
      const payload = {
        customers: Object.fromEntries(this.customers.entries()),
        tickets: this.tickets.slice(0, 300),
      };
      fs.writeFileSync(CRM_PATH, JSON.stringify(payload, null, 2), "utf8");
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
      if (cleanAnalysisName && (!cust.name || cust.name.startsWith("ว่า"))) {
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

    // Auto-create follow-up ticket when AI flags unresolved or needs human follow-up
    let createdTicket = null;
    if (analysis.needsHumanFollowUp || analysis.resolved === false) {
      const alreadyExists = this.tickets.some((t) => t.sessionId && t.sessionId === sessionId);
      if (!alreadyExists) {
        const priority =
          analysis.urgency === "high" || analysis.sentiment === "urgent"
            ? "urgent"
            : analysis.urgency === "medium" || analysis.sentiment === "negative"
              ? "high"
              : "medium";
        createdTicket = this.createTicket({
          phone: key,
          customerName: cust.name,
          company: cust.company,
          subject: analysis.topic || "ติดตามลูกค้าจากสายสนทนา",
          description: [
            analysis.summary ? `สรุป: ${analysis.summary}` : "",
            analysis.followUpAction ? `สิ่งที่ต้องทำต่อ: ${analysis.followUpAction}` : "",
            analysis.resolutionSteps ? `วิธีแก้ไขเบื้องต้น: ${analysis.resolutionSteps}` : "",
          ]
            .filter(Boolean)
            .join("\n"),
          priority,
          assignee: priority === "urgent" ? "ฝ่ายซัพพอร์ตเร่งด่วน" : "ทีมดูแลลูกค้า",
          source: "ai_auto",
          sessionId,
        });
      }
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
    const seq = String(this.tickets.length + 1).padStart(3, "0");
    const cust = key ? this.getCustomer(key) : null;

    const ticket = {
      id: `TCK-${ymd}-${seq}`,
      phone: key || "",
      customerName: String(input.customerName || cust?.name || "").trim().slice(0, 100),
      company: String(input.company || cust?.company || "").trim().slice(0, 120),
      subject,
      description: String(input.description || "").trim().slice(0, 1500),
      status: VALID_TICKET_STATUS.has(input.status) ? input.status : "open",
      priority: VALID_PRIORITIES.has(input.priority) ? input.priority : "medium",
      assignee: String(input.assignee || "ทีมดูแลลูกค้า").trim().slice(0, 80),
      source: input.source === "ai_auto" ? "ai_auto" : "manual",
      sessionId: input.sessionId || null,
      createdAt: now.toISOString(),
      updatedAt: now.toISOString(),
    };

    this.tickets.unshift(ticket);
    if (this.tickets.length > 300) this.tickets.length = 300;
    this.save();
    return ticket;
  }

  updateTicket(id, patch) {
    const ticket = this.tickets.find((t) => t.id === id);
    if (!ticket) throw new Error("ไม่พบเคสที่ระบุ");
    if (patch.status && VALID_TICKET_STATUS.has(patch.status)) ticket.status = patch.status;
    if (patch.priority && VALID_PRIORITIES.has(patch.priority)) ticket.priority = patch.priority;
    if (patch.assignee !== undefined) ticket.assignee = String(patch.assignee).trim().slice(0, 80);
    if (patch.subject !== undefined && String(patch.subject).trim()) {
      ticket.subject = String(patch.subject).trim().slice(0, 160);
    }
    if (patch.description !== undefined) {
      ticket.description = String(patch.description).trim().slice(0, 1500);
    }
    ticket.updatedAt = new Date().toISOString();
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
      (t) => t.phone === key && t.status !== "resolved" && t.status !== "closed"
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
      (t) => t.phone === cust.phone && t.status !== "resolved" && t.status !== "closed"
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
        avgCsat,
      },
    };
  }
}
