import crypto from "node:crypto";
import dgram from "node:dgram";
import dns from "node:dns/promises";
import { EventEmitter } from "node:events";
import os from "node:os";
import { analyzeCallTranscript } from "./call-analyzer.mjs";
import { CallMemoryStore } from "./call-memory.mjs";
import { JavisCallSession } from "./javis-bridge.mjs";

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
    this.javisUrl = config.javisUrl || "http://127.0.0.1:7861";
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
      callerTotalCalls: 0,
      callerHasHistory: false,
      sessionId: null,
      // internal SIP dialog state (stripped before sending to client)
      _dialog: null,
    };
  }

  publicState() {
    return {
      trunk: this.trunk,
      autoAnswerAi: this.autoAnswerAi,
      javisUrl: this.javisUrl,
      activeAudioLine: this.activeAudioLine,
      lines: this.lines.map(({ _dialog, ...rest }) => rest),
      history: this.history.slice(0, 30),
      callers: this.memory.listRecentCallers(25),
      callLogs: this.memory.listCallLogs(40),
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
      if (line.aiActive && dlg._javis) {
        dlg._javis.sendPcmu(payload);
      }
      this.emit("rtp", { lineId: line.lineId, pt, payload });
    });

    return rtpPort;
  }

  cleanupRtp(line) {
    if (line._dialog?._javis) {
      void line._dialog._javis.close();
      line._dialog._javis = null;
    }
    if (line._dialog?.rtpSocket) {
      try { line._dialog.rtpSocket.close(); } catch {}
      line._dialog.rtpSocket = null;
    }
  }

  sendRtpAudio(lineId, pcmuPayload) {
    const line = this.lines[lineId - 1];
    if (!line || line.muted || line.held) return;
    if (line.state !== "connected") return;
    const dlg = line._dialog;
    if (!dlg?.rtpSocket || !dlg.remoteRtpIp || !dlg.remoteRtpPort) return;

    const pkt = Buffer.allocUnsafe(12 + pcmuPayload.length);
    pkt[0] = 0x80; // V=2, P=0, X=0, CC=0
    pkt[1] = 0x00; // PT=0 (PCMU)
    dlg.rtpSeq = (dlg.rtpSeq + 1) & 0xffff;
    pkt.writeUInt16BE(dlg.rtpSeq, 2);
    dlg.rtpTs = (dlg.rtpTs + pcmuPayload.length) >>> 0;
    pkt.writeUInt32BE(dlg.rtpTs, 4);
    pkt.writeUInt32BE(dlg.rtpSsrc, 8);
    pcmuPayload.copy(pkt, 12);

    dlg.rtpSocket.send(pkt, dlg.remoteRtpPort, dlg.remoteRtpIp);
  }

  resetLine(line, endStatus = "answered") {
    const phone = line.remoteNumber;
    const sessionId = line.sessionId;
    const dur = line.connectedAt ? Math.round((Date.now() - line.connectedAt) / 1000) : 0;
    let endedSession = null;
    if (phone && sessionId) {
      endedSession = this.memory.endSession(phone, sessionId, dur, endStatus);
    }
    this.cleanupRtp(line);
    const idx = line.lineId - 1;
    this.lines[idx] = this.createEmptyLine(line.lineId);
    if (this.activeAudioLine === line.lineId) {
      const another = this.lines.find((l) => l.state === "connected");
      this.activeAudioLine = another ? another.lineId : null;
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
    } else {
      this.memory.setSessionAnalysis(phone, sessionId, "error", result);
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
      const startedMem = this.memory.startSession(caller.number, "inbound");
      freeLine.sessionId = startedMem?.sessionId || null;
      freeLine.callerTotalCalls = startedMem?.caller?.totalCalls || 1;
      freeLine.callerHasHistory = Boolean(
        this.memory.buildAiContextPrompt(caller.number, freeLine.sessionId)
      );
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
        }, 350);
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

  async answer(lineId) {
    const line = this.lines[lineId - 1];
    if (!line || line.state !== "ringing_in" || !line._dialog) return;

    const rtpPort = await this.openRtpSocket(line);
    const sdp = buildSdp(this.localIp, rtpPort, "sendrecv");
    const dlg = line._dialog;

    this.sendSipReply(200, "OK", dlg.vias, dlg.fromHdr, dlg.toHdr, dlg.callId, dlg.cseqHdr, sdp, dlg.routes);
    line.state = "connected";
    line.connectedAt = Date.now();
    this.activeAudioLine = line.lineId;
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
    if (lineId >= 1 && lineId <= this.maxLines) {
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
    if (line._dialog._javis) return;

    line.aiActive = true;
    line.aiTranscript = "กำลังเชื่อมต่อ Javis AI...";
    this.emitState();

    const initialContext = this.memory.buildAiContextPrompt(line.remoteNumber, line.sessionId);
    const session = new JavisCallSession({
      lineId,
      javisUrl: this.javisUrl,
      initialContext,
      onAudioFromJavis: (pcmuPayload) => {
        if (!line.aiActive || line.state !== "connected") return;
        this.sendRtpAudio(lineId, pcmuPayload);
        this.emit("rtp", { lineId, pt: 0, payload: pcmuPayload });
      },
      onSubtitle: (evt) => {
        if (!line.aiActive) return;
        if (evt?.text) {
          const role = evt.kind === "user" ? "caller" : "ai";
          this.memory.appendMessage(line.remoteNumber, line.sessionId, role, evt.text);
          const prefix = evt.kind === "user" ? "ผู้โทร: " : "Javis: ";
          line.aiTranscript = `${prefix}${evt.text}`;
          this.emitState();
        }
      },
      onStateChange: (st) => {
        if (st === "failed" || st === "closed") {
          if (line.aiActive) {
            line.aiTranscript = "การเชื่อมต่อ Javis สิ้นสุดลง";
            this.emitState();
          }
        }
      },
    });

    line._dialog._javis = session;
    try {
      await session.connect();
      if (line.aiActive) {
        line.aiTranscript = "Javis AI พร้อมสนทนาแล้ว";
        this.emitState();
      }
    } catch (err) {
      line.aiActive = false;
      line.aiTranscript = `เชื่อมต่อ Javis ไม่สำเร็จ: ${err.message}`;
      line._dialog._javis = null;
      this.emitState();
    }
  }

  async takeOverFromAi(lineId) {
    const line = this.lines[lineId - 1];
    if (!line || !line._dialog) return;
    line.aiActive = false;
    line.aiTranscript = "";
    if (line._dialog._javis) {
      await line._dialog._javis.close();
      line._dialog._javis = null;
    }
    this.activeAudioLine = lineId;
    this.emitState();
  }
}
