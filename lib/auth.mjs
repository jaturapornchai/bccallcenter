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

export function isAdminEmail(email) {
  const admin = String(process.env.ADMIN_EMAIL || "").trim().toLowerCase();
  const candidate = String(email || "").trim().toLowerCase();
  return Boolean(admin && candidate && candidate === admin);
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
    // Re-evaluate admin status dynamically against current ADMIN_EMAIL env var
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

const PHONE_IN_TEXT_RE = /(?:\+66|66|0)[\d][\d\s-]{6,12}\d/g;

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
  for (const t of state.crm?.tickets || []) if (t.phone) alias(t.phone);
  return alias;
}

function sanitizeAnalysis(a, publicTrunkNumber = "") {
  if (!a) return a;
  return {
    ...a,
    topic: maskPhonesInText(a.topic, publicTrunkNumber),
    summary: maskPhonesInText(a.summary, publicTrunkNumber),
    resolutionSteps: maskPhonesInText(a.resolutionSteps, publicTrunkNumber),
    followUpAction: maskPhonesInText(a.followUpAction, publicTrunkNumber),
    aiCoachingTip: maskPhonesInText(a.aiCoachingTip, publicTrunkNumber),
  };
}

function sanitizeMessages(msgs, publicTrunkNumber = "") {
  return (msgs || []).map((m) => ({
    ...m,
    text: maskPhonesInText(m.text, publicTrunkNumber),
  }));
}

export function sanitizeStateForViewer(state) {
  const aliasPhone = makePhoneAliasMap(state);
  const publicTrunkNumber = state.trunk?.user || "";

  return {
    ...state,
    trunk: {
      ...state.trunk,
      host: "SIP Trunk (ปกปิด)",
      ip: null,
      user: publicTrunkNumber,
      error: state.trunk?.error ? maskPhonesInText(state.trunk.error, publicTrunkNumber) : null,
    },
    lines: (state.lines || []).map((l) => ({
      ...l,
      remoteNumber: l.remoteNumber ? aliasPhone(l.remoteNumber) : "",
      remoteName: "",
      crmName: l.crmName ? "ลูกค้า (ปกปิด)" : "",
      crmCompany: l.crmCompany ? "บริษัท (ปกปิด)" : "",
      aiTranscript: maskPhonesInText(l.aiTranscript, publicTrunkNumber),
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
        messages: sanitizeMessages(s.messages, publicTrunkNumber),
        analysis: sanitizeAnalysis(s.analysis, publicTrunkNumber),
      })),
    })),
    callLogs: (state.callLogs || []).map((log) => ({
      ...log,
      phone: aliasPhone(log.phone),
      messages: sanitizeMessages(log.messages, publicTrunkNumber),
      analysis: sanitizeAnalysis(log.analysis, publicTrunkNumber),
    })),
    crm: state.crm
      ? {
          ...state.crm,
          customers: (state.crm.customers || []).map((c) => ({
            ...c,
            phone: aliasPhone(c.phone),
            name: c.name ? "ลูกค้า (ปกปิด)" : "",
            company: c.company ? "บริษัท (ปกปิด)" : "",
            tags: (c.tags || []).map((tag) => maskPhonesInText(tag, publicTrunkNumber)),
            notes: maskPhonesInText(c.notes, publicTrunkNumber),
          })),
          tickets: (state.crm.tickets || []).map((t) => ({
            ...t,
            phone: t.phone ? aliasPhone(t.phone) : "",
            customerName: t.customerName ? "ลูกค้า (ปกปิด)" : "",
            company: t.company ? "บริษัท (ปกปิด)" : "",
            subject: maskPhonesInText(t.subject, publicTrunkNumber),
            description: maskPhonesInText(t.description, publicTrunkNumber),
          })),
        }
      : undefined,
  };
}
