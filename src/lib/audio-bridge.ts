"use client";

// Browser Web Audio API <-> G.711 mu-law (8kHz) bridge + Ringtone + DTMF

const MULAW_MAX = 32635;
const MULAW_BIAS = 0x84;

const decodeTable = new Float32Array(256);
for (let i = 0; i < 256; i++) {
  const mu = ~i & 0xff;
  const sign = mu & 0x80;
  const exponent = (mu >> 4) & 0x07;
  const mantissa = mu & 0x0f;
  let sample = ((mantissa << 3) + MULAW_BIAS) << exponent;
  sample -= MULAW_BIAS;
  decodeTable[i] = (sign ? -sample : sample) / 32768;
}

function encodeMuLawSample(floatSample: number): number {
  let sample = Math.trunc(Math.max(-1, Math.min(1, floatSample)) * 32767);
  let sign = 0;
  if (sample < 0) {
    sign = 0x80;
    sample = -sample;
  }
  if (sample > MULAW_MAX) sample = MULAW_MAX;
  sample += MULAW_BIAS;
  let exponent = 7;
  for (let expMask = 0x4000; (sample & expMask) === 0 && exponent > 0; exponent--, expMask >>= 1) {}
  const mantissa = (sample >> (exponent + 3)) & 0x0f;
  return ~(sign | (exponent << 4) | mantissa) & 0xff;
}

const DTMF_FREQS: Record<string, [number, number]> = {
  "1": [697, 1209], "2": [697, 1336], "3": [697, 1477],
  "4": [770, 1209], "5": [770, 1336], "6": [770, 1477],
  "7": [852, 1209], "8": [852, 1336], "9": [852, 1477],
  "*": [941, 1209], "0": [941, 1336], "#": [941, 1477],
};

export class AudioBridge {
  private ctx: AudioContext | null = null;
  private micStream: MediaStream | null = null;
  private micSource: MediaStreamAudioSourceNode | null = null;
  private processor: ScriptProcessorNode | null = null;
  private silentGain: GainNode | null = null;
  private nextPlayTime = 0;
  private ringTimer: ReturnType<typeof setInterval> | null = null;
  private sendAudioCb: ((lineId: number, pcmu: Uint8Array) => void) | null = null;
  private activeLineId: number | null = null;
  public isReady = false;
  public micError: string | null = null;

  setSender(cb: (lineId: number, pcmu: Uint8Array) => void) {
    this.sendAudioCb = cb;
  }

  setActiveLine(lineId: number | null) {
    this.activeLineId = lineId;
  }

  private disconnectMicNodes() {
    if (this.processor) {
      this.processor.onaudioprocess = null;
      try { this.processor.disconnect(); } catch {}
      this.processor = null;
    }
    if (this.micSource) {
      try { this.micSource.disconnect(); } catch {}
      this.micSource = null;
    }
    if (this.silentGain) {
      try { this.silentGain.disconnect(); } catch {}
      this.silentGain = null;
    }
    if (this.micStream) {
      this.micStream.getTracks().forEach((t) => {
        try { t.stop(); } catch {}
      });
      this.micStream = null;
    }
  }

  async ensureAudio(): Promise<boolean> {
    if (!this.ctx) {
      const AudioCtx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      this.ctx = new AudioCtx();
    }
    if (this.ctx.state === "suspended") {
      await this.ctx.resume();
    }
    if (this.micStream && this.micStream.getTracks().every((t) => t.readyState === "ended")) {
      this.disconnectMicNodes();
    }
    if (!this.micStream) {
      try {
        this.micStream = await navigator.mediaDevices.getUserMedia({
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          },
        });
        this.micError = null;
        this.setupMicCapture();
      } catch (err) {
        this.micError = err instanceof Error ? err.message : "ไม่สามารถเข้าถึงไมโครโฟนได้";
      }
    }
    this.isReady = true;
    return true;
  }

  async disableAudio(): Promise<void> {
    this.isReady = false;
    this.micError = null;
    this.nextPlayTime = 0;
    this.stopRingtone();
    this.disconnectMicNodes();
  }

  private setupMicCapture() {
    if (!this.ctx || !this.micStream) return;
    if (this.processor) {
      this.processor.onaudioprocess = null;
      try { this.processor.disconnect(); } catch {}
      this.processor = null;
    }
    if (this.micSource) {
      try { this.micSource.disconnect(); } catch {}
      this.micSource = null;
    }
    if (this.silentGain) {
      try { this.silentGain.disconnect(); } catch {}
      this.silentGain = null;
    }
    const source = this.ctx.createMediaStreamSource(this.micStream);
    // 2048 samples @ 48kHz ~= 42ms; downsample to 8kHz
    const processor = this.ctx.createScriptProcessor(2048, 1, 1);
    const inputRate = this.ctx.sampleRate;
    const ratio = inputRate / 8000;
    let carry: number[] = [];

    processor.onaudioprocess = (ev) => {
      if (!this.isReady || !this.activeLineId || !this.sendAudioCb) return;
      const input = ev.inputBuffer.getChannelData(0);
      const outLen = Math.floor(input.length / ratio);
      for (let i = 0; i < outLen; i++) {
        const idx = Math.floor(i * ratio);
        carry.push(encodeMuLawSample(input[idx]));
        if (carry.length >= 160) {
          this.sendAudioCb(this.activeLineId, new Uint8Array(carry.slice(0, 160)));
          carry = carry.slice(160);
        }
      }
    };

    const silentGain = this.ctx.createGain();
    silentGain.gain.value = 0;
    source.connect(processor);
    processor.connect(silentGain);
    silentGain.connect(this.ctx.destination);

    this.micSource = source;
    this.processor = processor;
    this.silentGain = silentGain;
  }

  playIncomingPcmu(pcmu: Uint8Array) {
    if (!this.isReady || !this.ctx || this.ctx.state !== "running") return;
    const buf = this.ctx.createBuffer(1, pcmu.length, 8000);
    const channel = buf.getChannelData(0);
    for (let i = 0; i < pcmu.length; i++) {
      channel[i] = decodeTable[pcmu[i]];
    }
    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    src.connect(this.ctx.destination);

    const now = this.ctx.currentTime;
    if (this.nextPlayTime < now || this.nextPlayTime > now + 0.35) {
      this.nextPlayTime = now + 0.04;
    }
    src.start(this.nextPlayTime);
    this.nextPlayTime += buf.duration;
  }

  unlockOutputOnly(): boolean {
    try {
      if (!this.ctx) {
        const AudioCtx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
        this.ctx = new AudioCtx();
      }
      if (this.ctx.state === "suspended") {
        void this.ctx.resume();
      }
      return this.ctx.state === "running";
    } catch {
      return false;
    }
  }

  private _scheduleTicketChime(): void {
    if (!this.ctx || this.ctx.state !== "running") return;
    const now = this.ctx.currentTime;
    const notes = [880, 1174.66, 1318.51];
    notes.forEach((freq, idx) => {
      if (!this.ctx) return;
      const start = now + idx * 0.14;
      const gain = this.ctx.createGain();
      gain.gain.setValueAtTime(0.001, start);
      gain.gain.linearRampToValueAtTime(0.14, start + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.001, start + 0.2);
      gain.connect(this.ctx.destination);

      const osc = this.ctx.createOscillator();
      osc.type = "triangle";
      osc.frequency.setValueAtTime(freq, start);
      osc.connect(gain);
      osc.start(start);
      osc.stop(start + 0.22);
    });
  }

  playTicketAlertChime(): boolean {
    try {
      const unlocked = this.unlockOutputOnly();
      if (!this.ctx) return false;
      if (unlocked || this.ctx.state === "running") {
        this._scheduleTicketChime();
        return true;
      }
      if (this.ctx.state === "suspended") {
        const nav = navigator as Navigator & { userActivation?: { hasBeenActive: boolean } };
        if (nav.userActivation && !nav.userActivation.hasBeenActive) {
          return false;
        }
        void this.ctx.resume().then(() => this._scheduleTicketChime()).catch(() => {});
        return true;
      }
      return false;
    } catch {
      return false;
    }
  }

  playDtmf(digit: string) {
    const freqs = DTMF_FREQS[digit];
    if (!freqs) return;
    if (!this.ctx) {
      const AudioCtx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      this.ctx = new AudioCtx();
    }
    if (this.ctx.state === "suspended") {
      void this.ctx.resume();
    }
    const now = this.ctx.currentTime;
    const gain = this.ctx.createGain();
    gain.gain.setValueAtTime(0.08, now);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.14);
    gain.connect(this.ctx.destination);

    for (const f of freqs) {
      const osc = this.ctx.createOscillator();
      osc.type = "sine";
      osc.frequency.setValueAtTime(f, now);
      osc.connect(gain);
      osc.start(now);
      osc.stop(now + 0.14);
    }
  }

  startRingtone() {
    if (this.ringTimer) return;
    const playBurst = () => {
      if (!this.ctx || this.ctx.state !== "running") return;
      const now = this.ctx.currentTime;
      const notes = [659.25, 830.61, 659.25, 830.61];
      notes.forEach((freq, idx) => {
        if (!this.ctx) return;
        const start = now + idx * 0.16;
        const gain = this.ctx.createGain();
        gain.gain.setValueAtTime(0.001, start);
        gain.gain.linearRampToValueAtTime(0.09, start + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.001, start + 0.15);
        gain.connect(this.ctx.destination);

        const osc = this.ctx.createOscillator();
        osc.type = "triangle";
        osc.frequency.setValueAtTime(freq, start);
        osc.connect(gain);
        osc.start(start);
        osc.stop(start + 0.15);
      });
    };
    playBurst();
    this.ringTimer = setInterval(playBurst, 2200);
  }

  stopRingtone() {
    if (this.ringTimer) {
      clearInterval(this.ringTimer);
      this.ringTimer = null;
    }
  }

  destroy() {
    this.isReady = false;
    this.micError = null;
    this.nextPlayTime = 0;
    this.stopRingtone();
    this.disconnectMicNodes();
    if (this.ctx) {
      void this.ctx.close();
      this.ctx = null;
    }
  }
}
