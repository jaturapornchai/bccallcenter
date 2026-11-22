import crypto from "node:crypto";
import {
  MediaStreamTrack,
  RTCPeerConnection,
  RTCRtpCodecParameters,
  RtpHeader,
  RtpPacket,
} from "werift";
import { decodeMuLawSample } from "./g711.mjs";

function calcPcmuRms(buf) {
  if (!buf || buf.length === 0) return 0;
  let sumSq = 0;
  for (let i = 0; i < buf.length; i++) {
    const s = decodeMuLawSample(buf[i]) / 32768;
    sumSq += s * s;
  }
  return Math.sqrt(sumSq / buf.length);
}

export class JavisCallSession {
  constructor({ lineId, javisUrl, initialContext = null, onAudioFromJavis, onSubtitle, onStateChange }) {
    this.lineId = lineId;
    this.javisUrl = javisUrl.replace(/\/+$/, "");
    this.initialContext = initialContext;
    this.onAudioFromJavis = onAudioFromJavis;
    this.onSubtitle = onSubtitle;
    this.onStateChange = onStateChange;

    this.webrtcId = `sip-${lineId}-${crypto.randomBytes(5).toString("hex")}`;
    this.pc = null;
    this.txTrack = null;
    this.dc = null;
    this.closed = false;
    this.seq = Math.floor(Math.random() * 65535);
    this.ts = Math.floor(Math.random() * 0xffffffff);
    this.ssrc = crypto.randomBytes(4).readUInt32BE(0);
    this.sseAbort = null;

    // Barge-in state: duck/mute Javis TTS immediately when caller speaks over it
    this.ducked = false;
    this.duckedAt = 0;
    this.lastBotAudioAt = 0;
    this.callerLoudFrames = 0;
    this.awaitingNewTurn = false;
  }

  async connect() {
    const pc = new RTCPeerConnection({
      codecs: {
        audio: [
          new RTCRtpCodecParameters({
            mimeType: "audio/PCMU",
            clockRate: 8000,
            channels: 1,
            payloadType: 0,
          }),
        ],
      },
    });
    this.pc = pc;

    const txTrack = new MediaStreamTrack({ kind: "audio" });
    this.txTrack = txTrack;

    const transceiver = pc.addTransceiver(txTrack, { direction: "sendrecv" });
    const dc = pc.createDataChannel("text");
    this.dc = dc;

    dc.onMessage.subscribe((raw) => {
      if (this.closed) return;
      try {
        const msg = JSON.parse(typeof raw === "string" ? raw : raw.toString("utf8"));
        if (msg?.type === "log" && msg?.data === "started_talking") {
          // Javis VAD detected caller speaking -> silence Javis immediately
          this.ducked = true;
          this.duckedAt = Date.now();
          this.awaitingNewTurn = true;
        } else if (msg?.type === "log" && msg?.data === "noise") {
          // False alarm (noise only) -> resume Javis audio
          this.ducked = false;
          this.awaitingNewTurn = false;
        } else if (msg?.type === "log" && msg?.data === "response_starting") {
          this.ducked = false;
          this.awaitingNewTurn = false;
        }
      } catch {}
    });

    transceiver.onTrack.subscribe((track) => {
      track.onReceiveRtp.subscribe((pkt) => {
        if (this.closed) return;
        if (pkt.payload && pkt.payload.length > 0) {
          this.lastBotAudioAt = Date.now();
          // Auto-release local ducking if no server confirmation arrived after 2.2s
          if (this.ducked && !this.awaitingNewTurn && Date.now() - this.duckedAt > 2200) {
            this.ducked = false;
          }
          if (this.ducked) return;
          this.onAudioFromJavis?.(pkt.payload);
        }
      });
    });

    pc.connectionStateChange.subscribe((st) => {
      this.onStateChange?.(st);
    });

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);

    await new Promise((resolve) => {
      if (pc.iceGatheringState === "complete") return resolve();
      const timer = setTimeout(resolve, 1500);
      pc.iceGatheringStateChange.subscribe((state) => {
        if (state === "complete") {
          clearTimeout(timer);
          resolve();
        }
      });
    });

    const res = await fetch(`${this.javisUrl}/webrtc/offer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sdp: pc.localDescription.sdp,
        type: pc.localDescription.type,
        webrtc_id: this.webrtcId,
      }),
    });

    if (!res.ok) {
      throw new Error(`Javis /webrtc/offer HTTP ${res.status}`);
    }

    const ans = await res.json();
    if (!ans?.sdp) {
      throw new Error(`Javis /webrtc/offer failed: ${JSON.stringify(ans)}`);
    }

    await pc.setRemoteDescription(ans);
    this.startOutputsStream();

    if (this.initialContext) {
      setTimeout(() => {
        if (this.closed) return;
        fetch(`${this.javisUrl}/chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            webrtc_id: this.webrtcId,
            text: this.initialContext,
          }),
        }).catch(() => {});
      }, 700);
    }
  }

  startOutputsStream() {
    this.sseAbort = new AbortController();
    fetch(`${this.javisUrl}/outputs?webrtc_id=${encodeURIComponent(this.webrtcId)}`, {
      signal: this.sseAbort.signal,
    })
      .then(async (res) => {
        if (!res.ok || !res.body) return;
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";
        while (!this.closed) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          const lines = buf.split("\n");
          buf = lines.pop() || "";
          for (const line of lines) {
            if (!line.startsWith("data: ")) continue;
            try {
              const evt = JSON.parse(line.slice(6));
              if (evt?.kind === "bot" && evt?.text) {
                // New bot response chunk arrived -> unmute output so caller hears the new answer
                this.ducked = false;
                this.awaitingNewTurn = false;
              }
              this.onSubtitle?.(evt);
            } catch {}
          }
        }
      })
      .catch(() => {});
  }

  sendPcmu(pcmuPayload) {
    if (this.closed || !this.txTrack) return;

    // Fast local energy-based barge-in (~100ms) while Javis is speaking
    const botIsSpeaking = Date.now() - this.lastBotAudioAt < 350;
    if (botIsSpeaking && !this.ducked) {
      const rms = calcPcmuRms(pcmuPayload);
      if (rms > 0.055) {
        this.callerLoudFrames += 1;
        if (this.callerLoudFrames >= 5) {
          this.ducked = true;
          this.duckedAt = Date.now();
          this.callerLoudFrames = 0;
        }
      } else {
        this.callerLoudFrames = 0;
      }
    } else {
      this.callerLoudFrames = 0;
    }

    this.seq = (this.seq + 1) & 0xffff;
    this.ts = (this.ts + pcmuPayload.length) >>> 0;
    const pkt = new RtpPacket(
      new RtpHeader({
        version: 2,
        padding: false,
        extension: false,
        marker: false,
        payloadType: 0,
        sequenceNumber: this.seq,
        timestamp: this.ts,
        ssrc: this.ssrc,
      }),
      Buffer.from(pcmuPayload)
    );
    this.txTrack.writeRtp(pkt);
  }

  async close() {
    if (this.closed) return;
    this.closed = true;
    try { this.sseAbort?.abort(); } catch {}
    try { await this.pc?.close(); } catch {}
  }
}
