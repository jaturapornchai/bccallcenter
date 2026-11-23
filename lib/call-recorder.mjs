import fs from "node:fs";
import path from "node:path";
import { decodeMuLawSample } from "./g711.mjs";

export const RECORDINGS_DIR = path.resolve(process.cwd(), "data", "recordings");
const SAMPLE_RATE = 8000;
const CHANNELS = 2; // Left = Caller (inbound RTP), Right = AI / Agent (outbound RTP)
const BITS_PER_SAMPLE = 16;
const MAX_RECORDING_SECONDS = 30 * 60; // Cap single call at 30 mins (~57 MB raw)
const MAX_FILES_KEPT = 200;

function ensureDir() {
  if (!fs.existsSync(RECORDINGS_DIR)) {
    fs.mkdirSync(RECORDINGS_DIR, { recursive: true });
  }
}

export function sanitizeSessionId(sessionId) {
  const s = String(sessionId || "").trim();
  return /^[a-zA-Z0-9_-]{6,64}$/.test(s) ? s : null;
}

export function getRecordingPath(sessionId) {
  const safe = sanitizeSessionId(sessionId);
  if (!safe) return null;
  return path.join(RECORDINGS_DIR, `${safe}.wav`);
}

export function hasRecordingFile(sessionId) {
  const fp = getRecordingPath(sessionId);
  return Boolean(fp && fs.existsSync(fp));
}

function buildWavHeader(dataBytes) {
  const header = Buffer.alloc(44);
  const byteRate = SAMPLE_RATE * CHANNELS * (BITS_PER_SAMPLE / 8);
  const blockAlign = CHANNELS * (BITS_PER_SAMPLE / 8);
  header.write("RIFF", 0);
  header.writeUInt32LE(36 + dataBytes, 4);
  header.write("WAVE", 8);
  header.write("fmt ", 12);
  header.writeUInt32LE(16, 16); // PCM chunk size
  header.writeUInt16LE(1, 20); // PCM format
  header.writeUInt16LE(CHANNELS, 22);
  header.writeUInt32LE(SAMPLE_RATE, 24);
  header.writeUInt32LE(byteRate, 28);
  header.writeUInt16LE(blockAlign, 32);
  header.writeUInt16LE(BITS_PER_SAMPLE, 34);
  header.write("data", 36);
  header.writeUInt32LE(dataBytes, 40);
  return header;
}

function pruneOldRecordings() {
  try {
    if (!fs.existsSync(RECORDINGS_DIR)) return;
    const files = fs
      .readdirSync(RECORDINGS_DIR)
      .filter((f) => f.endsWith(".wav"))
      .map((f) => {
        const fp = path.join(RECORDINGS_DIR, f);
        try {
          return { fp, mtime: fs.statSync(fp).mtimeMs };
        } catch {
          return null;
        }
      })
      .filter(Boolean)
      .sort((a, b) => b.mtime - a.mtime);
    for (let i = MAX_FILES_KEPT; i < files.length; i++) {
      try {
        fs.unlinkSync(files[i].fp);
      } catch {}
    }
  } catch {}
}

/**
 * Time-aligned Dual-Channel 8kHz 16-bit Stereo WAV Recorder.
 * Left channel = Caller voice (inbound PCMU), Right channel = J.A.R.V.I.S / Agent voice (outbound PCMU).
 */
export class CallRecorder {
  constructor(sessionId) {
    this.sessionId = sanitizeSessionId(sessionId);
    this.startedAt = Date.now();
    this.closed = false;
    this.callerChunks = []; // { offsetSamples, pcm: Int16Array }
    this.agentChunks = []; // { offsetSamples, pcm: Int16Array }
    this.maxSampleIndex = 0;
  }

  _decodePcmu(pcmuBuf) {
    const out = new Int16Array(pcmuBuf.length);
    for (let i = 0; i < pcmuBuf.length; i++) {
      out[i] = decodeMuLawSample(pcmuBuf[i]);
    }
    return out;
  }

  _append(targetList, pcmuBuf) {
    if (this.closed || !this.sessionId || !pcmuBuf || pcmuBuf.length === 0) return;
    const elapsedSec = (Date.now() - this.startedAt) / 1000;
    if (elapsedSec > MAX_RECORDING_SECONDS) return;
    const nowSample = Math.max(0, Math.round(elapsedSec * SAMPLE_RATE));
    // Keep sequential frames contiguous if within 80ms jitter window, otherwise jump to wall clock
    const last = targetList.length > 0 ? targetList[targetList.length - 1] : null;
    const lastEnd = last ? last.offsetSamples + last.pcm.length : 0;
    const offsetSamples =
      last && Math.abs(nowSample - lastEnd) <= 640 ? lastEnd : Math.max(lastEnd, nowSample);
    const pcm = this._decodePcmu(pcmuBuf);
    targetList.push({ offsetSamples, pcm });
    const endIdx = offsetSamples + pcm.length;
    if (endIdx > this.maxSampleIndex) {
      this.maxSampleIndex = endIdx;
    }
  }

  writeCallerPcmu(pcmuBuf) {
    this._append(this.callerChunks, pcmuBuf);
  }

  writeAgentPcmu(pcmuBuf) {
    this._append(this.agentChunks, pcmuBuf);
  }

  finish() {
    if (this.closed) return null;
    this.closed = true;
    if (!this.sessionId || this.maxSampleIndex < SAMPLE_RATE / 2) {
      // Skip ultra-short (<0.5s) empty recordings
      this.callerChunks = [];
      this.agentChunks = [];
      return null;
    }

    try {
      ensureDir();
      const totalSamples = Math.min(this.maxSampleIndex, MAX_RECORDING_SECONDS * SAMPLE_RATE);
      const dataBytes = totalSamples * CHANNELS * 2;
      const stereoBuf = Buffer.alloc(dataBytes);

      for (const chunk of this.callerChunks) {
        const len = Math.min(chunk.pcm.length, totalSamples - chunk.offsetSamples);
        for (let i = 0; i < len; i++) {
          stereoBuf.writeInt16LE(chunk.pcm[i], (chunk.offsetSamples + i) * 4);
        }
      }
      for (const chunk of this.agentChunks) {
        const len = Math.min(chunk.pcm.length, totalSamples - chunk.offsetSamples);
        for (let i = 0; i < len; i++) {
          stereoBuf.writeInt16LE(chunk.pcm[i], (chunk.offsetSamples + i) * 4 + 2);
        }
      }

      const filePath = getRecordingPath(this.sessionId);
      const tmpPath = `${filePath}.tmp`;
      const header = buildWavHeader(dataBytes);
      const fd = fs.openSync(tmpPath, "w");
      try {
        fs.writeSync(fd, header);
        fs.writeSync(fd, stereoBuf);
      } finally {
        fs.closeSync(fd);
      }
      fs.renameSync(tmpPath, filePath);
      pruneOldRecordings();

      const durationSec = Math.round((totalSamples / SAMPLE_RATE) * 10) / 10;
      this.callerChunks = [];
      this.agentChunks = [];
      return {
        sessionId: this.sessionId,
        durationSec,
        sizeBytes: 44 + dataBytes,
      };
    } catch (err) {
      console.warn("[CallRecorder] Failed to save WAV:", err.message);
      this.callerChunks = [];
      this.agentChunks = [];
      return null;
    }
  }
}
