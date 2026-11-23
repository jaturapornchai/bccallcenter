#!/usr/bin/env node
/**
 * Dual-channel call recording analyzer for Dev on the Fly (BC Call Center, d:\yalecom).
 * An AI reviewer cannot listen to audio, so this turns data/recordings/<sessionId>.wav
 * (left = caller, right = AI, written by lib/call-recorder.mjs) into numbers it can read:
 * levels, speech turns, AI response latency, barge-in reaction, talk-over and dead air.
 * Speech spans come from Silero VAD (scripts/wav-transcribe.py), the VAD the live agent takes its turns from;
 * without that Python pass the script falls back to its own level-based spans ("vadSource": "energy").
 * --transcribe adds a per-utterance Whisper transcript with the detected language of each turn.
 *
 * Usage: node scripts/wav-analyze.mjs <sessionId|path.wav> [--transcribe] [--device auto|cpu|cuda]
 */
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const RECORDINGS_DIR = path.join(ROOT, "data", "recordings");

const FRAME_SEC = 0.02;
const DIGITAL_SILENCE_DB = -90; // the recorder leaves zeros where no RTP arrived
const CLIP_LEVEL = 32000; // G.711 mu-law tops out at 32124
const MIN_BLIP_SEC = 0.06; // shorter bursts are clicks; dropped before merging so they cannot stretch a turn
const MERGE_GAP_SEC = 0.5; // Thai mid-sentence pauses run ~0.3 s (jarvis/config.py), so shorter gaps stay one utterance
const MIN_SPEECH_SEC = 0.16;
const DEAD_AIR_SEC = 3;
const ECHO_DROP_DB = 12; // "caller" sound this far under the caller's own speech level, while the AI talks, is line echo
const MAX_TIMELINE = 200;
// Flag limits: roughly where a caller on a phone line starts to notice. Tune them from real calls, not by taste.
const LIMITS = {
  latencyP50Sec: 2.0,
  latencyMaxSec: 5.0,
  bargeStopSec: 1.0,
  bargeMinCallerSec: 0.6, // shorter caller sounds over the AI are backchannels ("ครับ", "อืม"), not an interruption
  deadAirSec: 6.0,
  greetingSec: 3.0,
  clipPct: 0.5,
  callerQuietDb: -35,
  aiQuietDb: -30,
  aiLoudDb: -9,
};

const r2 = (v) => Math.round(v * 100) / 100;
const r1 = (v) => Math.round(v * 10) / 10;
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

function readWav(fp) {
  const buf = fs.readFileSync(fp);
  if (buf.length < 44 || buf.toString("ascii", 0, 4) !== "RIFF" || buf.toString("ascii", 8, 12) !== "WAVE") {
    throw new Error("not a RIFF/WAVE file");
  }
  let pos = 12;
  let fmt = null;
  let data = null;
  while (pos + 8 <= buf.length) {
    const id = buf.toString("ascii", pos, pos + 4);
    const size = buf.readUInt32LE(pos + 4);
    const body = pos + 8;
    if (id === "fmt ") {
      fmt = {
        format: buf.readUInt16LE(body),
        channels: buf.readUInt16LE(body + 2),
        sampleRate: buf.readUInt32LE(body + 4),
        bits: buf.readUInt16LE(body + 14),
      };
    } else if (id === "data") {
      data = buf.subarray(body, Math.min(body + size, buf.length));
      break;
    }
    pos = body + size + (size % 2);
  }
  if (!fmt || !data) throw new Error("WAV has no fmt/data chunk");
  if (fmt.format !== 1 || fmt.bits !== 16 || fmt.channels !== 2) {
    throw new Error(`expected 16-bit PCM stereo, got format=${fmt.format} bits=${fmt.bits} channels=${fmt.channels}`);
  }
  const n = Math.floor(data.length / 4);
  const left = new Int16Array(n);
  const right = new Int16Array(n);
  for (let i = 0; i < n; i++) {
    left[i] = data.readInt16LE(i * 4);
    right[i] = data.readInt16LE(i * 4 + 2);
  }
  return { sampleRate: fmt.sampleRate, left, right };
}

function frameLevels(pcm, frameLen) {
  const frames = Math.floor(pcm.length / frameLen);
  const db = new Float32Array(frames);
  let peak = 0;
  let clipped = 0;
  for (let f = 0; f < frames; f++) {
    let sum = 0;
    for (let i = f * frameLen, end = i + frameLen; i < end; i++) {
      const v = pcm[i];
      sum += v * v;
      const a = v < 0 ? -v : v;
      if (a > peak) peak = a;
      if (a >= CLIP_LEVEL) clipped++;
    }
    const rms = Math.sqrt(sum / frameLen) / 32768;
    db[f] = rms > 0 ? Math.max(-100, 20 * Math.log10(rms)) : -100;
  }
  return { db, peak, clipped };
}

function percentile(sorted, p) {
  return sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * p))];
}

/** Speech/no-speech level for one channel, from its own loudest and quietest frames. */
function speechThreshold(db) {
  const live = Array.from(db).filter((v) => v > DIGITAL_SILENCE_DB).sort((a, b) => a - b);
  if (live.length < 10) return null; // the channel never carried audio
  const loud = percentile(live, 0.98);
  const quiet = percentile(live, 0.1);
  // A TTS channel is digital silence between phrases, so its quietest live frames are word edges, not line noise.
  const hasLineNoise = live.length >= db.length * 0.8;
  const level = hasLineNoise ? Math.max(loud - 30, quiet + 8) : loud - 30;
  return { thresholdDb: clamp(level, -55, -25), noiseFloorDb: hasLineNoise ? quiet : null };
}

function speechSegments(db, thresholdDb) {
  const raw = [];
  let start = -1;
  for (let f = 0; f <= db.length; f++) {
    const on = f < db.length && db[f] >= thresholdDb;
    if (on && start < 0) start = f;
    if (!on && start >= 0) {
      raw.push([start * FRAME_SEC, f * FRAME_SEC]);
      start = -1;
    }
  }
  const merged = [];
  for (const [s, e] of raw) {
    if (e - s < MIN_BLIP_SEC) continue;
    const last = merged[merged.length - 1];
    if (last && s - last[1] <= MERGE_GAP_SEC) last[1] = e;
    else merged.push([s, e]);
  }
  return merged.filter(([s, e]) => e - s >= MIN_SPEECH_SEC);
}

/** Energy-average level in dBFS over [startSec, endSec). */
function levelBetween(db, startSec, endSec) {
  const from = Math.max(0, Math.floor(startSec / FRAME_SEC));
  const to = Math.min(db.length, Math.ceil(endSec / FRAME_SEC));
  if (to <= from) return -100;
  let power = 0;
  for (let f = from; f < to; f++) power += 10 ** (db[f] / 10);
  return 10 * Math.log10(power / (to - from));
}

function speechLevel(db, segments) {
  let power = 0;
  let frames = 0;
  for (const [s, e] of segments) {
    for (let f = Math.floor(s / FRAME_SEC), to = Math.min(db.length, Math.ceil(e / FRAME_SEC)); f < to; f++) {
      power += 10 ** (db[f] / 10);
      frames++;
    }
  }
  return frames ? 10 * Math.log10(power / frames) : null;
}

function analyzeChannel(pcm, sampleRate, vadSegments) {
  const frameLen = Math.round(sampleRate * FRAME_SEC);
  const { db, peak, clipped } = frameLevels(pcm, frameLen);
  const threshold = speechThreshold(db);
  const segments = vadSegments ?? (threshold ? speechSegments(db, threshold.thresholdDb) : []);
  const speechSec = segments.reduce((sum, [s, e]) => sum + (e - s), 0);
  const level = speechLevel(db, segments);
  return {
    db,
    segments,
    speechDb: level,
    summary: {
      speechSec: r1(speechSec),
      utterances: segments.length,
      speechDb: level === null ? null : r1(level),
      peakDb: peak ? r1(20 * Math.log10(peak / 32768)) : null,
      clipPct: r2((clipped / Math.max(1, pcm.length)) * 100),
      noiseFloorDb: threshold?.noiseFloorDb == null ? null : r1(threshold.noiseFloorDb),
    },
  };
}

const segmentAt = (segments, t) => segments.find(([s, e]) => t >= s && t < e);

/** Caller stops -> AI starts. Skipped when the AI was already talking or the caller spoke again first. */
function responseLatencies(caller, ai) {
  const out = [];
  caller.forEach(([, callerEnd], i) => {
    if (segmentAt(ai, callerEnd)) return;
    const nextCallerStart = caller[i + 1]?.[0] ?? Infinity;
    const reply = ai.find(([aiStart]) => aiStart >= callerEnd);
    if (!reply || reply[0] > nextCallerStart) return;
    out.push({ at: r1(callerEnd), sec: r2(reply[0] - callerEnd) });
  });
  return out;
}

/** Caller starts while the AI is mid-speech: how long did the AI keep talking? */
function bargeIns(caller, ai, callerDb, callerSpeechDb) {
  const out = [];
  for (const [callerStart, callerEnd] of caller) {
    const talking = ai.find(([s, e]) => callerStart > s + 0.3 && callerStart < e - 0.2);
    if (!talking) continue;
    const level = levelBetween(callerDb, callerStart, callerEnd);
    out.push({
      at: r1(callerStart),
      callerSec: r2(callerEnd - callerStart),
      aiKeptTalkingSec: r2(talking[1] - callerStart),
      callerDb: r1(level),
      likelyEcho: level < callerSpeechDb - ECHO_DROP_DB,
    });
  }
  return out;
}

/** AI starts while the caller is mid-speech: the AI cut the caller off. */
function aiTalkOvers(caller, ai, callerDb, callerSpeechDb) {
  const out = [];
  for (const [aiStart, aiEnd] of ai) {
    const speaking = caller.find(([s, e]) => aiStart > s + 0.3 && aiStart < e - 0.3);
    if (!speaking) continue;
    const level = levelBetween(callerDb, speaking[0], speaking[1]);
    out.push({
      at: r1(aiStart),
      aiSec: r2(aiEnd - aiStart),
      callerHadSpokenSec: r2(aiStart - speaking[0]),
      callerWentOnSec: r2(speaking[1] - aiStart),
      likelyEcho: level < callerSpeechDb - ECHO_DROP_DB,
    });
  }
  return out;
}

function deadAirs(timeline, durationSec) {
  const out = [];
  let lastEnd = 0;
  let lastWho = "start";
  for (const [who, s, e] of timeline) {
    if (s - lastEnd >= DEAD_AIR_SEC && lastWho !== "start") out.push({ at: r1(lastEnd), sec: r1(s - lastEnd), after: lastWho });
    if (e > lastEnd) {
      lastEnd = e;
      lastWho = who;
    }
  }
  if (durationSec - lastEnd >= DEAD_AIR_SEC && lastWho !== "start") {
    out.push({ at: r1(lastEnd), sec: r1(durationSec - lastEnd), after: lastWho, untilHangup: true });
  }
  return out;
}

function overlapSeconds(a, b) {
  let total = 0;
  for (const [as, ae] of a) for (const [bs, be] of b) total += Math.max(0, Math.min(ae, be) - Math.max(as, bs));
  return total;
}

function buildFlags({ caller, ai, latency, barge, talkOver, deadAir, greetingSec }) {
  const flags = [];
  if (!caller.segments.length) flags.push("no_caller_speech");
  if (!ai.segments.length) flags.push("no_ai_speech");
  if (latency.p50Sec > LIMITS.latencyP50Sec || latency.maxSec > LIMITS.latencyMaxSec) flags.push("ai_latency_high");
  if (barge.some((b) => !b.likelyEcho && b.callerSec >= LIMITS.bargeMinCallerSec && b.aiKeptTalkingSec > LIMITS.bargeStopSec)) {
    flags.push("barge_in_slow");
  }
  if (talkOver.some((t) => !t.likelyEcho)) flags.push("ai_talk_over");
  // Silence after the AI spoke is the caller thinking; only a caller left without an answer counts.
  if (deadAir.some((d) => d.after === "caller" && d.sec > LIMITS.deadAirSec)) flags.push("dead_air");
  if (greetingSec !== null && greetingSec > LIMITS.greetingSec) flags.push("greeting_slow");
  if (caller.summary.clipPct > LIMITS.clipPct || ai.summary.clipPct > LIMITS.clipPct) flags.push("clipping");
  if (caller.speechDb !== null && caller.speechDb < LIMITS.callerQuietDb) flags.push("caller_quiet");
  if (ai.speechDb !== null && ai.speechDb < LIMITS.aiQuietDb) flags.push("ai_quiet");
  if (ai.speechDb !== null && ai.speechDb > LIMITS.aiLoudDb) flags.push("ai_loud");
  return flags;
}

function analyze(wavPath, vad) {
  const { sampleRate, left, right } = readWav(wavPath);
  const durationSec = left.length / sampleRate;
  const caller = analyzeChannel(left, sampleRate, vad?.caller);
  const ai = analyzeChannel(right, sampleRate, vad?.ai);
  const callerSpeechDb = caller.speechDb ?? -100;

  const timeline = [
    ...caller.segments.map(([s, e]) => ["caller", s, e]),
    ...ai.segments.map(([s, e]) => ["ai", s, e]),
  ].sort((a, b) => a[1] - b[1]);

  const latencies = responseLatencies(caller.segments, ai.segments);
  const sortedLatency = latencies.map((l) => l.sec).sort((a, b) => a - b);
  const latency = {
    count: latencies.length,
    p50Sec: sortedLatency.length ? percentile(sortedLatency, 0.5) : 0,
    maxSec: sortedLatency.length ? sortedLatency[sortedLatency.length - 1] : 0,
    slowest: [...latencies].sort((a, b) => b.sec - a.sec).slice(0, 5),
  };
  const barge = bargeIns(caller.segments, ai.segments, caller.db, callerSpeechDb);
  const talkOver = aiTalkOvers(caller.segments, ai.segments, caller.db, callerSpeechDb);
  const deadAir = deadAirs(timeline, durationSec);
  const greetingSec = ai.segments.length ? r2(ai.segments[0][0]) : null;

  return {
    ok: true,
    wav: path.basename(wavPath),
    durationSec: r1(durationSec),
    vadSource: vad ? "silero" : "energy",
    caller: caller.summary,
    ai: ai.summary,
    greetingSec,
    overlapSec: r1(overlapSeconds(caller.segments, ai.segments)),
    latency,
    bargeIns: barge,
    aiTalkOvers: talkOver,
    deadAir,
    flags: buildFlags({ caller, ai, latency, barge, talkOver, deadAir, greetingSec }),
    limits: LIMITS,
    timeline: timeline.slice(0, MAX_TIMELINE).map(([who, s, e]) => `${who === "caller" ? "C" : "A"} ${r1(s)}-${r1(e)}`),
  };
}

function resolvePython() {
  // Same order as scripts/start-jarvis.ps1: the interpreter that already has faster-whisper installed.
  const candidates = [
    process.env.JARVIS_PYTHON,
    path.join(ROOT, "jarvis", ".venv", "Scripts", "python.exe"),
    "D:\\bctest01\\.venv\\Scripts\\python.exe",
  ].filter(Boolean);
  return candidates.find((p) => fs.existsSync(p)) || "python";
}

/** Silero VAD spans (always) and the Whisper transcript (when asked) from scripts/wav-transcribe.py. */
function runSpeechPass(wavPath, { withTranscript, device }) {
  const script = path.join(ROOT, "scripts", "wav-transcribe.py");
  const scriptArgs = ["-X", "utf8", script, wavPath, "--device", device, ...(withTranscript ? [] : ["--vad-only"])];
  const run = spawnSync(resolvePython(), scriptArgs, { encoding: "utf8", maxBuffer: 32 * 1024 * 1024, timeout: 20 * 60_000 });
  if (run.status !== 0) {
    return { error: String(run.stderr || run.error?.message || "speech pass failed").trim().slice(-600) };
  }
  return JSON.parse(run.stdout);
}

function mergeTranscript(speech) {
  const lines = [
    ...speech.caller.map((u) => ({ who: "caller", ...u })),
    ...speech.ai.map((u) => ({ who: "ai", ...u })),
  ].sort((a, b) => a.start - b.start);
  return { transcriptModel: speech.model, transcriptDevice: speech.device, transcriptSec: speech.elapsedSec, transcript: lines };
}

const args = process.argv.slice(2);
const optionValue = (name, fallback) => (args.includes(name) ? args[args.indexOf(name) + 1] : fallback);
const device = optionValue("--device", "auto");
const target = args.find((a, i) => !a.startsWith("--") && args[i - 1] !== "--device");
if (!target || !["auto", "cpu", "cuda"].includes(device)) {
  console.error("Usage: node scripts/wav-analyze.mjs <sessionId|path.wav> [--transcribe] [--device auto|cpu|cuda]");
  process.exit(64);
}
const wavPath = target.toLowerCase().endsWith(".wav") ? path.resolve(target) : path.join(RECORDINGS_DIR, `${target}.wav`);

try {
  if (!fs.existsSync(wavPath)) throw new Error(`recording not found: ${path.basename(wavPath)}`);
  const withTranscript = args.includes("--transcribe");
  const speech = runSpeechPass(wavPath, { withTranscript, device });
  const report = analyze(wavPath, speech.error ? null : speech.vad);
  if (speech.error) report.speechPassError = speech.error;
  else if (withTranscript) Object.assign(report, mergeTranscript(speech));
  console.log(JSON.stringify(report, null, 1));
} catch (err) {
  console.log(JSON.stringify({ ok: false, error: err.message }));
  process.exit(1);
}
