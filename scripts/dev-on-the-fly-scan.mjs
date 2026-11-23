#!/usr/bin/env node
/**
 * Dev-on-the-Fly Call Scanner & State Tracker for BC Call Center (d:\yalecom)
 * Used by the hourly Dev-on-the-Fly review (docs/dev-on-the-fly.md) to discover unreviewed calls
 * with WAV recordings + transcripts + DeepSeek QA, check line idle safety before deploy,
 * and record review outcomes without leaking real caller phone numbers.
 *
 *   --limit N [--lock] [--recorded-only]   list unreviewed calls; --lock claims the run so two reviews never
 *                             overlap; --recorded-only skips calls that have no WAV recording
 *   --jarvis-log <sessionId>  J.A.R.V.I.S log lines written during that call
 *   --check-idle              is any call live right now?
 *   --mark-done <ids> --changed <true|false> --summary "<text>"   record the outcome and release the lock
 *   --unlock                  release the lock without recording anything (aborted run)
 */
import fs from "node:fs";
import path from "node:path";

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1")), "..");
const DATA_DIR = path.join(ROOT, "data");
const MEMORY_PATH = path.join(DATA_DIR, "call-memory.json");
const RECORDINGS_DIR = path.join(DATA_DIR, "recordings");
const STATE_PATH = path.join(DATA_DIR, "dev-on-the-fly-state.json");
const LOG_PATH = path.join(DATA_DIR, "dev-on-the-fly-log.jsonl");
const LOCK_PATH = path.join(DATA_DIR, "dev-on-the-fly.lock");
const LOCK_STALE_MS = 100 * 60_000; // a run that died without --mark-done/--unlock must not block reviews forever
const JARVIS_LOG_DIR = path.join(ROOT, "jarvis", "logs");
// aioice logs every address pair it probes, ~35 lines per call that bury the conversation and carry IP addresses
const ICE_PROBE = /Connection\(\d+\) (?:Check CandidatePair|Could not bind)/;

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

function activeLock() {
  const lock = loadJson(LOCK_PATH, null);
  return lock?.at && Date.now() - new Date(lock.at).getTime() < LOCK_STALE_MS ? lock : null;
}

function releaseLock() {
  try {
    fs.unlinkSync(LOCK_PATH);
  } catch {}
}

function findSession(mem, sessionId) {
  for (const rec of Object.values(mem.callers || {})) {
    const session = (rec.sessions || []).find((s) => s?.id === sessionId);
    if (session) return session;
  }
  return null;
}

/** Log lines stamped inside [fromMs, toMs]; unstamped lines (tracebacks) follow the stamped line above them. */
function jarvisLogLines(fromMs, toMs) {
  if (!fs.existsSync(JARVIS_LOG_DIR)) return null;
  const files = fs
    .readdirSync(JARVIS_LOG_DIR)
    .filter((f) => f.startsWith("voice_agent.log"))
    .map((f) => ({ fp: path.join(JARVIS_LOG_DIR, f), mtime: fs.statSync(path.join(JARVIS_LOG_DIR, f)).mtimeMs }))
    .filter((f) => f.mtime >= fromMs)
    .sort((a, b) => a.mtime - b.mtime);
  const picked = [];
  for (const { fp } of files) {
    let inWindow = false;
    for (const line of fs.readFileSync(fp, "utf8").split(/\r?\n/)) {
      // J.A.R.V.I.S stamps in local time: "2026-11-23 13:53:01,123"
      const m = /^(\d{4})-(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d),(\d{3})/.exec(line);
      if (m) {
        const at = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6], +m[7]).getTime();
        inWindow = at >= fromMs && at <= toMs;
      }
      if (!inWindow || !line || ICE_PROBE.test(line)) continue;
      const stamp = m ? m[0] : ""; // kept out of the phone mask, which reads the date as a phone number
      picked.push((stamp + maskTextPhones(line.slice(stamp.length))).slice(0, 400));
    }
  }
  return picked;
}

/** State of every line on the running server, or null when it cannot be asked (down, or not answering in 3 s). */
function liveLineStates() {
  return new Promise((resolve) => {
    let socket = null;
    const finish = (states) => {
      clearTimeout(timer);
      socket?.close();
      resolve(states);
    };
    const timer = setTimeout(() => finish(null), 3000);
    try {
      socket = new WebSocket("ws://127.0.0.1:3000/ws/sip"); // the port restart-services.ps1 restarts
    } catch {
      return finish(null);
    }
    socket.onerror = () => finish(null);
    socket.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        if (message.type === "state") finish((message.data?.lines || []).map((line) => line.state));
      } catch {}
    };
  });
}

const args = process.argv.slice(2);
const flagIdx = (name) => args.indexOf(name);

if (args.includes("--unlock")) {
  releaseLock();
  console.log(JSON.stringify({ ok: true, unlocked: true }));
  process.exit(0);
}

// Mode 0: J.A.R.V.I.S log excerpt for one call (the log carries no Node session id, so the call's time window is used)
if (flagIdx("--jarvis-log") >= 0) {
  const session = findSession(loadJson(MEMORY_PATH, { callers: {} }), args[flagIdx("--jarvis-log") + 1] || "");
  if (!session) {
    console.log("session not found");
    process.exit(1);
  }
  const maxLines = flagIdx("--max-lines") >= 0 ? Math.max(20, Number(args[flagIdx("--max-lines") + 1]) || 150) : 150;
  const fromMs = new Date(session.startedAt).getTime() - 5_000;
  const toMs = (session.endedAt ? new Date(session.endedAt).getTime() : Date.now()) + 20_000; // the post-call QA lands late
  const lines = jarvisLogLines(fromMs, toMs);
  if (!lines) {
    console.log("no J.A.R.V.I.S log file yet (jarvis/logs/voice_agent.log)");
  } else if (lines.length > maxLines) {
    const half = Math.floor(maxLines / 2);
    console.log([...lines.slice(0, half), `... ${lines.length - half * 2} lines omitted ...`, ...lines.slice(-half)].join("\n"));
  } else {
    console.log(lines.length ? lines.join("\n") : "no J.A.R.V.I.S log lines in this call's time window");
  }
  process.exit(0);
}

// Mode 1: Check whether any call is currently active (safety gate before auto-deploy restart)
if (args.includes("--check-idle")) {
  // The running server knows its lines. The call memory is the fallback only: a session opened just as the server
  // was restarted is never closed there and kept every restart blocked for 35 minutes.
  const lineStates = await liveLineStates();
  if (lineStates) {
    const activeCalls = lineStates.filter((state) => state !== "idle").length;
    console.log(JSON.stringify({ idle: activeCalls === 0, activeCalls }, null, 2));
    process.exit(0);
  }
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
  releaseLock();
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

const recordedOnly = args.includes("--recorded-only"); // calls from before recording existed have no audio to review
const wantsLock = args.includes("--lock");
const heldLock = wantsLock ? activeLock() : null;
if (heldLock) {
  console.log(JSON.stringify({ ok: false, locked: true, lockedAt: heldLock.at, sessionIds: heldLock.sessionIds || [] }, null, 2));
  process.exit(0);
}

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
    if (!hasWav && (recordedOnly || msgCount < 2)) continue;
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
if (wantsLock && batch.length > 0) {
  fs.writeFileSync(LOCK_PATH, JSON.stringify({ at: new Date().toISOString(), sessionIds: batch.map((s) => s.sessionId) }), "utf8");
}
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
