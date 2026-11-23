#!/usr/bin/env node
/**
 * Dev-on-the-Fly Call Scanner & State Tracker for BC Call Center (d:\yalecom)
 * Used by the hourly Antigravity Scheduled Automation to discover unreviewed calls
 * with WAV recordings + transcripts + DeepSeek QA, check line idle safety before deploy,
 * and record review outcomes without leaking real caller phone numbers.
 */
import fs from "node:fs";
import path from "node:path";

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1")), "..");
const DATA_DIR = path.join(ROOT, "data");
const MEMORY_PATH = path.join(DATA_DIR, "call-memory.json");
const RECORDINGS_DIR = path.join(DATA_DIR, "recordings");
const STATE_PATH = path.join(DATA_DIR, "dev-on-the-fly-state.json");
const LOG_PATH = path.join(DATA_DIR, "dev-on-the-fly-log.jsonl");

function maskPhone(raw) {
  const s = String(raw || "");
  if (!s) return "";
  return s.replace(/\d{6,}/g, (m) => "*".repeat(Math.max(0, m.length - 4)) + m.slice(-4));
}

function maskTextPhones(text) {
  return String(text || "").replace(/(?:\+66|66|0)[\d][\d\s-]{6,12}\d/g, (m) => {
    const d = m.replace(/\D/g, "");
    return "XXX-XXX-" + d.slice(-4);
  });
}

function loadJson(fp, fallback) {
  try {
    if (fs.existsSync(fp)) return JSON.parse(fs.readFileSync(fp, "utf8"));
  } catch {}
  return fallback;
}

function saveState(state) {
  if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });
  const tmp = `${STATE_PATH}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(state, null, 2), "utf8");
  fs.renameSync(tmp, STATE_PATH);
}

const args = process.argv.slice(2);
const flagIdx = (name) => args.indexOf(name);

// Mode 1: Check whether any call is currently active (safety gate before auto-deploy restart)
if (args.includes("--check-idle")) {
  const mem = loadJson(MEMORY_PATH, { callers: {} });
  const now = Date.now();
  let activeCount = 0;
  for (const rec of Object.values(mem.callers || {})) {
    for (const s of rec.sessions || []) {
      const started = new Date(s.startedAt || 0).getTime();
      if (s.status === "active" && !s.endedAt && now - started < 35 * 60_000) {
        activeCount++;
      }
    }
  }
  console.log(JSON.stringify({ idle: activeCount === 0, activeCalls: activeCount }, null, 2));
  process.exit(0);
}

// Mode 2: Mark reviewed session IDs & append audit log
if (flagIdx("--mark-done") >= 0) {
  const idsArg = args[flagIdx("--mark-done") + 1] || "";
  const ids = idsArg.split(",").map((s) => s.trim()).filter(Boolean);
  const summary = flagIdx("--summary") >= 0 ? String(args[flagIdx("--summary") + 1] || "") : "";
  const changed = flagIdx("--changed") >= 0 ? args[flagIdx("--changed") + 1] === "true" : false;
  const state = loadJson(STATE_PATH, { reviewed: {}, lastRunAt: null, totalRuns: 0 });
  const nowIso = new Date().toISOString();
  for (const id of ids) {
    state.reviewed[id] = { reviewedAt: nowIso, changed, summary: summary.slice(0, 300) };
  }
  state.lastRunAt = nowIso;
  state.totalRuns = Number(state.totalRuns || 0) + 1;
  saveState(state);
  if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });
  fs.appendFileSync(
    LOG_PATH,
    JSON.stringify({ at: nowIso, sessionIds: ids, changed, summary: summary.slice(0, 600) }) + "\n",
    "utf8"
  );
  console.log(JSON.stringify({ ok: true, marked: ids.length, lastRunAt: nowIso }, null, 2));
  process.exit(0);
}

// Mode 3 (Default): Scan for unreviewed completed calls with WAV recordings / transcripts
const limitArg = flagIdx("--limit") >= 0 ? Number(args[flagIdx("--limit") + 1] || 5) : 5;
const maxItems = Number.isFinite(limitArg) && limitArg > 0 ? Math.min(limitArg, 10) : 5;

const mem = loadJson(MEMORY_PATH, { callers: {} });
const state = loadJson(STATE_PATH, { reviewed: {}, lastRunAt: null, totalRuns: 0 });
const reviewed = state.reviewed || {};

const candidates = [];
for (const rec of Object.values(mem.callers || {})) {
  for (const s of rec.sessions || []) {
    if (!s?.id || reviewed[s.id]) continue;
    if (s.status === "active" && !s.endedAt) continue;
    const wavPath = path.join(RECORDINGS_DIR, `${s.id}.wav`);
    const hasWav = fs.existsSync(wavPath);
    const msgCount = Array.isArray(s.messages) ? s.messages.length : 0;
    if (!hasWav && msgCount < 2) continue;
    const wavSizeKb = hasWav ? Math.round(fs.statSync(wavPath).size / 1024) : 0;
    candidates.push({
      sessionId: s.id,
      phoneMasked: maskPhone(rec.phone),
      startedAt: s.startedAt,
      endedAt: s.endedAt,
      durationSec: s.durationSec || s.recordingDurationSec || 0,
      hasRecording: hasWav,
      wavPath: hasWav ? wavPath : null,
      wavSizeKb,
      messageCount: msgCount,
      messages: (s.messages || []).slice(0, 40).map((m) => ({
        at: m.at,
        role: m.role,
        text: maskTextPhones(m.text),
      })),
      analysis: s.analysis
        ? {
            csatScore: s.analysis.csatScore,
            aiQualityScore: s.analysis.aiQualityScore,
            sentiment: s.analysis.sentiment,
            topic: maskTextPhones(s.analysis.topic || ""),
            summary: maskTextPhones(s.analysis.summary || ""),
            resolved: s.analysis.resolved,
            aiCoachingTip: maskTextPhones(s.analysis.aiCoachingTip || ""),
          }
        : null,
    });
  }
}

// Prioritize calls that actually have audio recordings, newest first
candidates.sort((a, b) => {
  if (a.hasRecording !== b.hasRecording) return a.hasRecording ? -1 : 1;
  return String(b.startedAt || "").localeCompare(String(a.startedAt || ""));
});

const batch = candidates.slice(0, maxItems);
console.log(
  JSON.stringify(
    {
      ok: true,
      scannedAt: new Date().toISOString(),
      lastRunAt: state.lastRunAt || null,
      totalUnreviewed: candidates.length,
      batchCount: batch.length,
      sessions: batch,
    },
    null,
    2
  )
);
