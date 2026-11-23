import crypto from "node:crypto";
import {
  MediaStreamTrack,
  RTCPeerConnection,
  RTCRtpCodecParameters,
  RtpHeader,
  RtpPacket,
} from "werift";

export class JarvisCallSession {
  constructor({
    lineId,
    jarvisUrl,
    initialContext = null,
    language = null,
    isNewCaller = false,
    onAudioFromJarvis,
    onFlushAudio,
    onSubtitle,
    onLanguageDetected,
    onStateChange,
  }) {
    this.lineId = lineId;
    this.jarvisUrl = jarvisUrl.replace(/\/+$/, "");
    this.initialContext = initialContext;
    this.language = language;
    this.isNewCaller = Boolean(isNewCaller);
    this.onAudioFromJarvis = onAudioFromJarvis;
    this.onFlushAudio = onFlushAudio;
    this.onSubtitle = onSubtitle;
    this.onLanguageDetected = onLanguageDetected;
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

    // Barge-in state: duck/mute J.A.R.V.I.S TTS immediately when caller speaks over it
    this.connectedAt = 0;
    this.ducked = false;
    this.duckedAt = 0;
    this.lastBotAudioAt = 0;
    this.callerLoudFrames = 0;
    this.awaitingNewTurn = false;
    this.primingTimer = null;
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
        // Forward live streaming partial transcript (every 180ms while caller is speaking)
        if (msg?.type === "partial" && typeof msg.data === "string" && msg.data.trim()) {
          this.onSubtitle?.({ kind: "partial", text: msg.data.trim() });
          return;
        }
        // Only duck & flush RTP queue on word-verified barge-in (not on a cough or 100ms ambient noise!)
        if (msg?.type === "log" && msg?.data === "barge_in_confirmed") {
          this.ducked = true;
          this.duckedAt = Date.now();
          this.awaitingNewTurn = true;
          this.onFlushAudio?.();
          this.onSubtitle?.({ kind: "status", text: "ผู้โทรพูดแทรก • กำลังฟัง..." });
        } else if (msg?.type === "log" && msg?.data === "started_talking") {
          this.onSubtitle?.({ kind: "status", text: "ผู้โทรกำลังพูด..." });
        } else if (msg?.type === "log" && msg?.data === "noise") {
          this.ducked = false;
          this.awaitingNewTurn = false;
        } else if (msg?.type === "log" && msg?.data === "response_starting") {
          this.ducked = false;
          this.awaitingNewTurn = false;
          this.onSubtitle?.({ kind: "status", text: "J.A.R.V.I.S กำลังประมวลผลคำตอบ..." });
        }
      } catch {}
    });

    transceiver.onTrack.subscribe((track) => {
      track.onReceiveRtp.subscribe((pkt) => {
        if (this.closed) return;
        if (pkt.payload && pkt.payload.length > 0) {
          this.lastBotAudioAt = Date.now();
          if (this.ducked && !this.awaitingNewTurn && Date.now() - this.duckedAt > 2200) {
            this.ducked = false;
          }
          if (this.ducked) return;
          this.onAudioFromJarvis?.(pkt.payload);
        }
      });
    });

    pc.connectionStateChange.subscribe((st) => {
      this.onStateChange?.(st);
    });

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);

    // Wait briefly for ICE gathering (fast 80ms for localhost, 1200ms for remote)
    const isLocal = /^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?/i.test(this.jarvisUrl);
    await new Promise((resolve) => {
      if (pc.iceGatheringState === "complete") return resolve();
      const timer = setTimeout(resolve, isLocal ? 80 : 1200);
      pc.iceGatheringStateChange.subscribe((state) => {
        if (state === "complete") {
          clearTimeout(timer);
          resolve();
        }
      });
    });

    const res = await fetch(`${this.jarvisUrl}/webrtc/offer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sdp: pc.localDescription.sdp,
        type: pc.localDescription.type,
        webrtc_id: this.webrtcId,
      }),
    });

    if (!res.ok) {
      throw new Error(`J.A.R.V.I.S /webrtc/offer HTTP ${res.status}`);
    }

    const ans = await res.json();
    if (!ans?.sdp) {
      throw new Error(`J.A.R.V.I.S /webrtc/offer failed: ${JSON.stringify(ans)}`);
    }

    await pc.setRemoteDescription(ans);
    this.connectedAt = Date.now();
    this.startOutputsStream();

    // Send 600ms of quiet PCMU frames immediately so FastRTC initializes sampling_rate (>0)
    // and triggers its automatic greeting cleanly even before the caller speaks.
    let primeCount = 0;
    const silence = Buffer.alloc(160, 0xff);
    this.primingTimer = setInterval(() => {
      if (this.closed || primeCount >= 30) {
        if (this.primingTimer) clearInterval(this.primingTimer);
        this.primingTimer = null;
        return;
      }
      primeCount++;
      this._writePcmuRtp(silence);
    }, 20);

    // Sync caller language + CRM context ONLY via POST /language (NEVER call POST /chat at call start,
    // because POST /chat calls push_turn(interrupt=True) which kills the greeting and can trigger sr=0 crash!)
    await this.pushLanguage(this.language, this.isNewCaller, false, this.initialContext);
  }

  _writePcmuRtp(pcmuPayload) {
    if (this.closed || !this.txTrack) return;
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

  async pushLanguage(language, isNewCaller = false, _notifyChat = false, initialContext = null, pendingConfirm = null) {
    if (this.closed) return false;
    this.language = language || null;
    try {
      const res = await fetch(`${this.jarvisUrl}/language`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          webrtc_id: this.webrtcId,
          language: this.language,
          is_new_caller: Boolean(isNewCaller),
          initial_context: initialContext,
          pending_confirm: pendingConfirm,
        }),
      });
      if (res.ok) return true;
    } catch {}
    return false;
  }

  startOutputsStream() {
    this.sseAbort = new AbortController();
    fetch(`${this.jarvisUrl}/outputs?webrtc_id=${encodeURIComponent(this.webrtcId)}`, {
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
              if (evt?.kind === "lang" && evt?.language) {
                this.language = evt.language;
                this.onLanguageDetected?.(evt.language);
              }
              if (evt?.kind === "bot" && evt?.text) {
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
    // Barge-in is verified by J.A.R.V.I.S (80Hz HPF + Noise Floor + OpenThai live word check)
    // so coughs, knocks, and ambient room noise never cut off the agent's speech.
    this._writePcmuRtp(pcmuPayload);
  }

  async close() {
    if (this.closed) return;
    this.closed = true;
    if (this.primingTimer) {
      clearInterval(this.primingTimer);
      this.primingTimer = null;
    }
    try { this.sseAbort?.abort(); } catch {}
    try { await this.pc?.close(); } catch {}
  }
}
