import crypto from "node:crypto";
import dgram from "node:dgram";
import dns from "node:dns/promises";
import { EventEmitter } from "node:events";
import os from "node:os";
import { analyzeCallTranscript } from "./call-analyzer.mjs";
import { CallMemoryStore, normalizePhone } from "./call-memory.mjs";
import { CallRecorder } from "./call-recorder.mjs";
import { cleanCustomerName, CrmStore, LANGUAGE_LABELS, VALID_LANGUAGES } from "./crm-store.mjs";
import { decodeMuLawSample } from "./g711.mjs";
import { JarvisCallSession } from "./jarvis-bridge.mjs";

function calcPcmuLevel(buf) {
  if (!buf || buf.length === 0) return 0;
  let sumSq = 0;
  const step = buf.length >= 80 ? 2 : 1;
  let count = 0;
  for (let i = 0; i < buf.length; i += step) {
    const s = decodeMuLawSample(buf[i]) / 32768;
    sumSq += s * s;
    count++;
  }
  const rms = Math.sqrt(sumSq / Math.max(count, 1));
  return Math.min(100, Math.round(rms * 320));
}

function extractCallerNameFromText(text, prevBotText = "") {
  const s = String(text || "").trim();
  if (!s) return null;
  const badTh = new Set([
    "อะไร", "เล่น", "จริง", "โปรแกรม", "ระบบ", "บริษัท", "ร้าน", "ลูกค้า", "เมนู", "สินค้า",
    "ไม่", "ครับ", "ค่ะ", "คะ", "คับ", "ไม่สะดวก", "ไม่เป็นไร", "ไม่บอก", "ไม่ต้อง", "เบอร์",
    "ราคา", "ใช่", "ไม่ใช่", "โอเค", "ได้เลย", "ดี", "ครับผม", "เปลี่ยนเลย", "เรื่องเดิม",
  ]);
  // Explicit Thai patterns: "ชื่อว่าลุงจืด", "ผมชื่อสมชายครับ", "เรียกผมว่าเบิร์ดก็ได้"
  const thMatch = s.match(
    /(?:ผมชื่อว่า|ดิฉันชื่อว่า|หนูชื่อว่า|ฉันชื่อว่า|พี่ชื่อว่า|ชื่อว่า|ผมชื่อ|ดิฉันชื่อ|หนูชื่อ|ฉันชื่อ|พี่ชื่อ|เรียกผมว่า|เรียกพี่ว่า|เรียกหนูว่า|เรียกฉันว่า|เรียกว่า|ชื่อเล่นว่า|ชื่อเล่น|ชื่อ)\s*([ก-๙a-zA-Z]{2,20})/
  );
  if (thMatch && thMatch[1]) {
    const cand = cleanCustomerName(thMatch[1]);
    if (cand.length >= 2 && !badTh.has(cand)) return { name: cand, explicit: true };
  }
  const enMatch = s.match(/(?:my name is|i am|i'm|call me)\s+([A-Za-z]{2,20})/i);
  if (enMatch && enMatch[1]) {
    const badEn = new Set(["calling", "asking", "looking", "trying", "interested", "using", "not", "here", "fine", "good", "okay"]);
    if (!badEn.has(enMatch[1].toLowerCase())) return { name: enMatch[1], explicit: true };
  }
  // Short direct answer right after AI asked for the caller's name (e.g. "ลุงจืดครับ", "สมชาย")
  if (prevBotText && /(?:ขอเรียกชื่อ|ชื่อเล่นว่าอะไร|ชื่ออะไรดี|may i have your name|what(?:'s| is) your name)/i.test(prevBotText)) {
    const shortMatch = s.match(/^([ก-๙a-zA-Z]{2,18})(?:\s*(?:ครับผม|นะครับ|นะคะ|ครับ|ค่ะ|คะ|คับ|ก็ได้))?$/);
    if (shortMatch && shortMatch[1]) {
      const cand = cleanCustomerName(shortMatch[1]);
      if (cand.length >= 2 && !badTh.has(cand) && !/^(ไม่|ขอ|อยาก|มี|ทำ|ราคา|เบอร์)/.test(cand)) {
        return { name: cand, explicit: false };
      }
    }
  }
  return null;
}

function detectLiveCallSignal(messages) {
  const callerTexts = (messages || []).filter((m) => m.role === "caller").map((m) => String(m.text || ""));
  if (callerTexts.length === 0) return null;
  const full = callerTexts.join(" ");
  const recent = callerTexts.slice(-2).join(" ");

  const frustratedRe = /(?:หงุดหงิด|ช้ามาก|รอนาน|ไม่เข้าใจ|งง|ไม่ได้เรื่อง|พัง|แย่|รำคาญ|คุยไม่รู้เรื่อง|โกรธ|โมโห|เสียเวลา)/i;
  const positiveRe = /(?:ดีเลย|เยี่ยม|ขอบคุณ|สุดยอด|เข้าท่า|โอเคเลย|สบายใจ|ดีครับ|ดีมาก|น่ารัก|ชอบเลย|เข้าใจแล้ว)/i;
  const highBuyRe = /(?:ราคาเท่าไหร่|ราคา|แพ็กเกจ|ทดลองใช้|เดโม|demo|ซื้อ|สมัคร|ติดตั้ง|เสนอราคา|โทรกลับ|ติดต่อกลับ|กี่บาท|ใช้ได้ไหม|ร้านผม|บริษัทผม|หลายสาขา)/i;
  const medBuyRe = /(?:ทำอะไรได้|มีระบบ|รองรับ|สต็อก|บัญชี|ภาษี|คอมมิชชั่น|สาขา|ออนไลน์|คลาวด์)/i;

  const isFrustrated = frustratedRe.test(recent) || frustratedRe.test(full);
  const isPositive = positiveRe.test(recent);
  const highBuy = highBuyRe.test(full);
  const medBuy = medBuyRe.test(full);

  const sentiment = isFrustrated ? "frustrated" : isPositive ? "positive" : "neutral";
  const buyingIntent = highBuy ? "high" : medBuy ? "medium" : "none";

  let label = "กำลังสนทนา";
  let tone = "idle";
  if (isFrustrated) {
    label = "เริ่มกังวล/หงุดหงิด";
    tone = "danger";
  } else if (highBuy) {
    label = "สนใจซื้อสูง";
    tone = "in";
  } else if (medBuy) {
    label = "กำลังพิจารณา";
    tone = "held";
  } else if (isPositive) {
    label = "คุยสบายใจ";
    tone = "in";
  }

  return {
    sentiment,
    buyingIntent,
    label,
    tone,
    updatedAt: new Date().toISOString(),
  };
}

function randId(len = 12) {
  return crypto.randomBytes(Math.ceil(len / 2)).toString("hex").slice(0, len);
}

function md5(str) {
  return crypto.createHash("md5").update(str).digest("hex");
}

function getLocalIpFallback() {
  const nets = os.networkInterfaces();
  for (const name of Object.keys(nets)) {
    for (const net of nets[name] || []) {
      if (net.family === "IPv4" && !net.internal) {
        return net.address;
      }
    }
  }
  return "127.0.0.1";
}

function parseSipMessage(raw) {
  const sepIdx = raw.indexOf("\r\n\r\n");
  const head = sepIdx >= 0 ? raw.slice(0, sepIdx) : raw;
  const body = sepIdx >= 0 ? raw.slice(sepIdx + 4) : "";
  const lines = head.split(/\r?\n/);
  const startLine = lines[0] || "";
  const headers = [];
  for (let i = 1; i < lines.length; i++) {
    const line = lines[i];
    const colon = line.indexOf(":");
    if (colon > 0) {
      headers.push({
        name: line.slice(0, colon).trim(),
        lower: line.slice(0, colon).trim().toLowerCase(),
        value: line.slice(colon + 1).trim(),
      });
    }
  }
  return { startLine, headers, body };
}

function getHeader(msg, name) {
  const low = name.toLowerCase();
  const h = msg.headers.find((x) => x.lower === low);
  return h ? h.value : "";
}

function getHeaders(msg, name) {
  const low = name.toLowerCase();
  return msg.headers.filter((x) => x.lower === low).map((x) => x.value);
}

function parseAuthParams(val) {
  const params = {};
  const re = /(\w+)=(?:"([^"]*)"|([^,\s]+))/g;
  let m;
  while ((m = re.exec(val)) !== null) {
    params[m[1].toLowerCase()] = m[2] !== undefined ? m[2] : m[3];
  }
  return params;
}

function buildDigestHeader({ method, uri, username, password, challenge }) {
  const realm = challenge.realm || "";
  const nonce = challenge.nonce || "";
  const algorithm = (challenge.algorithm || "MD5").toUpperCase();
  const qop = challenge.qop || "";
  const opaque = challenge.opaque || "";

  const ha1 = md5(`${username}:${realm}:${password}`);
  const ha2 = md5(`${method}:${uri}`);
  const cnonce = randId(8);
  const nc = "00000001";
  const qopList = qop.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);

  let response;
  let qopPart = "";
  if (qopList.includes("auth")) {
    response = md5(`${ha1}:${nonce}:${nc}:${cnonce}:auth:${ha2}`);
    qopPart = `, qop=auth, nc=${nc}, cnonce="${cnonce}"`;
  } else {
    response = md5(`${ha1}:${nonce}:${ha2}`);
  }
  const opaquePart = opaque ? `, opaque="${opaque}"` : "";
  return `Digest username="${username}", realm="${realm}", nonce="${nonce}", uri="${uri}", response="${response}", algorithm=${algorithm}${qopPart}${opaquePart}`;
}

function parseCallerInfo(headerVal) {
  const nameMatch = headerVal.match(/^\s*"?([^"<]+?)"?\s*</);
  const uriMatch = headerVal.match(/<([^>]+)>/);
  const uri = uriMatch ? uriMatch[1] : headerVal.split(";")[0].trim();
  const userMatch = uri.match(/sip:([^@;>]+)/i);
  const number = userMatch ? userMatch[1] : uri;
  const displayName = nameMatch ? nameMatch[1].trim() : "";
  return { number, displayName, uri };
}

function parseSdpRtp(sdp) {
  if (!sdp) return { ip: null, port: null };
  const ipMatch = sdp.match(/^c=IN IP4 (\S+)/m);
  const portMatch = sdp.match(/^m=audio (\d+) /m);
  return {
    ip: ipMatch ? ipMatch[1] : null,
    port: portMatch ? Number(portMatch[1]) : null,
  };
}

function buildSdp(localIp, rtpPort, direction = "sendrecv") {
  const sid = Math.floor(Date.now() / 1000);
  return [
    "v=0",
    `o=- ${sid} ${sid} IN IP4 ${localIp}`,
    "s=YalecomConsole",
    `c=IN IP4 ${localIp}`,
    "t=0 0",
    `m=audio ${rtpPort} RTP/AVP 0 8 101`,
    "a=rtpmap:0 PCMU/8000",
    "a=rtpmap:8 PCMA/8000",
    "a=rtpmap:101 telephone-event/8000",
    "a=fmtp:101 0-16",
    `a=${direction}`,
  ].join("\r\n") + "\r\n";
}

function extractUri(headerVal) {
  const m = headerVal.match(/<([^>]+)>/);
  return m ? m[1] : headerVal.split(";")[0].trim();
}

function ensureToTag(toHdr, tag) {
  if (/;tag=/i.test(toHdr)) return toHdr;
  return `${toHdr};tag=${tag}`;
}

export class SipEngine extends EventEmitter {
  constructor(config) {
    super();
    this.host = config.host;
    this.failoverHost = config.failoverHost || "";
    this.port = Number(config.port || 5060);
    this.user = config.user;
    this.password = config.password;
    this.maxLines = Number(config.maxLines || 5);
    this.jarvisUrl = config.jarvisUrl || "http://127.0.0.1:7861";
    this.autoAnswerAi = config.autoAnswerAi !== false;
    this.ollamaBaseUrl = config.ollamaBaseUrl || "https://ollama.com";
    this.ollamaModel = config.ollamaModel || "deepseek-v4.1-flash";
    this.ollamaApiKey = config.ollamaApiKey || "";

    this.resolvedIp = null;
    this.localIp = getLocalIpFallback();
    this.localPort = this.port;
    this.sock = null;
    this.started = false;

    this.trunk = {
      status: "connecting", // connecting | registered | error
      host: this.host,
      ip: null,
      port: this.port,
      user: this.user,
      lastRegisterAt: null,
      expires: 120,
      error: null,
    };

    this.regCallId = `${randId(16)}@yalecom`;
    this.regTag = randId(8);
    this.regCseq = 1;
    this.regTimer = null;
    this.keepAliveTimer = null;
    this.nextRtpPort = 20000;

    this.lines = Array.from({ length: this.maxLines }, (_, i) => this.createEmptyLine(i + 1));
    this.history = [];
    this.memory = new CallMemoryStore();
    this.crm = new CrmStore();
    for (const caller of this.memory.listRecentCallers(50)) {
      if (caller.phone && !this.crm.getCustomer(caller.phone)) {
        this.crm.upsertCustomer({ phone: caller.phone, tier: caller.totalCalls >= 3 ? "regular" : "new" });
      }
    }
    this.activeAudioLine = null;
  }

  createEmptyLine(lineId) {
    return {
      lineId,
      state: "idle", // idle | ringing_in | dialing | ringing_out | connected | held
      direction: null, // inbound | outbound
      remoteNumber: "",
      remoteName: "",
      startedAt: null,
      connectedAt: null,
      muted: false,
      held: false,
      aiActive: false,
      aiTranscript: "",
      aiPartial: "",
      aiStatus: "",
      liveMessages: [],
      liveSignal: null,
      callerTotalCalls: 0,
      callerHasHistory: false,
      sessionId: null,
      crmName: "",
      crmCompany: "",
      crmTier: null,
      crmOpenTickets: 0,
      language: null,
      pendingLanguageConfirm: null,
      isNewCaller: false,
      // internal SIP dialog state (stripped before sending to client)
      _dialog: null,
    };
  }

  publicState() {
    return {
      trunk: this.trunk,
      autoAnswerAi: this.autoAnswerAi,
      jarvisUrl: this.jarvisUrl,
      activeAudioLine: this.activeAudioLine,
      lines: this.lines.map(({ _dialog, ...rest }) => rest),
      history: this.history.slice(0, 30),
      callers: this.memory.listRecentCallers(25),
      callLogs: this.memory.listCallLogs(40),
      crm: this.crm.publicState(),
    };
  }

  emitState() {
    this.emit("state", this.publicState());
  }

  addHistory(entry) {
    this.history.unshift({
      id: randId(10),
      at: new Date().toISOString(),
      ...entry,
    });
    if (this.history.length > 50) this.history.length = 50;
  }

  async start() {
    if (this.started) return;
    this.started = true;

    try {
      const res = await dns.lookup(this.host);
      this.resolvedIp = res.address;
      this.trunk.ip = res.address;
    } catch (err) {
      this.trunk.status = "error";
      this.trunk.error = `DNS error: ${err.message}`;
      this.emitState();
      return;
    }

    this.sock = dgram.createSocket("udp4");
    this.sock.on("message", (buf, rinfo) => {
      this.handleSipMessage(buf.toString("utf8"), rinfo);
    });

    // Real-time audio level telemetry (150ms) while any call is connected
    if (!this.meterTimer) {
      let lastHadActive = false;
      this.meterTimer = setInterval(() => {
        const levels = {};
        let hasActive = false;
        for (const line of this.lines) {
          if (line.state === "connected" && line._dialog) {
            hasActive = true;
            const inLvl = line._dialog._inLevel || 0;
            const outLvl = line._dialog._outLevel || 0;
            levels[line.lineId] = { in: inLvl, out: outLvl };
            // Smooth decay toward 0 between RTP bursts
            line._dialog._inLevel = Math.floor(inLvl * 0.45);
            line._dialog._outLevel = Math.floor(outLvl * 0.45);
          }
        }
        if (hasActive || lastHadActive) {
          lastHadActive = hasActive;
          this.emit("meter", levels);
        }
      }, 150);
      this.meterTimer.unref?.();
    }
    this.sock.on("error", (err) => {
      this.trunk.status = "error";
      this.trunk.error = err.message;
      this.emitState();
    });

    await new Promise((resolve) => {
      this.sock.once("error", () => {
        // Fallback if port 5060 is already in use
        this.sock.bind(0, "0.0.0.0", resolve);
      });
      this.sock.bind(this.port, "0.0.0.0", resolve);
    });

    this.localPort = this.sock.address().port;
    this.localIp = await this.detectOutboundIp(this.resolvedIp, this.port);
    console.log(`[SIP] Listening UDP ${this.localIp}:${this.localPort} -> ${this.host} (${this.resolvedIp}:${this.port})`);

    this.sendRegister();
    this.regTimer = setInterval(() => this.sendRegister(), 110_000);
    this.keepAliveTimer = setInterval(() => this.sendOptions(), 30_000);
  }

  async detectOutboundIp(targetIp, targetPort) {
    return new Promise((resolve) => {
      const s = dgram.createSocket("udp4");
      s.connect(targetPort, targetIp, () => {
        const addr = s.address();
        s.close();
        resolve(addr.address || getLocalIpFallback());
      });
      s.on("error", () => {
        s.close();
        resolve(getLocalIpFallback());
      });
    });
  }

  stop() {
    if (this.regTimer) clearInterval(this.regTimer);
    if (this.keepAliveTimer) clearInterval(this.keepAliveTimer);
    for (const line of this.lines) {
      this.cleanupRtp(line);
    }
    if (this.sock) {
      try { this.sock.close(); } catch {}
    }
    this.started = false;
  }

  sendRaw(msg) {
    if (!this.sock || !this.resolvedIp) return;
    this.sock.send(Buffer.from(msg, "utf8"), this.port, this.resolvedIp);
  }

  sendOptions() {
    const branch = `z9hG4bK${randId(10)}`;
    const msg = [
      `OPTIONS sip:${this.host} SIP/2.0`,
      `Via: SIP/2.0/UDP ${this.localIp}:${this.localPort};branch=${branch};rport`,
      "Max-Forwards: 70",
      `From: <sip:${this.user}@${this.host}>;tag=${randId(8)}`,
      `To: <sip:${this.host}>`,
      `Call-ID: ${randId(14)}@${this.localIp}`,
      "CSeq: 1 OPTIONS",
      `Contact: <sip:${this.user}@${this.localIp}:${this.localPort}>`,
      "User-Agent: YalecomConsole/1.0",
      "Content-Length: 0",
    ].join("\r\n") + "\r\n\r\n";
    this.sendRaw(msg);
  }

  sendRegister(authHeader = null, authHeaderName = "Authorization") {
    const branch = `z9hG4bK${randId(10)}`;
    const cseq = this.regCseq++;
    const lines = [
      `REGISTER sip:${this.host} SIP/2.0`,
      `Via: SIP/2.0/UDP ${this.localIp}:${this.localPort};branch=${branch};rport`,
      "Max-Forwards: 70",
      `From: <sip:${this.user}@${this.host}>;tag=${this.regTag}`,
      `To: <sip:${this.user}@${this.host}>`,
      `Call-ID: ${this.regCallId}`,
      `CSeq: ${cseq} REGISTER`,
      `Contact: <sip:${this.user}@${this.localIp}:${this.localPort};transport=udp>`,
      "Expires: 180",
      "User-Agent: YalecomConsole/1.0",
      "Allow: INVITE, ACK, CANCEL, OPTIONS, BYE, REFER, NOTIFY, INFO, UPDATE",
    ];
    if (authHeader) lines.push(`${authHeaderName}: ${authHeader}`);
    lines.push("Content-Length: 0");
    this.sendRaw(lines.join("\r\n") + "\r\n\r\n");
  }

  async openRtpSocket(line) {
    this.cleanupRtp(line);
    const rtpSock = dgram.createSocket("udp4");
    let bound = false;
    for (let attempt = 0; attempt < 40; attempt++) {
      const port = this.nextRtpPort;
      this.nextRtpPort = this.nextRtpPort >= 30000 ? 20000 : this.nextRtpPort + 2;
      try {
        await new Promise((resolve, reject) => {
          const onErr = (e) => reject(e);
          rtpSock.once("error", onErr);
          rtpSock.bind(port, "0.0.0.0", () => {
            rtpSock.removeListener("error", onErr);
            resolve();
          });
        });
        bound = true;
        break;
      } catch {
        // try next even port
      }
    }
    if (!bound) {
      await new Promise((resolve) => rtpSock.bind(0, "0.0.0.0", resolve));
    }

    const rtpPort = rtpSock.address().port;
    const dlg = line._dialog;
    dlg.rtpSocket = rtpSock;
    dlg.rtpPort = rtpPort;
    dlg.rtpSeq = Math.floor(Math.random() * 65535);
    dlg.rtpTs = Math.floor(Math.random() * 0xffffffff);
    dlg.rtpSsrc = crypto.randomBytes(4).readUInt32BE(0);
    // Outbound 20ms RTP jitter/pacing buffer (eliminates burst/gap stuttering on phone lines)
    dlg.rtpQueue = [];
    dlg.rtpQueuedBytes = 0;
    dlg.rtpSpeaking = false;
    dlg.rtpFirstQueuedAt = 0;
    dlg.rtpPacerTimer = null;
    dlg._recorder = line.sessionId ? new CallRecorder(line.sessionId) : null;
    this.startRtpPacer(line);

    rtpSock.on("message", (pkt) => {
      if (pkt.length <= 12) return;
      if (line.held) return;
      const v = (pkt[0] >> 6) & 0x03;
      if (v !== 2) return;
      const pt = pkt[1] & 0x7f;
      if (pt !== 0 && pt !== 8) return; // PCMU (0) or PCMA (8)
      const cc = pkt[0] & 0x0f;
      const hasExt = (pkt[0] & 0x10) !== 0;
      let offset = 12 + cc * 4;
      if (hasExt && pkt.length >= offset + 4) {
        const extLen = pkt.readUInt16BE(offset + 2) * 4;
        offset += 4 + extLen;
      }
      if (offset >= pkt.length) return;
      const payload = pkt.subarray(offset);
      if (dlg._recorder) dlg._recorder.writeCallerPcmu(payload);
      const lvl = calcPcmuLevel(payload);
      if (lvl > (dlg._inLevel || 0)) dlg._inLevel = lvl;
      if (line.aiActive && dlg._jarvis) {
        dlg._jarvis.sendPcmu(payload);
      }
      this.emit("rtp", { lineId: line.lineId, pt, payload });
    });

    return rtpPort;
  }

  cleanupRtp(line) {
    this.stopRtpPacer(line);
    if (line._dialog?._jarvis) {
      void line._dialog._jarvis.close();
      line._dialog._jarvis = null;
    }
    if (line._dialog?.rtpSocket) {
      try { line._dialog.rtpSocket.close(); } catch {}
      line._dialog.rtpSocket = null;
    }
  }

  startRtpPacer(line) {
    const dlg = line._dialog;
    if (!dlg || dlg.rtpPacerTimer) return;
    const FRAME_BYTES = 160; // 20ms @ 8kHz G.711 PCMU
    const PREBUFFER_BYTES = FRAME_BYTES * 8; // 160ms smooth jitter buffer eliminates mid-word stutter
    const SILENCE_FRAME = Buffer.alloc(FRAME_BYTES, 0xff);
    const INTERVAL_NS = 20_000_000n;
    let nextTickNs = process.hrtime.bigint() + INTERVAL_NS;
    let closed = false;

    const sendRawFrame = (payload, marker = false) => {
      if (!dlg.rtpSocket || !dlg.remoteRtpIp || !dlg.remoteRtpPort) return;
      const pkt = Buffer.allocUnsafe(12 + payload.length);
      pkt[0] = 0x80; // V=2, P=0, X=0, CC=0
      pkt[1] = marker ? 0x80 : 0x00; // M bit + PT=0 (PCMU)
      dlg.rtpSeq = (dlg.rtpSeq + 1) & 0xffff;
      pkt.writeUInt16BE(dlg.rtpSeq, 2);
      dlg.rtpTs = (dlg.rtpTs + payload.length) >>> 0;
      pkt.writeUInt32BE(dlg.rtpTs, 4);
      pkt.writeUInt32BE(dlg.rtpSsrc, 8);
      payload.copy(pkt, 12);
      dlg.rtpSocket.send(pkt, dlg.remoteRtpPort, dlg.remoteRtpIp);
    };

    const popFrame = () => {
      const out = Buffer.alloc(FRAME_BYTES, 0xff); // pre-filled with PCMU silence for clean tail padding
      let filled = 0;
      while (filled < FRAME_BYTES && dlg.rtpQueue.length > 0) {
        const head = dlg.rtpQueue[0];
        const take = Math.min(head.length, FRAME_BYTES - filled);
        head.copy(out, filled, 0, take);
        filled += take;
        if (take === head.length) {
          dlg.rtpQueue.shift();
        } else {
          dlg.rtpQueue[0] = head.subarray(take);
        }
        dlg.rtpQueuedBytes = Math.max(0, dlg.rtpQueuedBytes - take);
      }
      return out;
    };

    const tick = () => {
      if (closed || !dlg.rtpSocket) return;
      const nowNs = process.hrtime.bigint();
      if (line.state === "connected" && dlg.remoteRtpIp && dlg.remoteRtpPort) {
        if (line.muted || line.held) {
          sendRawFrame(SILENCE_FRAME, false);
        } else {
          let marker = false;
          if (!dlg.rtpSpeaking) {
            const waitedMs = dlg.rtpFirstQueuedAt ? Date.now() - dlg.rtpFirstQueuedAt : 0;
            if (dlg.rtpQueuedBytes >= PREBUFFER_BYTES || (dlg.rtpQueuedBytes >= FRAME_BYTES && waitedMs >= 160)) {
              dlg.rtpSpeaking = true;
              marker = true;
            }
          }
          if (dlg.rtpSpeaking && dlg.rtpQueuedBytes > 0) {
            const frame = popFrame();
            const outLvl = calcPcmuLevel(frame);
            if (outLvl > (dlg._outLevel || 0)) dlg._outLevel = outLvl;
            if (dlg._recorder) dlg._recorder.writeAgentPcmu(frame);
            sendRawFrame(frame, marker);
            this.emit("rtp", { lineId: line.lineId, pt: 0, payload: frame });
            if (dlg.rtpQueuedBytes === 0) {
              dlg.rtpSpeaking = false;
              dlg.rtpFirstQueuedAt = 0;
            }
          } else {
            sendRawFrame(SILENCE_FRAME, false);
          }
        }
      }
      nextTickNs += INTERVAL_NS;
      // Re-sync if OS event loop paused > 100ms to prevent catch-up bursts
      if (nextTickNs < nowNs - 100_000_000n) {
        nextTickNs = nowNs + INTERVAL_NS;
      }
      const delayMs = Math.max(1, Number((nextTickNs - process.hrtime.bigint()) / 1_000_000n));
      dlg.rtpPacerTimer = setTimeout(tick, delayMs);
    };

    dlg._stopRtpPacer = () => {
      closed = true;
      if (dlg.rtpPacerTimer) {
        clearTimeout(dlg.rtpPacerTimer);
        dlg.rtpPacerTimer = null;
      }
    };
    dlg.rtpPacerTimer = setTimeout(tick, 20);
  }

  stopRtpPacer(line) {
    const dlg = line?._dialog;
    if (!dlg) return;
    if (typeof dlg._stopRtpPacer === "function") {
      dlg._stopRtpPacer();
      dlg._stopRtpPacer = null;
    } else if (dlg.rtpPacerTimer) {
      clearTimeout(dlg.rtpPacerTimer);
      dlg.rtpPacerTimer = null;
    }
    dlg.rtpQueue = [];
    dlg.rtpQueuedBytes = 0;
    dlg.rtpSpeaking = false;
    dlg.rtpFirstQueuedAt = 0;
  }

  clearRtpQueue(lineId) {
    const line = this.lines[lineId - 1];
    const dlg = line?._dialog;
    if (!dlg) return;
    dlg.rtpQueue = [];
    dlg.rtpQueuedBytes = 0;
    dlg.rtpSpeaking = false;
    dlg.rtpFirstQueuedAt = 0;
  }

  sendRtpAudio(lineId, pcmuPayload) {
    const line = this.lines[lineId - 1];
    if (!line || line.muted || line.held) return;
    if (line.state !== "connected") return;
    const dlg = line._dialog;
    if (!dlg?.rtpSocket || !dlg.remoteRtpIp || !dlg.remoteRtpPort) return;
    if (!pcmuPayload || pcmuPayload.length === 0) return;

    if (!dlg.rtpQueue) dlg.rtpQueue = [];
    const buf = Buffer.from(pcmuPayload);
    if (dlg.rtpQueuedBytes === 0) {
      dlg.rtpFirstQueuedAt = Date.now();
    }
    dlg.rtpQueue.push(buf);
    dlg.rtpQueuedBytes = (dlg.rtpQueuedBytes || 0) + buf.length;

    // Cap buffer at 12 seconds (96,000 bytes @ 8kHz) to prevent runaway lag
    const MAX_QUEUE_BYTES = 96_000;
    while (dlg.rtpQueuedBytes > MAX_QUEUE_BYTES && dlg.rtpQueue.length > 1) {
      const dropped = dlg.rtpQueue.shift();
      dlg.rtpQueuedBytes -= dropped.length;
    }
  }

  resetLine(line, endStatus = "answered") {
    const phone = line.remoteNumber;
    const sessionId = line.sessionId;
    const dur = line.connectedAt ? Math.round((Date.now() - line.connectedAt) / 1000) : 0;
    const recInfo = line._dialog?._recorder ? line._dialog._recorder.finish() : null;
    if (line._dialog) line._dialog._recorder = null;
    let endedSession = null;
    if (phone && sessionId) {
      endedSession = this.memory.endSession(phone, sessionId, dur, endStatus, recInfo);
    }
    this.cleanupRtp(line);
    const idx = line.lineId - 1;
    this.lines[idx] = this.createEmptyLine(line.lineId);
    if (this.activeAudioLine === line.lineId) {
      this.activeAudioLine = null;
    }
    this.emitState();

    if (endedSession && endedSession.messages?.length > 0) {
      void this.runPostCallAnalysis(phone, sessionId, dur);
    }
  }

  async runPostCallAnalysis(phone, sessionId, durationSec) {
    const session = this.memory.getSession(phone, sessionId);
    if (!session || !session.messages?.length) return;
    if (!this.ollamaApiKey) return;

    this.memory.setSessionAnalysis(phone, sessionId, "analyzing", null);
    this.emitState();

    const result = await analyzeCallTranscript({
      phone,
      messages: session.messages,
      durationSec: durationSec ?? session.durationSec ?? 0,
      baseUrl: this.ollamaBaseUrl,
      model: this.ollamaModel,
      apiKey: this.ollamaApiKey,
    });

    if (result && !result.error) {
      this.memory.setSessionAnalysis(phone, sessionId, "done", result);
      this.crm.applyPostCallAnalysis(phone, sessionId, result);
    } else {
      this.memory.setSessionAnalysis(phone, sessionId, "error", result);
    }
    this.emitState();
  }

  _refreshLineOpenTicketCounts() {
    for (const l of this.lines) {
      l.crmOpenTickets = l.state !== "idle" && l.remoteNumber
        ? this.crm.getOpenTicketCount(l.remoteNumber)
        : 0;
    }
  }

  handleCallbackRequest(line, evt) {
    const { ticket, created } = this.crm.createCallbackTicket({
      phone: evt.phone || line.remoteNumber,
      callbackPhone: evt.phone || line.remoteNumber,
      callerPhone: evt.callerPhone || line.remoteNumber,
      customerName: evt.name || line.crmName,
      company: evt.company || line.crmCompany,
      message: evt.message || "",
      sessionId: line.sessionId || null,
      lineId: line.lineId,
    });
    if (ticket.customerName && ticket.customerName !== line.crmName) {
      line.crmName = ticket.customerName;
    }
    if (ticket.company && !line.crmCompany) {
      line.crmCompany = ticket.company;
    }
    this._refreshLineOpenTicketCounts();
    if (created) {
      this.emit("callback_ticket", ticket);
    }
    this.emitState();
    return ticket;
  }

  crmUpsertCustomer(input) {
    const cust = this.crm.upsertCustomer(input);
    this.emitState();
    return cust;
  }

  crmCreateTicket(input) {
    const ticket = this.crm.createTicket(input);
    this._refreshLineOpenTicketCounts();
    this.emitState();
    return ticket;
  }

  crmUpdateTicket(id, patch, actor = "") {
    const ticket = this.crm.updateTicket(id, patch, actor);
    this._refreshLineOpenTicketCounts();
    this.emitState();
    return ticket;
  }

  crmAcknowledgeTicket(id, actor = "พนักงาน") {
    const ticket = this.crm.acknowledgeTicket(id, actor);
    this._refreshLineOpenTicketCounts();
    this.emitState();
    return ticket;
  }

  crmCloseTicket(id, actor = "พนักงาน", note = "") {
    const ticket = this.crm.closeTicket(id, actor, note);
    this._refreshLineOpenTicketCounts();
    this.emitState();
    return ticket;
  }

  crmDeleteTicket(id) {
    this.crm.deleteTicket(id);
    this._refreshLineOpenTicketCounts();
    this.emitState();
  }

  async setLineLanguage(lineId, language) {
    const line = this.lines[lineId - 1];
    if (!line || line.state === "idle") return;
    const lang = language && VALID_LANGUAGES.has(language) ? language : null;
    line.language = lang;
    line.isNewCaller = !lang;
    if (line.remoteNumber) {
      this.crm.setCustomerLanguage(line.remoteNumber, lang);
    }
    if (line._dialog?._jarvis) {
      await line._dialog._jarvis.pushLanguage(lang, !lang, true);
    }
    this.emitState();
  }

  findLineByCallId(callId) {
    return this.lines.find((l) => l._dialog?.callId === callId);
  }

  handleSipMessage(raw, rinfo) {
    const msg = parseSipMessage(raw);
    if (!msg.startLine) return;

    if (msg.startLine.startsWith("SIP/2.0 ")) {
      this.handleSipResponse(msg);
    } else {
      this.handleSipRequest(msg, rinfo);
    }
  }

  handleSipResponse(msg) {
    const m = msg.startLine.match(/^SIP\/2\.0\s+(\d{3})\s*(.*)$/);
    if (!m) return;
    const code = Number(m[1]);
    const reason = m[2] || "";
    const cseqHdr = getHeader(msg, "CSeq");
    const cseqParts = cseqHdr.split(/\s+/);
    const cseqNum = Number(cseqParts[0] || 0);
    const method = (cseqParts[1] || "").toUpperCase();
    const callId = getHeader(msg, "Call-ID");

    if (method === "REGISTER") {
      if (code === 200) {
        this.trunk.status = "registered";
        this.trunk.lastRegisterAt = new Date().toISOString();
        this.trunk.error = null;
        this.emitState();
      } else if (code === 401 || code === 407) {
        const hdrIn = code === 401 ? "WWW-Authenticate" : "Proxy-Authenticate";
        const hdrOut = code === 401 ? "Authorization" : "Proxy-Authorization";
        const challenge = parseAuthParams(getHeader(msg, hdrIn));
        if (challenge.nonce) {
          const authVal = buildDigestHeader({
            method: "REGISTER",
            uri: `sip:${this.host}`,
            username: this.user,
            password: this.password,
            challenge,
          });
          this.sendRegister(authVal, hdrOut);
        }
      } else if (code >= 400) {
        this.trunk.status = "error";
        this.trunk.error = `REGISTER ${code} ${reason}`;
        this.emitState();
      }
      return;
    }

    if (method === "OPTIONS") return;

    const line = this.findLineByCallId(callId);
    if (!line || !line._dialog) return;
    const dlg = line._dialog;

    if (method === "INVITE") {
      const toHdr = getHeader(msg, "To") || dlg.toHdr;
      if (code === 100) return;
      if (code === 180 || code === 183) {
        line.state = "ringing_out";
        const rtp = parseSdpRtp(msg.body);
        if (rtp.ip && rtp.port) {
          dlg.remoteRtpIp = rtp.ip;
          dlg.remoteRtpPort = rtp.port;
        }
        this.emitState();
        return;
      }

      if (code === 401 || code === 407) {
        this.sendAck(dlg.reqUri, dlg.fromHdr, toHdr, callId, cseqNum, dlg.activeBranch);
        if (dlg.authAttempts >= 2) {
          this.addHistory({
            direction: line.direction,
            number: line.remoteNumber,
            status: "failed",
            durationSec: 0,
          });
          this.resetLine(line);
          return;
        }
        dlg.authAttempts++;
        const hdrIn = code === 401 ? "WWW-Authenticate" : "Proxy-Authenticate";
        const hdrOut = code === 401 ? "Authorization" : "Proxy-Authorization";
        const challenge = parseAuthParams(getHeader(msg, hdrIn));
        const authVal = buildDigestHeader({
          method: "INVITE",
          uri: dlg.reqUri,
          username: this.user,
          password: this.password,
          challenge,
        });
        dlg.cseq++;
        dlg.activeBranch = `z9hG4bK${randId(10)}`;
        dlg.authHeader = authVal;
        dlg.authHeaderName = hdrOut;
        this.sendInviteMsg(line);
        return;
      }

      if (code === 200) {
        const contact = getHeader(msg, "Contact");
        const targetUri = contact ? extractUri(contact) : dlg.reqUri;
        const recordRoutes = getHeaders(msg, "Record-Route").reverse();
        dlg.targetUri = targetUri;
        dlg.toHdr = toHdr;
        dlg.routes = recordRoutes;
        const rtp = parseSdpRtp(msg.body);
        if (rtp.ip && rtp.port) {
          dlg.remoteRtpIp = rtp.ip;
          dlg.remoteRtpPort = rtp.port;
        }
        this.sendAck(targetUri, dlg.fromHdr, toHdr, callId, cseqNum, `z9hG4bK${randId(10)}`, recordRoutes, dlg.authHeader, dlg.authHeaderName);

        if (line.state !== "connected" && line.state !== "held") {
          line.state = "connected";
          line.connectedAt = Date.now();
          this.activeAudioLine = line.lineId;
          this.emitState();
        }
        return;
      }

      if (code >= 400) {
        this.sendAck(dlg.reqUri, dlg.fromHdr, toHdr, callId, cseqNum, dlg.activeBranch);
        this.addHistory({
          direction: line.direction,
          number: line.remoteNumber,
          status: code === 486 ? "busy" : "failed",
          durationSec: 0,
        });
        this.resetLine(line);
      }
    }
  }

  handleSipRequest(msg, rinfo) {
    const m = msg.startLine.match(/^([A-Z]+)\s+(\S+)\s+SIP\/2\.0$/);
    if (!m) return;
    const method = m[1];
    const reqUri = m[2];
    const callId = getHeader(msg, "Call-ID");
    const fromHdr = getHeader(msg, "From");
    const toHdr = getHeader(msg, "To");
    const cseqHdr = getHeader(msg, "CSeq");
    const vias = getHeaders(msg, "Via");
    const recordRoutes = getHeaders(msg, "Record-Route");

    if (method === "OPTIONS") {
      this.sendSipReply(200, "OK", vias, fromHdr, ensureToTag(toHdr, randId(8)), callId, cseqHdr);
      return;
    }

    if (method === "INVITE") {
      const existing = this.findLineByCallId(callId);
      if (existing) {
        // Re-INVITE (e.g. session refresh or hold)
        const rtp = parseSdpRtp(msg.body);
        if (rtp.ip && rtp.port) {
          existing._dialog.remoteRtpIp = rtp.ip;
          existing._dialog.remoteRtpPort = rtp.port;
        }
        const sdp = buildSdp(this.localIp, existing._dialog.rtpPort, existing.held ? "sendonly" : "sendrecv");
        this.sendSipReply(200, "OK", vias, fromHdr, existing._dialog.toHdr, callId, cseqHdr, sdp, recordRoutes);
        return;
      }

      const freeLine = this.lines.find((l) => l.state === "idle");
      if (!freeLine) {
        this.sendSipReply(486, "Busy Here", vias, fromHdr, ensureToTag(toHdr, randId(8)), callId, cseqHdr);
        return;
      }

      const caller = parseCallerInfo(getHeader(msg, "P-Asserted-Identity") || fromHdr);
      const localTag = randId(8);
      const taggedTo = ensureToTag(toHdr, localTag);
      const rtp = parseSdpRtp(msg.body);
      const contact = getHeader(msg, "Contact");

      freeLine.state = "ringing_in";
      freeLine.direction = "inbound";
      freeLine.remoteNumber = caller.number;
      freeLine.remoteName = caller.displayName;
      freeLine.startedAt = Date.now();
      freeLine.connectedAt = null;
      freeLine.muted = false;
      freeLine.held = false;
      const existingBeforeCall = this.crm.getCustomer(caller.number);
      const startedMem = this.memory.startSession(caller.number, "inbound");
      const crmCust = this.crm.ensureCustomerOnCall(caller.number);
      freeLine.sessionId = startedMem?.sessionId || null;
      freeLine.callerTotalCalls = startedMem?.caller?.totalCalls || 1;
      freeLine.callerHasHistory = Boolean(
        this.memory.buildAiContextPrompt(caller.number, freeLine.sessionId)
      );
      freeLine.crmName = crmCust?.name || "";
      freeLine.crmCompany = crmCust?.company || "";
      freeLine.crmTier = crmCust?.tier || null;
      freeLine.crmOpenTickets = this.crm.getOpenTicketCount(caller.number);
      freeLine.language = crmCust?.preferredLanguage || "th";
      freeLine.isNewCaller = !existingBeforeCall || !crmCust?.preferredLanguage;
      freeLine._dialog = {
        callId,
        fromHdr,
        toHdr: taggedTo,
        vias,
        cseqHdr,
        reqUri,
        targetUri: contact ? extractUri(contact) : caller.uri,
        routes: recordRoutes,
        remoteRtpIp: rtp.ip,
        remoteRtpPort: rtp.port,
        rtpPort: null,
        rtpSocket: null,
        cseq: 1,
      };

      this.sendSipReply(100, "Trying", vias, fromHdr, taggedTo, callId, cseqHdr);
      this.sendSipReply(180, "Ringing", vias, fromHdr, taggedTo, callId, cseqHdr, "", recordRoutes);
      this.emitState();

      if (this.autoAnswerAi) {
        const lineIdToAnswer = freeLine.lineId;
        setTimeout(() => {
          void this.answerWithAi(lineIdToAnswer);
        }, 80);
      }
      return;
    }

    if (method === "ACK") {
      return;
    }

    if (method === "CANCEL") {
      const line = this.findLineByCallId(callId);
      this.sendSipReply(200, "OK", vias, fromHdr, toHdr, callId, cseqHdr);
      if (line && line.state === "ringing_in") {
        this.sendSipReply(487, "Request Terminated", line._dialog.vias, line._dialog.fromHdr, line._dialog.toHdr, callId, line._dialog.cseqHdr);
        this.addHistory({
          direction: "inbound",
          number: line.remoteNumber,
          status: "missed",
          durationSec: 0,
        });
        this.resetLine(line);
      }
      return;
    }

    if (method === "BYE") {
      const line = this.findLineByCallId(callId);
      this.sendSipReply(200, "OK", vias, fromHdr, toHdr, callId, cseqHdr);
      if (line) {
        const dur = line.connectedAt ? Math.round((Date.now() - line.connectedAt) / 1000) : 0;
        this.addHistory({
          direction: line.direction || "inbound",
          number: line.remoteNumber,
          status: "answered",
          durationSec: dur,
        });
        this.resetLine(line);
      }
    }
  }

  sendSipReply(code, reason, vias, fromHdr, toHdr, callId, cseqHdr, sdp = "", recordRoutes = []) {
    const lines = [`SIP/2.0 ${code} ${reason}`];
    for (const v of vias) lines.push(`Via: ${v}`);
    for (const rr of recordRoutes) lines.push(`Record-Route: ${rr}`);
    lines.push(`From: ${fromHdr}`);
    lines.push(`To: ${toHdr}`);
    lines.push(`Call-ID: ${callId}`);
    lines.push(`CSeq: ${cseqHdr}`);
    lines.push(`Contact: <sip:${this.user}@${this.localIp}:${this.localPort}>`);
    lines.push("User-Agent: YalecomConsole/1.0");
    if (sdp) {
      lines.push("Content-Type: application/sdp");
      lines.push(`Content-Length: ${Buffer.byteLength(sdp, "utf8")}`);
    } else {
      lines.push("Content-Length: 0");
    }
    this.sendRaw(lines.join("\r\n") + "\r\n\r\n" + sdp);
  }

  sendAck(reqUri, fromHdr, toHdr, callId, cseq, branch, routes = [], authHdr = null, authHdrName = "Authorization") {
    const lines = [
      `ACK ${reqUri} SIP/2.0`,
      `Via: SIP/2.0/UDP ${this.localIp}:${this.localPort};branch=${branch};rport`,
      "Max-Forwards: 70",
      `From: ${fromHdr}`,
      `To: ${toHdr}`,
      `Call-ID: ${callId}`,
      `CSeq: ${cseq} ACK`,
    ];
    for (const r of routes) lines.push(`Route: ${r}`);
    if (authHdr) lines.push(`${authHdrName}: ${authHdr}`);
    lines.push("Content-Length: 0");
    this.sendRaw(lines.join("\r\n") + "\r\n\r\n");
  }

  sendInviteMsg(line) {
    const dlg = line._dialog;
    const sdp = buildSdp(this.localIp, dlg.rtpPort, line.held ? "sendonly" : "sendrecv");
    const lines = [
      `INVITE ${dlg.reqUri} SIP/2.0`,
      `Via: SIP/2.0/UDP ${this.localIp}:${this.localPort};branch=${dlg.activeBranch};rport`,
      "Max-Forwards: 70",
      `From: ${dlg.fromHdr}`,
      `To: ${dlg.toHdr}`,
      `Call-ID: ${dlg.callId}`,
      `CSeq: ${dlg.cseq} INVITE`,
      `Contact: <sip:${this.user}@${this.localIp}:${this.localPort}>`,
      "User-Agent: YalecomConsole/1.0",
      "Allow: INVITE, ACK, CANCEL, OPTIONS, BYE, REFER, NOTIFY, INFO, UPDATE",
      "Content-Type: application/sdp",
    ];
    if (dlg.authHeader) lines.push(`${dlg.authHeaderName}: ${dlg.authHeader}`);
    lines.push(`Content-Length: ${Buffer.byteLength(sdp, "utf8")}`);
    this.sendRaw(lines.join("\r\n") + "\r\n\r\n" + sdp);
  }

  sendDialogReq(line, method, extraHeaders = [], body = "") {
    const dlg = line._dialog;
    if (!dlg) return;
    dlg.cseq = (dlg.cseq || 1) + 1;
    const branch = `z9hG4bK${randId(10)}`;
    const lines = [
      `${method} ${dlg.targetUri || dlg.reqUri} SIP/2.0`,
      `Via: SIP/2.0/UDP ${this.localIp}:${this.localPort};branch=${branch};rport`,
      "Max-Forwards: 70",
      `From: ${line.direction === "inbound" ? dlg.toHdr : dlg.fromHdr}`,
      `To: ${line.direction === "inbound" ? dlg.fromHdr : dlg.toHdr}`,
      `Call-ID: ${dlg.callId}`,
      `CSeq: ${dlg.cseq} ${method}`,
    ];
    for (const r of dlg.routes || []) lines.push(`Route: ${r}`);
    for (const h of extraHeaders) lines.push(h);
    lines.push(`Content-Length: ${Buffer.byteLength(body, "utf8")}`);
    this.sendRaw(lines.join("\r\n") + "\r\n\r\n" + body);
  }

  async answer(lineId, { enableWebAudio = false } = {}) {
    const line = this.lines[lineId - 1];
    if (!line || line.state !== "ringing_in" || !line._dialog) return;

    const rtpPort = await this.openRtpSocket(line);
    const sdp = buildSdp(this.localIp, rtpPort, "sendrecv");
    const dlg = line._dialog;

    this.sendSipReply(200, "OK", dlg.vias, dlg.fromHdr, dlg.toHdr, dlg.callId, dlg.cseqHdr, sdp, dlg.routes);
    line.state = "connected";
    line.connectedAt = Date.now();
    if (enableWebAudio) {
      this.activeAudioLine = line.lineId;
    }
    this.emitState();
  }

  reject(lineId) {
    const line = this.lines[lineId - 1];
    if (!line || line.state !== "ringing_in" || !line._dialog) return;
    const dlg = line._dialog;
    this.sendSipReply(486, "Busy Here", dlg.vias, dlg.fromHdr, dlg.toHdr, dlg.callId, dlg.cseqHdr);
    this.addHistory({
      direction: "inbound",
      number: line.remoteNumber,
      status: "rejected",
      durationSec: 0,
    });
    this.resetLine(line);
  }

  async dial(number, requestedLineId = null) {
    const cleanNum = String(number || "").replace(/[^\d+*#]/g, "");
    if (!cleanNum) return { ok: false, error: "กรุณาระบุเบอร์โทรศัพท์" };

    const line = requestedLineId
      ? this.lines[requestedLineId - 1]
      : this.lines.find((l) => l.state === "idle");

    if (!line || line.state !== "idle") {
      return { ok: false, error: "ไม่มีคู่สายว่างในขณะนี้ (ใช้งานครบ 5 คู่สายแล้ว)" };
    }

    const callId = `${randId(16)}@${this.localIp}`;
    const tag = randId(8);
    const reqUri = `sip:${cleanNum}@${this.host}`;

    line.state = "dialing";
    line.direction = "outbound";
    line.remoteNumber = cleanNum;
    line.remoteName = "";
    line.startedAt = Date.now();
    line.connectedAt = null;
    line.muted = false;
    line.held = false;
    const startedMem = this.memory.startSession(cleanNum, "outbound");
    line.sessionId = startedMem?.sessionId || null;
    line.callerTotalCalls = startedMem?.caller?.totalCalls || 1;
    line.callerHasHistory = Boolean(
      this.memory.buildAiContextPrompt(cleanNum, line.sessionId)
    );
    line._dialog = {
      callId,
      reqUri,
      targetUri: reqUri,
      fromHdr: `<sip:${this.user}@${this.host}>;tag=${tag}`,
      toHdr: `<sip:${cleanNum}@${this.host}>`,
      cseq: 1,
      activeBranch: `z9hG4bK${randId(10)}`,
      authAttempts: 0,
      authHeader: null,
      authHeaderName: "Authorization",
      routes: [],
      remoteRtpIp: null,
      remoteRtpPort: null,
      rtpPort: null,
      rtpSocket: null,
    };

    await this.openRtpSocket(line);
    this.sendInviteMsg(line);
    this.activeAudioLine = line.lineId;
    this.emitState();
    return { ok: true, lineId: line.lineId };
  }

  hangup(lineId) {
    const line = this.lines[lineId - 1];
    if (!line || line.state === "idle") return;
    if (line.state === "ringing_in") {
      this.reject(lineId);
      return;
    }
    const dlg = line._dialog;
    if (line.state === "dialing" || line.state === "ringing_out") {
      const lines = [
        `CANCEL ${dlg.reqUri} SIP/2.0`,
        `Via: SIP/2.0/UDP ${this.localIp}:${this.localPort};branch=${dlg.activeBranch};rport`,
        "Max-Forwards: 70",
        `From: ${dlg.fromHdr}`,
        `To: ${dlg.toHdr}`,
        `Call-ID: ${dlg.callId}`,
        `CSeq: ${dlg.cseq} CANCEL`,
        "Content-Length: 0",
      ];
      this.sendRaw(lines.join("\r\n") + "\r\n\r\n");
      this.addHistory({
        direction: "outbound",
        number: line.remoteNumber,
        status: "cancelled",
        durationSec: 0,
      });
      this.resetLine(line);
      return;
    }

    this.sendDialogReq(line, "BYE");
    const dur = line.connectedAt ? Math.round((Date.now() - line.connectedAt) / 1000) : 0;
    this.addHistory({
      direction: line.direction || "outbound",
      number: line.remoteNumber,
      status: "answered",
      durationSec: dur,
    });
    this.resetLine(line);
  }

  toggleMute(lineId) {
    const line = this.lines[lineId - 1];
    if (!line || line.state !== "connected") return;
    line.muted = !line.muted;
    this.emitState();
  }

  toggleHold(lineId) {
    const line = this.lines[lineId - 1];
    if (!line || (line.state !== "connected" && line.state !== "held")) return;
    line.held = !line.held;
    line.state = line.held ? "held" : "connected";
    this.emitState();
  }

  sendDtmf(lineId, digit) {
    const line = this.lines[lineId - 1];
    if (!line || line.state !== "connected") return;
    const body = `Signal=${digit}\r\nDuration=160\r\n`;
    this.sendDialogReq(line, "INFO", ["Content-Type: application/dtmf-relay"], body);
  }

  setActiveAudioLine(lineId) {
    if (!lineId) {
      this.activeAudioLine = null;
      this.emitState();
      return;
    }
    if (lineId >= 1 && lineId <= this.maxLines) {
      const target = this.lines[lineId - 1];
      if (!target || target.aiActive) return;
      this.activeAudioLine = lineId;
      this.emitState();
    }
  }

  setAutoAnswerAi(enabled) {
    this.autoAnswerAi = Boolean(enabled);
    this.emitState();
  }

  async answerWithAi(lineId) {
    const line = this.lines[lineId - 1];
    if (!line || line.state !== "ringing_in") return;
    await this.answer(lineId);
    await this.attachAiToLine(lineId);
  }

  async attachAiToLine(lineId) {
    const line = this.lines[lineId - 1];
    if (!line || line.state !== "connected" || !line._dialog) return;
    if (line._dialog._jarvis) return;

    line.aiActive = true;
    if (this.activeAudioLine === lineId) {
      this.activeAudioLine = null;
    }
    line.aiTranscript = "กำลังเชื่อมต่อ J.A.R.V.I.S...";
    this.emitState();

    const memCtx = this.memory.buildAiContextPrompt(line.remoteNumber, line.sessionId);
    const crmCtx = this.crm.buildCrmPromptSnippet(line.remoteNumber);
    const cust = this.crm.getCustomer(line.remoteNumber);
    const prevRec = this.memory.getCaller(line.remoteNumber);
    const prevDoneSess = (prevRec?.sessions || []).find((s) => s.id !== line.sessionId && (s.analysis?.topic || s.messages?.length > 0));
    const lastTopic = prevDoneSess?.analysis?.topic || cust?.tags?.[cust.tags.length - 1] || "";
    const normalizedPhone = normalizePhone(line.remoteNumber);
    const cleanedCustName = cleanCustomerName(cust?.name || "");
    if (cleanedCustName && cleanedCustName !== cust?.name) {
      try {
        this.crm.upsertCustomer({ phone: normalizedPhone, name: cleanedCustName });
        line.crmName = cleanedCustName;
      } catch {}
    }
    const callerMeta = {
      phone: normalizedPhone,
      name: cleanedCustName,
      company: cust?.company || "",
      totalCalls: Math.max(Number(cust?.totalCalls || 0), Number(line.callerTotalCalls || 0)),
      lastTopic: String(lastTopic || "").slice(0, 80),
    };
    // J.A.R.V.I.S asks the language question in code (_lang_turn in jarvis/voice_agent.py); told to ask as well,
    // the LLM asked or switched on misheard words
    const languageChangeRule = "ห้ามเปลี่ยนภาษาหรือถามเรื่องเปลี่ยนภาษาเอง ระบบจะถามยืนยันกับลูกค้าเองเมื่อได้ยินภาษาอื่นชัดเจน";
    const langInstruction = line.isNewCaller
      ? `[สายใหม่ค่าเริ่มต้นภาษาไทย] คุยด้วยภาษาไทยเป็นค่าเริ่มต้นและแจ้งลูกค้าว่าคุยได้ทุกภาษา ${languageChangeRule}`
      : `[ภาษาของลูกค้า] ลูกค้าเบอร์เดิมใช้ ${LANGUAGE_LABELS[line.language || "th"]} กรุณาฟังและแปลคำตอบเป็น ${LANGUAGE_LABELS[line.language || "th"]} ${languageChangeRule}`;
    const initialContext = [langInstruction, crmCtx ? `[ข้อมูล CRM] ${crmCtx}` : "", memCtx || ""]
      .filter(Boolean)
      .join(" ")
      .slice(0, 700) || null;
    const session = new JarvisCallSession({
      lineId,
      jarvisUrl: this.jarvisUrl,
      initialContext,
      callerMeta,
      language: line.language,
      isNewCaller: line.isNewCaller,
      onAudioFromJarvis: (pcmuPayload) => {
        if (!line.aiActive || line.state !== "connected") return;
        this.sendRtpAudio(lineId, pcmuPayload);
      },
      onFlushAudio: () => {
        this.clearRtpQueue(lineId);
      },
      onLanguageDetected: (detectedLang) => {
        if (!VALID_LANGUAGES.has(detectedLang) || line.language === detectedLang) return;
        line.language = detectedLang;
        line.pendingLanguageConfirm = null;
        line.isNewCaller = false;
        this.crm.setCustomerLanguage(line.remoteNumber, detectedLang);
        this.emitState();
      },
      onSubtitle: (evt) => {
        if (!evt) return;
        if (evt.kind === "callback_request") {
          this.handleCallbackRequest(line, evt);
          return;
        }
        if (!line.aiActive) return;
        if (evt.kind === "partial" && evt.text) {
          line.aiPartial = evt.text;
          line.aiStatus = "ผู้โทรกำลังพูด...";
          this.emitState();
          return;
        }
        if (evt.kind === "status" && evt.text) {
          line.aiStatus = evt.text;
          this.emitState();
          return;
        }
        if (evt.kind === "done") {
          line.aiStatus = "พร้อมรับฟัง";
          line.aiPartial = "";
          this.emitState();
          return;
        }
        if (evt.kind === "caller_name" && evt.name) {
          const syncedName = cleanCustomerName(evt.name);
          if (syncedName && syncedName !== line.crmName) {
            line.crmName = syncedName;
            try {
              this.crm.upsertCustomer({ phone: line.remoteNumber, name: syncedName });
            } catch {}
            this.emitState();
          }
          return;
        }
        if ((evt.kind === "user" || evt.kind === "bot") && evt.text) {
          const role = evt.kind === "user" ? "caller" : "ai";
          const isFiller = Boolean(evt.filler);
          const prevBotMsg = (line.liveMessages || []).filter((m) => m.role === "ai").slice(-1)[0]?.text || "";
          if (!isFiller) {
            this.memory.appendMessage(line.remoteNumber, line.sessionId, role, evt.text);
            line.liveMessages = [
              ...(line.liveMessages || []).slice(-7),
              { at: new Date().toISOString(), role, text: evt.text },
            ];
          }
          if (evt.kind === "user") {
            line.aiPartial = "";
            line.aiStatus = "กำลังค้นหาคำตอบ...";
            line.liveSignal = detectLiveCallSignal(line.liveMessages);
            // Auto-capture (or explicitly correct) caller's name into CRM + sync to J.A.R.V.I.S immediately
            const detected = extractCallerNameFromText(evt.text, prevBotMsg);
            if (detected && detected.name && (!line.crmName || detected.explicit)) {
              line.crmName = detected.name;
              try {
                this.crm.upsertCustomer({ phone: line.remoteNumber, name: detected.name });
                void session.pushLanguage(line.language, false, false, null, null, {
                  ...(session.callerMeta || {}),
                  phone: normalizedPhone,
                  name: detected.name,
                });
              } catch {}
            }
            // Language detection & switching is driven directly by J.A.R.V.I.S Multilingual Whisper (emits SSE kind="lang" -> onLanguageDetected)
            // so we avoid racing/overwriting CALL_LANG with conflicting POST /language calls mid-turn.
          } else {
            line.aiStatus = isFiller ? "กำลังเช็กข้อมูล..." : "J.A.R.V.I.S กำลังพูด...";
          }
          const prefix = evt.kind === "user" ? "ผู้โทร: " : "J.A.R.V.I.S: ";
          line.aiTranscript = `${prefix}${evt.text}`;
          this.emitState();
        }
      },
      onStateChange: (st) => {
        if (st === "failed" || st === "closed") {
          if (line.aiActive) {
            line.aiTranscript = "การเชื่อมต่อ J.A.R.V.I.S สิ้นสุดลง";
            this.emitState();
          }
        }
      },
    });

    line._dialog._jarvis = session;
    try {
      await session.connect();
      if (line.aiActive) {
        line.aiTranscript = "J.A.R.V.I.S พร้อมสนทนาแล้ว";
        this.emitState();
      }
    } catch (err) {
      line.aiActive = false;
      line.aiTranscript = `เชื่อมต่อ J.A.R.V.I.S ไม่สำเร็จ: ${err.message}`;
      line._dialog._jarvis = null;
      this.emitState();
    }
  }

  async takeOverFromAi(lineId) {
    const line = this.lines[lineId - 1];
    if (!line || !line._dialog) return;
    line.aiActive = false;
    line.aiTranscript = "";
    if (line._dialog._jarvis) {
      await line._dialog._jarvis.close();
      line._dialog._jarvis = null;
    }
    this.activeAudioLine = lineId;
    this.emitState();
  }
}
