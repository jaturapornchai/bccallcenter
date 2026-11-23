import fs from "node:fs";
import path from "node:path";
import { hasRecordingFile } from "./call-recorder.mjs";

const DATA_DIR = path.resolve(process.cwd(), "data");
const STORE_PATH = path.join(DATA_DIR, "call-memory.json");

export function normalizePhone(num) {
  const raw = String(num || "").trim();
  const digits = raw.replace(/[^\d+]/g, "");
  if (digits.startsWith("+66")) return "0" + digits.slice(3);
  if (digits.startsWith("66") && digits.length >= 10) return "0" + digits.slice(2);
  return digits || raw;
}

export class CallMemoryStore {
  constructor() {
    this.callers = new Map();
    this.load();
  }

  load() {
    try {
      if (!fs.existsSync(STORE_PATH)) return;
      const raw = fs.readFileSync(STORE_PATH, "utf8");
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === "object" && parsed.callers) {
        for (const [phone, rec] of Object.entries(parsed.callers)) {
          this.callers.set(phone, rec);
        }
      }
    } catch (err) {
      console.warn("[CallMemory] Failed to load:", err.message);
    }
  }

  save() {
    try {
      if (!fs.existsSync(DATA_DIR)) {
        fs.mkdirSync(DATA_DIR, { recursive: true });
      }
      const obj = { callers: Object.fromEntries(this.callers.entries()) };
      fs.writeFileSync(STORE_PATH, JSON.stringify(obj, null, 2), "utf8");
    } catch (err) {
      console.warn("[CallMemory] Failed to save:", err.message);
    }
  }

  getCaller(phone) {
    const key = normalizePhone(phone);
    return key ? this.callers.get(key) || null : null;
  }

  getSession(phone, sessionId) {
    const rec = this.getCaller(phone);
    if (!rec) return null;
    return rec.sessions.find((s) => s.id === sessionId) || null;
  }

  startSession(phone, direction) {
    const key = normalizePhone(phone);
    if (!key) return null;
    const now = new Date().toISOString();
    let rec = this.callers.get(key);
    if (!rec) {
      rec = {
        phone: key,
        totalCalls: 0,
        lastCallAt: now,
        lastDirection: direction,
        sessions: [],
      };
      this.callers.set(key, rec);
    }
    rec.totalCalls += 1;
    rec.lastCallAt = now;
    rec.lastDirection = direction;
    const session = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      startedAt: now,
      endedAt: null,
      durationSec: 0,
      status: "active",
      direction,
      messages: [],
      analysisStatus: "idle", // idle | analyzing | done | error
      analysis: null,
    };
    rec.sessions.unshift(session);
    if (rec.sessions.length > 30) rec.sessions.length = 30;
    this.save();
    return { caller: rec, sessionId: session.id };
  }

  appendMessage(phone, sessionId, role, text) {
    const key = normalizePhone(phone);
    const clean = String(text || "").trim();
    if (!key || !clean) return;
    const rec = this.callers.get(key);
    if (!rec) return;
    const session = rec.sessions.find((s) => s.id === sessionId) || rec.sessions[0];
    if (!session) return;

    const last = session.messages[session.messages.length - 1];
    if (last && last.role === role && last.text === clean) return;

    session.messages.push({
      at: new Date().toISOString(),
      role, // 'caller' | 'ai' | 'agent'
      text: clean,
    });
    if (session.messages.length > 100) {
      session.messages = session.messages.slice(-100);
    }
    this.save();
  }

  endSession(phone, sessionId, durationSec = 0, status = "answered", recInfo = null) {
    const key = normalizePhone(phone);
    if (!key) return null;
    const rec = this.callers.get(key);
    if (!rec) return null;
    const session = rec.sessions.find((s) => s.id === sessionId);
    if (session) {
      session.endedAt = new Date().toISOString();
      session.durationSec = durationSec;
      session.status = status;
      if (recInfo) {
        session.hasRecording = true;
        session.recordingDurationSec = recInfo.durationSec || durationSec;
      }
      this.save();
      return session;
    }
    return null;
  }

  setSessionAnalysis(phone, sessionId, analysisStatus, analysis = null) {
    const session = this.getSession(phone, sessionId);
    if (!session) return;
    session.analysisStatus = analysisStatus;
    if (analysis !== undefined) {
      session.analysis = analysis;
    }
    this.save();
  }

  buildAiContextPrompt(phone, currentSessionId) {
    const rec = this.getCaller(phone);
    if (!rec) return null;
    const prevSessions = rec.sessions.filter((s) => s.id !== currentSessionId && s.messages.length > 0);
    if (prevSessions.length === 0) return null;

    const latestPrev = prevSessions[0];
    const summaryPart = latestPrev.analysis?.summary
      ? `สรุปครั้งก่อน: ${latestPrev.analysis.summary}`
      : latestPrev.messages
          .slice(-6)
          .map((m) => `${m.role === "caller" ? "ลูกค้า" : "J.A.R.V.I.S"}: ${m.text}`)
          .join(" | ");

    const prompt = `[ประวัติลูกค้าเบอร์ ${rec.phone} โทรมาแล้ว ${rec.totalCalls} ครั้ง ล่าสุด ${latestPrev.startedAt.slice(0, 16)}] ${summaryPart} (กรุณาทักทายและคุยต่อเนื่องจากเรื่องเดิม)`;
    return prompt.slice(0, 480);
  }

  listRecentCallers(limit = 25) {
    return Array.from(this.callers.values())
      .sort((a, b) => String(b.lastCallAt).localeCompare(String(a.lastCallAt)))
      .slice(0, limit);
  }

  listCallLogs(limit = 40) {
    const logs = [];
    for (const rec of this.callers.values()) {
      for (const s of rec.sessions) {
        logs.push({
          phone: rec.phone,
          callerTotalCalls: rec.totalCalls,
          sessionId: s.id,
          startedAt: s.startedAt,
          endedAt: s.endedAt,
          durationSec: s.durationSec || 0,
          status: s.status || "answered",
          direction: s.direction || "inbound",
          messages: s.messages || [],
          hasRecording: Boolean(s.hasRecording || hasRecordingFile(s.id)),
          recordingDurationSec: s.recordingDurationSec || s.durationSec || 0,
          analysisStatus: s.analysisStatus || "idle",
          analysis: s.analysis || null,
        });
      }
    }
    logs.sort((a, b) => String(b.startedAt).localeCompare(String(a.startedAt)));
    return logs.slice(0, limit);
  }
}
