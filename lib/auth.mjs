import crypto from "node:crypto";

const COOKIE_NAME = "bc_cc_session";
const SESSION_TTL_SEC = 7 * 24 * 60 * 60; // 7 days

// Cached ephemeral fallback secret if AUTH_COOKIE_SECRET is unset (fail-safe)
let runtimeSecret = null;

function getSecret() {
  if (process.env.AUTH_COOKIE_SECRET) return process.env.AUTH_COOKIE_SECRET;
  if (!runtimeSecret) runtimeSecret = crypto.randomBytes(32).toString("hex");
  return runtimeSecret;
}

export function getAdminEmails() {
  const raw = [process.env.ADMIN_EMAIL || "", process.env.ADMIN_EMAILS || ""].join(",");
  const list = raw
    .split(/[\s,;]+/)
    .map((e) => e.trim().toLowerCase())
    .filter(Boolean);
  return new Set(list);
}

export function isAdminEmail(email) {
  const candidate = String(email || "").trim().toLowerCase();
  return Boolean(candidate && getAdminEmails().has(candidate));
}

function b64urlEncode(str) {
  return Buffer.from(str, "utf8").toString("base64url");
}

function b64urlDecode(str) {
  return Buffer.from(str, "base64url").toString("utf8");
}

function sign(data) {
  return crypto.createHmac("sha256", getSecret()).update(data).digest("base64url");
}

export function createSessionToken(user) {
  const payload = {
    email: String(user.email || "").trim().toLowerCase(),
    name: String(user.name || "").trim().slice(0, 120),
    picture: String(user.picture || "").trim().slice(0, 400),
    isAdmin: isAdminEmail(user.email),
    exp: Math.floor(Date.now() / 1000) + SESSION_TTL_SEC,
  };
  const encoded = b64urlEncode(JSON.stringify(payload));
  const sig = sign(encoded);
  return `${encoded}.${sig}`;
}

export function parseCookies(cookieHeader) {
  const out = {};
  const raw = String(cookieHeader || "");
  if (!raw) return out;
  for (const part of raw.split(";")) {
    const idx = part.indexOf("=");
    if (idx <= 0) continue;
    const k = part.slice(0, idx).trim();
    const v = part.slice(idx + 1).trim();
    out[k] = decodeURIComponent(v);
  }
  return out;
}

export function verifySessionFromCookieHeader(cookieHeader) {
  try {
    const cookies = parseCookies(cookieHeader);
    const token = cookies[COOKIE_NAME];
    if (!token || !token.includes(".")) return null;
    const [encoded, sig] = token.split(".");
    if (!encoded || !sig) return null;
    const expected = sign(encoded);
    const sigBuf = Buffer.from(sig);
    const expBuf = Buffer.from(expected);
    if (sigBuf.length !== expBuf.length || !crypto.timingSafeEqual(sigBuf, expBuf)) {
      return null;
    }
    const payload = JSON.parse(b64urlDecode(encoded));
    if (!payload || typeof payload.exp !== "number" || payload.exp < Math.floor(Date.now() / 1000)) {
      return null;
    }
    // Re-evaluate admin status dynamically against current ADMIN_EMAIL / ADMIN_EMAILS env vars
    return {
      email: payload.email || "",
      name: payload.name || "",
      picture: payload.picture || "",
      isAdmin: isAdminEmail(payload.email),
    };
  } catch {
    return null;
  }
}

export function buildSessionSetCookie(token, isHttps = false) {
  const parts = [
    `${COOKIE_NAME}=${encodeURIComponent(token)}`,
    "Path=/",
    "HttpOnly",
    "SameSite=Lax",
    `Max-Age=${SESSION_TTL_SEC}`,
  ];
  if (isHttps) parts.push("Secure");
  return parts.join("; ");
}

export function buildClearSessionCookie(isHttps = false) {
  const parts = [
    `${COOKIE_NAME}=`,
    "Path=/",
    "HttpOnly",
    "SameSite=Lax",
    "Max-Age=0",
  ];
  if (isHttps) parts.push("Secure");
  return parts.join("; ");
}

export async function verifyGoogleIdToken(idToken) {
  const token = String(idToken || "").trim();
  if (!token) throw new Error("ไม่พบ Google credential");
  const expectedClientId = String(process.env.GOOGLE_CLIENT_ID || "").trim();
  if (!expectedClientId) throw new Error("ระบบยังไม่ได้ตั้งค่า GOOGLE_CLIENT_ID");

  const res = await fetch(
    `https://oauth2.googleapis.com/tokeninfo?id_token=${encodeURIComponent(token)}`,
    { method: "GET" }
  );
  if (!res.ok) {
    throw new Error("Google ID Token ไม่ถูกต้องหรือหมดอายุ");
  }
  const data = await res.json();
  if (data.aud !== expectedClientId) {
    throw new Error("Client ID ของ Google Token ไม่ตรงกับระบบนี้");
  }
  if (data.email_verified !== "true" && data.email_verified !== true) {
    throw new Error("บัญชี Google นี้ยังไม่ได้ยืนยันอีเมล");
  }
  if (!data.email) {
    throw new Error("ไม่พบอีเมลใน Google Token");
  }
  return {
    email: String(data.email).trim().toLowerCase(),
    name: String(data.name || data.email).trim(),
    picture: String(data.picture || "").trim(),
    isAdmin: isAdminEmail(data.email),
  };
}

// =========================================================================
// Server-side Privacy Redaction for Unauthenticated / Non-Admin Viewers
// =========================================================================

const PHONE_IN_TEXT_RE = /(?:\+\d[\d\s-]{6,18}\d|(?<!\d)(?:\+66|66|0)\d[\d\s-]{6,12}\d)/g;
const EMAIL_IN_TEXT_RE = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g;
const IPV4_IN_TEXT_RE = /\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b/g;
const THAI_PERSON_RE = /(?<!ขอบ|พระ|บุญ)คุณ(?!ภาพ|สมบัติ|ค่า|ลักษณะ|ประโยชน์|ธรรม|วุฒิ|ลูกค้า|ผู้|ท่าน|ครู|หมอ|แม่|พ่อ|ยาย|ตา|ปู่|ย่า|ครับ|ค่ะ|คะ|นะ|ที่|จะ|ได้|ต้อง|สามารถ|มี|เป็น|ใช้|อยาก|บอก|ถาม|โทร|ลอง|กด|เข้า|ดู|ส่ง|ไม่|ก็|ยัง|เอง|เลย|ด้วย|มาก|ดี)(?:(?!(?:โทร|ติด|สอบ|ถาม|แจ้ง|คุย|เรื่อง|ให้|ฝาก|กับ|และ|หรือ|ว่า|ที่|เป็น|จาก|มา|ไป|จะ|ได้|อยู่|ไม่|ครับ|ค่ะ|คะ|นะ|\s))[\u0E01-\u0E4E]){2,10}/g;
const COMMON_THAI_WORDS = /^(?:สินค้า|บริการ|โปรแกรม|ระบบ|รายงาน|บัญชี|ภาษี|พิมพ์|ใบเสร็จ|ใบกำกับ|ลูกค้า|บริษัท|สต็อก|ราคา|รุ่น|ขาย|ซื้อ|เงิน|ยอด|เดือน|ปี|วัน)$/;

function escapeRegExp(str) {
  return String(str).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function replaceTerms(text, terms, replacement) {
  if (!text || !terms || terms.length === 0) return text;
  let out = text;
  const sorted = Array.from(new Set(terms.map((t) => String(t || "").trim()).filter((t) => t.length >= 2)))
    .sort((a, b) => b.length - a.length);
  for (const term of sorted) {
    const esc = escapeRegExp(term);
    // Always redact "คุณ<Name>" regardless of length
    out = out.replace(new RegExp(`คุณ\\s*${esc}`, "g"), replacement);
    if (term.length >= 5 && !COMMON_THAI_WORDS.test(term)) {
      out = out.split(term).join(replacement);
    } else if (term.length >= 3 && !COMMON_THAI_WORDS.test(term)) {
      out = out.replace(
        new RegExp(`(^|[\\s:(\\[\\"'•\\-])${esc}(?=$|[\\s.,:;)\\]\\"'•\\-]|ครับ|ค่ะ|คะ|นะ)`, "g"),
        `$1${replacement}`
      );
    }
  }
  return out;
}

export function sanitizeFreeTextForViewer(text, ctx = {}) {
  if (!text) return text;
  const { publicTrunkNumber = "", secretNames = [], secretCompanies = [], secretActors = [] } = ctx || {};
  let out = String(text);
  out = replaceTerms(out, secretActors, "เจ้าหน้าที่");
  out = replaceTerms(out, secretCompanies, "บริษัท (ปกปิด)");
  out = replaceTerms(out, secretNames, "ลูกค้า (ปกปิด)");
  out = maskPhonesInText(out, publicTrunkNumber);
  out = out
    .replace(EMAIL_IN_TEXT_RE, "[อีเมลปกปิด]")
    .replace(IPV4_IN_TEXT_RE, "[IP ปกปิด]")
    .replace(/(ชื่อลูกค้า:\s*).+/g, "$1ลูกค้า (ปกปิด)")
    .replace(/(บริษัท:\s*).+/g, "$1บริษัท (ปกปิด)")
    .replace(/(\[ปิดเคสโดย\s+)[^\]]+(\])/g, "$1เจ้าหน้าที่$2")
    .replace(THAI_PERSON_RE, "ลูกค้า (ปกปิด)")
    .replace(/(?:คุณ\s*)?(?:ลูกค้า\s*)+(\(ปกปิด\))/g, "ลูกค้า (ปกปิด)")
    .replace(/(?:ร้าน|บริษัท)\s*บริษัท\s*\(ปกปิด\)/g, "บริษัท (ปกปิด)");
  return out;
}

export function collectSecretTerms(state = {}) {
  const names = new Set();
  const companies = new Set();
  const actors = new Set();

  for (const l of state.lines || []) {
    if (l.remoteName) names.add(l.remoteName);
    if (l.crmName) names.add(l.crmName);
    if (l.crmCompany) companies.add(l.crmCompany);
  }
  for (const c of state.crm?.customers || []) {
    if (c.name) names.add(c.name);
    if (c.company) companies.add(c.company);
  }
  for (const t of state.crm?.tickets || []) {
    if (t.customerName) names.add(t.customerName);
    if (t.company) companies.add(t.company);
    if (t.acknowledgedBy) actors.add(t.acknowledgedBy);
    if (t.closedBy) actors.add(t.closedBy);
  }
  for (const c of state.callers || []) {
    for (const s of c.sessions || []) {
      if (s.analysis?.callerName) names.add(s.analysis.callerName);
      if (s.analysis?.callerCompany) companies.add(s.analysis.callerCompany);
    }
  }
  for (const log of state.callLogs || []) {
    if (log.analysis?.callerName) names.add(log.analysis.callerName);
    if (log.analysis?.callerCompany) companies.add(log.analysis.callerCompany);
  }

  return {
    secretNames: Array.from(names),
    secretCompanies: Array.from(companies),
    secretActors: Array.from(actors),
  };
}

function normalizeDigits(str) {
  return String(str || "").replace(/\D/g, "");
}

export function maskPhonesInText(text, publicTrunkNumber = "") {
  if (!text) return text;
  const publicDigits = normalizeDigits(publicTrunkNumber);
  return String(text).replace(PHONE_IN_TEXT_RE, (match) => {
    const digits = normalizeDigits(match);
    if (publicDigits && (digits === publicDigits || digits === `66${publicDigits.replace(/^0/, "")}`)) {
      return match;
    }
    return "•••-•••-••••";
  });
}

function makePhoneAliasMap(state) {
  const map = new Map();
  let counter = 1;
  const alias = (phone) => {
    const key = String(phone || "").trim();
    if (!key) return "";
    if (!map.has(key)) {
      map.set(key, `•••-•••-••#${String(counter++).padStart(2, "0")}`);
    }
    return map.get(key);
  };

  for (const l of state.lines || []) if (l.remoteNumber) alias(l.remoteNumber);
  for (const c of state.callers || []) if (c.phone) alias(c.phone);
  for (const h of state.history || []) if (h.number) alias(h.number);
  for (const log of state.callLogs || []) if (log.phone) alias(log.phone);
  for (const c of state.crm?.customers || []) if (c.phone) alias(c.phone);
  for (const t of state.crm?.tickets || []) {
    if (t.phone) alias(t.phone);
    if (t.callbackPhone) alias(t.callbackPhone);
    if (t.callerPhone) alias(t.callerPhone);
  }
  return alias;
}

export function sanitizeTicketForViewer(t, publicTrunkNumber = "", aliasFn = null, ctx = null) {
  if (!t) return t;
  const maskSinglePhone = (p) => {
    if (!p) return "";
    if (aliasFn) return aliasFn(p);
    const masked = maskPhonesInText(p, publicTrunkNumber);
    return masked && masked !== p ? masked : "•••-•••-••••";
  };
  const mergedCtx = {
    publicTrunkNumber,
    secretNames: [...(ctx?.secretNames || []), ...(t.customerName ? [t.customerName] : [])],
    secretCompanies: [...(ctx?.secretCompanies || []), ...(t.company ? [t.company] : [])],
    secretActors: [...(ctx?.secretActors || []), ...(t.acknowledgedBy ? [t.acknowledgedBy] : []), ...(t.closedBy ? [t.closedBy] : [])],
  };
  return {
    ...t,
    phone: maskSinglePhone(t.phone),
    callbackPhone: t.callbackPhone ? maskSinglePhone(t.callbackPhone) : t.callbackPhone,
    callerPhone: t.callerPhone ? maskSinglePhone(t.callerPhone) : t.callerPhone,
    customerName: t.customerName ? "ลูกค้า (ปกปิด)" : "",
    company: t.company ? "บริษัท (ปกปิด)" : "",
    subject: sanitizeFreeTextForViewer(t.subject, mergedCtx),
    description: sanitizeFreeTextForViewer(t.description, mergedCtx),
    topics: Array.isArray(t.topics)
      ? t.topics.map((item) => sanitizeFreeTextForViewer(item, mergedCtx))
      : undefined,
    callbackAdvice: t.callbackAdvice ? sanitizeFreeTextForViewer(t.callbackAdvice, mergedCtx) : "",
    aiCallbackAdvice: t.aiCallbackAdvice ? sanitizeFreeTextForViewer(t.aiCallbackAdvice, mergedCtx) : undefined,
    acknowledgedBy: t.acknowledgedBy ? "เจ้าหน้าที่" : null,
    closedBy: t.closedBy ? "เจ้าหน้าที่" : null,
  };
}

function sanitizeAnalysis(a, ctx = {}) {
  if (!a) return a;
  return {
    ...a,
    callerName: a.callerName ? "ลูกค้า (ปกปิด)" : "",
    callerCompany: a.callerCompany ? "บริษัท (ปกปิด)" : "",
    topic: sanitizeFreeTextForViewer(a.topic, ctx),
    summary: sanitizeFreeTextForViewer(a.summary, ctx),
    resolutionSteps: sanitizeFreeTextForViewer(a.resolutionSteps, ctx),
    followUpAction: sanitizeFreeTextForViewer(a.followUpAction, ctx),
    callbackAdvice: sanitizeFreeTextForViewer(a.callbackAdvice, ctx),
    followUpMessage: sanitizeFreeTextForViewer(a.followUpMessage, ctx),
    aiCoachingTip: sanitizeFreeTextForViewer(a.aiCoachingTip, ctx),
  };
}

function sanitizeMessages(msgs, ctx = {}) {
  return (msgs || []).map((m) => ({
    ...m,
    text: sanitizeFreeTextForViewer(m.text, ctx),
  }));
}

export function sanitizeStateForViewer(state) {
  const aliasPhone = makePhoneAliasMap(state);
  const publicTrunkNumber = state.trunk?.user || "";
  const secretTerms = collectSecretTerms(state);
  const ctx = { publicTrunkNumber, ...secretTerms };

  return {
    ...state,
    jarvisUrl: "internal (ปกปิด)",
    trunk: {
      ...state.trunk,
      host: "SIP Trunk (ปกปิด)",
      ip: null,
      user: publicTrunkNumber,
      error: state.trunk?.error ? sanitizeFreeTextForViewer(state.trunk.error, ctx) : null,
    },
    lines: (state.lines || []).map((l) => ({
      ...l,
      remoteNumber: l.remoteNumber ? aliasPhone(l.remoteNumber) : "",
      remoteName: "",
      crmName: l.crmName ? "ลูกค้า (ปกปิด)" : "",
      crmCompany: l.crmCompany ? "บริษัท (ปกปิด)" : "",
      aiTranscript: sanitizeFreeTextForViewer(l.aiTranscript, ctx),
      aiPartial: sanitizeFreeTextForViewer(l.aiPartial, ctx),
      aiStatus: sanitizeFreeTextForViewer(l.aiStatus, ctx),
      liveMessages: (l.liveMessages || []).map((m) => ({
        ...m,
        text: sanitizeFreeTextForViewer(m.text, ctx),
      })),
    })),
    history: (state.history || []).map((h) => ({
      ...h,
      number: aliasPhone(h.number),
    })),
    callers: (state.callers || []).map((c) => ({
      ...c,
      phone: aliasPhone(c.phone),
      sessions: (c.sessions || []).map((s) => ({
        ...s,
        messages: sanitizeMessages(s.messages, ctx),
        analysis: sanitizeAnalysis(s.analysis, ctx),
      })),
    })),
    callLogs: (state.callLogs || []).map((log) => ({
      ...log,
      phone: aliasPhone(log.phone),
      messages: sanitizeMessages(log.messages, ctx),
      analysis: sanitizeAnalysis(log.analysis, ctx),
    })),
    crm: state.crm
      ? {
          ...state.crm,
          customers: (state.crm.customers || []).map((c) => ({
            ...c,
            phone: aliasPhone(c.phone),
            name: c.name ? "ลูกค้า (ปกปิด)" : "",
            company: c.company ? "บริษัท (ปกปิด)" : "",
            tags: (c.tags || []).map((tag) => sanitizeFreeTextForViewer(tag, ctx)),
            notes: c.notes ? "โน้ตส่วนตัวลูกค้า (สงวนสิทธิ์ผู้ดูแล)" : "",
          })),
          tickets: (state.crm.tickets || []).map((t) => sanitizeTicketForViewer(t, publicTrunkNumber, aliasPhone, ctx)),
        }
      : undefined,
  };
}
