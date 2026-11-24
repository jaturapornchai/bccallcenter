import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import next from "next";
import { WebSocketServer } from "ws";
import {
  buildClearSessionCookie,
  buildSessionSetCookie,
  collectSecretTerms,
  createSessionToken,
  sanitizeFreeTextForViewer,
  sanitizeStateForViewer,
  sanitizeTicketForViewer,
  verifyGoogleIdToken,
  verifySessionFromCookieHeader,
} from "./lib/auth.mjs";
import { getRecordingPath } from "./lib/call-recorder.mjs";
import { SipEngine } from "./lib/sip-engine.mjs";

const envPath = path.resolve(process.cwd(), ".env.local");
if (fs.existsSync(envPath) && typeof process.loadEnvFile === "function") {
  process.loadEnvFile(envPath);
}

const dev = process.env.NODE_ENV !== "production";
const hostname = "0.0.0.0";
const port = Number(process.env.PORT || 3000);

const app = next({ dev, hostname, port });
const handle = app.getRequestHandler();

const sip = new SipEngine({
  host: process.env.SIP_HOST || "",
  failoverHost: process.env.SIP_FAILOVER_HOST || "",
  port: Number(process.env.SIP_PORT || 5060),
  user: process.env.SIP_USER || "",
  password: process.env.SIP_PASSWORD || "",
  maxLines: Number(process.env.SIP_MAX_LINES || 5),
  jarvisUrl: process.env.JARVIS_URL || "http://127.0.0.1:7861",
  autoAnswerAi: process.env.AUTO_ANSWER_AI !== "false",
  ollamaBaseUrl: process.env.OLLAMA_BASE_URL || "https://ollama.com",
  ollamaModel: process.env.OLLAMA_MODEL || "deepseek-v4.1-flash",
  ollamaApiKey: process.env.OLLAMA_API_KEY || "",
});

function readJsonBody(req, maxBytes = 32768) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let total = 0;
    req.on("data", (chunk) => {
      total += chunk.length;
      if (total > maxBytes) {
        reject(new Error("Payload too large"));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => {
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}"));
      } catch {
        reject(new Error("Invalid JSON"));
      }
    });
    req.on("error", reject);
  });
}

function sendJson(res, status, data, extraHeaders = {}) {
  const body = JSON.stringify(data);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    ...extraHeaders,
  });
  res.end(body);
}

function isRequestHttps(req) {
  const proto = String(req.headers["x-forwarded-proto"] || "").toLowerCase();
  return proto === "https" || Boolean(req.socket?.encrypted);
}

function isDirectLoopback(req) {
  const addr = String(req.socket?.remoteAddress || "");
  const isLoop = addr === "127.0.0.1" || addr === "::1" || addr === "::ffff:127.0.0.1";
  const host = String(req.headers.host || "").toLowerCase();
  const localHost =
    host.startsWith("127.0.0.1:") ||
    host.startsWith("localhost:") ||
    host === "127.0.0.1" ||
    host === "localhost";
  const hasProxy = Boolean(
    req.headers["x-forwarded-for"] ||
    req.headers["x-forwarded-proto"] ||
    req.headers["x-forwarded-host"] ||
    req.headers["x-real-ip"]
  );
  return isLoop && localHost && !hasProxy;
}

app.prepare().then(async () => {
  await sip.start();

  const server = http.createServer(async (req, res) => {
    const urlPath = (req.url || "").split("?")[0];

    // --- Internal Loopback-Only Idle Check (no PII, blocked from public/proxy requests) ---
    if (urlPath === "/api/internal/idle" && req.method === "GET") {
      if (!isDirectLoopback(req)) {
        return sendJson(res, 403, { ok: false, error: "Forbidden" });
      }
      const activeCalls = sip.lines.filter((l) => l.state !== "idle").length;
      return sendJson(res, 200, { idle: activeCalls === 0, activeCalls });
    }

    // --- Auth Endpoints (Google Sign-In + HttpOnly Session Cookie) ---
    if (urlPath === "/api/auth/me" && req.method === "GET") {
      const session = verifySessionFromCookieHeader(req.headers.cookie);
      return sendJson(res, 200, {
        authenticated: Boolean(session),
        isAdmin: Boolean(session?.isAdmin),
        email: session?.email || null,
        name: session?.name || null,
        picture: session?.picture || null,
        googleClientId: process.env.GOOGLE_CLIENT_ID || "",
      });
    }

    if (urlPath === "/api/auth/google" && req.method === "POST") {
      try {
        const body = await readJsonBody(req);
        const user = await verifyGoogleIdToken(body.credential);
        const token = createSessionToken(user);
        const cookie = buildSessionSetCookie(token, isRequestHttps(req));
        return sendJson(
          res,
          200,
          {
            ok: true,
            authenticated: true,
            isAdmin: user.isAdmin,
            email: user.email,
            name: user.name,
            picture: user.picture,
            message: user.isAdmin
              ? "เข้าสู่ระบบผู้ดูแลสำเร็จ"
              : "เข้าสู่ระบบแล้ว (โหมดผู้ชมดูได้ทุกฟีเจอร์ — ข้อมูลที่เป็นความลับถูกปกปิดไว้ สงวนสิทธิ์เฉพาะผู้ดูแลระบบ)",
          },
          { "Set-Cookie": cookie }
        );
      } catch (err) {
        return sendJson(res, 401, { ok: false, error: err.message });
      }
    }

    if (urlPath === "/api/auth/logout" && req.method === "POST") {
      const cookie = buildClearSessionCookie(isRequestHttps(req));
      return sendJson(res, 200, { ok: true }, { "Set-Cookie": cookie });
    }

    // --- Admin-Only Call Recording Playback (with HTTP Range for Transcript Sync Seek) ---
    if (urlPath.startsWith("/api/recordings/") && req.method === "GET") {
      const session = verifySessionFromCookieHeader(req.headers.cookie);
      if (!session?.isAdmin) {
        return sendJson(res, 403, { ok: false, error: "สงวนสิทธิ์ไฟล์บันทึกเสียงเฉพาะผู้ดูแลระบบ" });
      }
      const sessionId = decodeURIComponent(urlPath.slice("/api/recordings/".length));
      const filePath = getRecordingPath(sessionId);
      if (!filePath || !fs.existsSync(filePath)) {
        return sendJson(res, 404, { ok: false, error: "ไม่พบไฟล์บันทึกเสียงของสายนี้" });
      }
      const stat = fs.statSync(filePath);
      const total = stat.size;
      const range = req.headers.range;
      if (range) {
        const m = /^bytes=(\d+)-(\d*)$/.exec(String(range));
        const start = m ? Math.min(Number(m[1]), total - 1) : 0;
        const end = m && m[2] ? Math.min(Number(m[2]), total - 1) : total - 1;
        res.writeHead(206, {
          "Content-Range": `bytes ${start}-${end}/${total}`,
          "Accept-Ranges": "bytes",
          "Content-Length": end - start + 1,
          "Content-Type": "audio/wav",
          "Cache-Control": "private, max-age=300",
        });
        fs.createReadStream(filePath, { start, end }).pipe(res);
        return;
      }
      res.writeHead(200, {
        "Content-Length": total,
        "Accept-Ranges": "bytes",
        "Content-Type": "audio/wav",
        "Cache-Control": "private, max-age=300",
      });
      fs.createReadStream(filePath).pipe(res);
      return;
    }

    // --- 1-Click Teach AI (Viewer Redacted Read; Admin Write -> J.A.R.V.I.S QA Cache) ---
    if (urlPath === "/api/teach") {
      const session = verifySessionFromCookieHeader(req.headers.cookie);
      if (req.method === "GET") {
        try {
          const r = await fetch(`${sip.jarvisUrl}/teach`, { signal: AbortSignal.timeout(4000) });
          const data = await r.json();
          if (!session?.isAdmin && Array.isArray(data?.entries)) {
            const curState = sip.publicState();
            const ctx = { publicTrunkNumber: curState.trunk?.user || "", ...collectSecretTerms(curState) };
            data.entries = data.entries.map((entry) => ({
              ...entry,
              question: sanitizeFreeTextForViewer(entry.question, ctx),
              answer: sanitizeFreeTextForViewer(entry.answer, ctx),
            }));
          }
          return sendJson(res, r.status, data);
        } catch {
          return sendJson(res, 200, { ok: false, entries: [] });
        }
      }
      if (!session?.isAdmin) {
        return sendJson(res, 403, { ok: false, error: "ต้องเข้าสู่ระบบผู้ดูแลก่อนสอนหรือลบคำตอบ AI" });
      }
      if (req.method === "POST") {
        try {
          const body = await readJsonBody(req);
          const r = await fetch(`${sip.jarvisUrl}/teach`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              question: String(body.question || "").trim(),
              answer: String(body.answer || "").trim(),
              lang: String(body.lang || "th").trim() || "th",
            }),
            signal: AbortSignal.timeout(5000),
          });
          const data = await r.json();
          return sendJson(res, r.status, data);
        } catch (err) {
          return sendJson(res, 502, { ok: false, error: err.message });
        }
      }
      if (req.method === "DELETE") {
        try {
          const body = await readJsonBody(req);
          const r = await fetch(`${sip.jarvisUrl}/teach`, {
            method: "DELETE",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ key: String(body.key || "").trim() }),
            signal: AbortSignal.timeout(5000),
          });
          const data = await r.json();
          return sendJson(res, r.status, data);
        } catch (err) {
          return sendJson(res, 502, { ok: false, error: err.message });
        }
      }
    }

    // --- Analytics & Cache ROI Endpoint (Safe Aggregate Data, PII Scrubbed for Viewers) ---
    if (urlPath === "/api/analytics" && req.method === "GET") {
      const session = verifySessionFromCookieHeader(req.headers.cookie);
      const isAdmin = Boolean(session?.isAdmin);
      const allLogs = sip.memory.listCallLogs(300);
      const crmState = sip.crm.publicState();
      const now = new Date();
      const todayKey = now.toISOString().slice(0, 10);

      // Build 7-day trend buckets
      const dailyMap = new Map();
      for (let i = 6; i >= 0; i--) {
        const d = new Date(now.getTime() - i * 86400_000);
        const key = d.toISOString().slice(0, 10);
        dailyMap.set(key, { date: key, calls: 0, answered: 0, highIntent: 0 });
      }

      let todayCalls = 0;
      let totalDurSec = 0;
      let durCount = 0;
      let csatSum = 0;
      let csatN = 0;
      let aiQSum = 0;
      let aiQN = 0;
      let resolvedCount = 0;
      let analyzedCount = 0;
      let highIntentCount = 0;
      let recordedCount = 0;
      const sentiments = { positive: 0, neutral: 0, negative: 0, urgent: 0 };
      const topicCounts = new Map();
      const moduleCounts = new Map();

      for (const log of allLogs) {
        const dayKey = String(log.startedAt || "").slice(0, 10);
        if (dayKey === todayKey) todayCalls++;
        if (log.hasRecording) recordedCount++;
        if (log.durationSec > 0) {
          totalDurSec += log.durationSec;
          durCount++;
        }
        const bucket = dailyMap.get(dayKey);
        if (bucket) {
          bucket.calls++;
          if (log.status === "answered" || log.durationSec > 0) bucket.answered++;
        }
        const a = log.analysis;
        if (a && !a.error) {
          analyzedCount++;
          if (typeof a.csatScore === "number") { csatSum += a.csatScore; csatN++; }
          if (typeof a.aiQualityScore === "number") { aiQSum += a.aiQualityScore; aiQN++; }
          if (a.resolved) resolvedCount++;
          if (a.buyingIntent === "high") {
            highIntentCount++;
            if (bucket) bucket.highIntent++;
          }
          if (a.sentiment && sentiments[a.sentiment] !== undefined) {
            sentiments[a.sentiment]++;
          }
          if (a.topic) {
            const t = String(a.topic).trim().slice(0, 48);
            if (t) topicCounts.set(t, (topicCounts.get(t) || 0) + 1);
          }
          if (Array.isArray(a.interestedModules)) {
            for (const mod of a.interestedModules) {
              const m = String(mod || "").trim().slice(0, 40);
              if (m) moduleCounts.set(m, (moduleCounts.get(m) || 0) + 1);
            }
          }
        }
      }

      let cacheStats = null;
      let taughtCount = 0;
      try {
        const cr = await fetch(`${sip.jarvisUrl}/cache/stats`, { signal: AbortSignal.timeout(3000) });
        if (cr.ok) {
          cacheStats = await cr.json();
          taughtCount = Number(cacheStats?.qa?.taught_entries || 0);
        }
      } catch {}

      const curState = isAdmin ? null : sip.publicState();
      const sanitizeCtx = curState ? { publicTrunkNumber: curState.trunk?.user || "", ...collectSecretTerms(curState) } : null;

      const topTopics = Array.from(topicCounts.entries())
        .sort((a, b) => b[1] - a[1])
        .slice(0, 8)
        .map(([topic, count]) => ({
          topic: sanitizeCtx ? sanitizeFreeTextForViewer(topic, sanitizeCtx) : topic,
          count,
        }));

      const topModules = Array.from(moduleCounts.entries())
        .sort((a, b) => b[1] - a[1])
        .slice(0, 8)
        .map(([name, count]) => ({
          name: sanitizeCtx ? sanitizeFreeTextForViewer(name, sanitizeCtx) : name,
          count,
        }));

      return sendJson(res, 200, {
        ok: true,
        generatedAt: now.toISOString(),
        summary: {
          totalCalls: allLogs.length,
          todayCalls,
          avgDurationSec: durCount ? Math.round(totalDurSec / durCount) : 0,
          recordedCalls: recordedCount,
          analyzedCalls: analyzedCount,
          avgCsat: csatN ? Math.round((csatSum / csatN) * 10) / 10 : null,
          avgAiQuality: aiQN ? Math.round((aiQSum / aiQN) * 10) / 10 : null,
          resolvedRatePct: analyzedCount ? Math.round((resolvedCount / analyzedCount) * 100) : null,
          highIntentLeads: highIntentCount,
          totalCustomers: crmState.stats.totalCustomers,
          openTickets: crmState.stats.openTickets,
          taughtFaqs: taughtCount,
        },
        dailyTrend: Array.from(dailyMap.values()),
        sentiments,
        topTopics,
        topModules,
        cache: cacheStats,
      });
    }

    return handle(req, res);
  });

  const wss = new WebSocketServer({ noServer: true });
  const nextUpgradeHandler = app.getUpgradeHandler();

  server.on("upgrade", (req, socket, head) => {
    const urlPath = (req.url || "").split("?")[0];
    if (urlPath === "/ws/sip") {
      wss.handleUpgrade(req, socket, head, (ws) => {
        wss.emit("connection", ws, req);
      });
    } else {
      nextUpgradeHandler(req, socket, head);
    }
  });

  function broadcastState(state) {
    const adminPayload = JSON.stringify({ type: "state", data: state, readOnly: false });
    const viewerPayload = JSON.stringify({
      type: "state",
      data: sanitizeStateForViewer(state),
      readOnly: true,
    });
    for (const client of wss.clients) {
      if (client.readyState === 1) {
        client.send(client.isAdmin ? adminPayload : viewerPayload);
      }
    }
  }

  sip.on("state", broadcastState);

  sip.on("callback_ticket", (ticket) => {
    const curState = sip.publicState();
    const publicTrunk = curState.trunk?.user || "";
    const secretCtx = collectSecretTerms(curState);
    const adminPayload = JSON.stringify({ type: "callback_ticket", ticket });
    const viewerPayload = JSON.stringify({
      type: "callback_ticket",
      ticket: sanitizeTicketForViewer(ticket, publicTrunk, null, secretCtx),
    });
    for (const client of wss.clients) {
      if (client.readyState === 1) {
        client.send(client.isAdmin ? adminPayload : viewerPayload);
      }
    }
  });

  sip.on("meter", (levels) => {
    if (wss.clients.size === 0) return;
    const payload = JSON.stringify({ type: "meter", levels, ts: Date.now() });
    for (const client of wss.clients) {
      if (client.readyState === 1) {
        client.send(payload);
      }
    }
  });

  // WebSocket keep-alive heartbeat (prevents Caddy / Reverse SSH Tunnel silent idle disconnects)
  const wsHeartbeatTimer = setInterval(() => {
    const hb = JSON.stringify({ type: "heartbeat", serverTime: Date.now() });
    for (const client of wss.clients) {
      if (client.isAlive === false) {
        try { client.terminate(); } catch {}
        continue;
      }
      client.isAlive = false;
      try {
        client.ping();
        if (client.readyState === 1) client.send(hb);
      } catch {}
    }
  }, 15000);
  wsHeartbeatTimer.unref?.();

  sip.on("rtp", ({ lineId, payload }) => {
    const line = sip.lines[lineId - 1];
    if (!line || line.aiActive || sip.activeAudioLine !== lineId) return;
    const frame = Buffer.allocUnsafe(1 + payload.length);
    frame[0] = lineId & 0xff;
    payload.copy(frame, 1);
    for (const client of wss.clients) {
      // Only authenticated admin can listen to live audio stream on non-AI human calls
      if (client.readyState === 1 && client.isAdmin) {
        client.send(frame, { binary: true });
      }
    }
  });

  wss.on("connection", (ws, req) => {
    const session = verifySessionFromCookieHeader(req.headers.cookie);
    ws.isAdmin = Boolean(session?.isAdmin);
    ws.userEmail = session?.email || null;
    ws.userName = session?.name || session?.email || "ผู้ดูแลระบบ";
    ws.isAlive = true;
    ws.on("pong", () => {
      ws.isAlive = true;
    });

    const current = sip.publicState();
    ws.send(
      JSON.stringify({
        type: "state",
        data: ws.isAdmin ? current : sanitizeStateForViewer(current),
        readOnly: !ws.isAdmin,
        serverTime: Date.now(),
      })
    );

    ws.on("message", async (data, isBinary) => {
      ws.isAlive = true;
      // Allow lightweight ping/sync from all connected viewers for true realtime monitoring
      if (!isBinary) {
        try {
          const probe = JSON.parse(data.toString("utf8"));
          if (probe?.type === "ping") {
            ws.send(JSON.stringify({ type: "pong", ts: probe.ts || Date.now(), serverTime: Date.now() }));
            return;
          }
          if (probe?.type === "sync") {
            const snap = sip.publicState();
            ws.send(
              JSON.stringify({
                type: "state",
                data: ws.isAdmin ? snap : sanitizeStateForViewer(snap),
                readOnly: !ws.isAdmin,
                serverTime: Date.now(),
              })
            );
            return;
          }
        } catch {}
      }

      // Strict server-side read-only enforcement for non-admin viewers
      if (!ws.isAdmin) {
        if (!isBinary) {
          ws.send(
            JSON.stringify({
              type: "error",
              message: "โหมดผู้ชม (ปกปิดความลับ) กรุณาเข้าสู่ระบบผู้ดูแลด้วย Google ก่อนดำเนินการ",
            })
          );
        }
        return;
      }

      if (isBinary) {
        const buf = Buffer.isBuffer(data) ? data : Buffer.from(data);
        if (buf.length < 2) return;
        const lineId = buf[0];
        const pcmu = buf.subarray(1);
        sip.sendRtpAudio(lineId, pcmu);
        return;
      }

      try {
        const msg = JSON.parse(data.toString("utf8"));
        switch (msg.type) {
          case "answer":
            await sip.answer(Number(msg.lineId), { enableWebAudio: true });
            break;
          case "reject":
            sip.reject(Number(msg.lineId));
            break;
          case "hangup":
            sip.hangup(Number(msg.lineId));
            break;
          case "dial": {
            const res = await sip.dial(msg.number, msg.lineId ? Number(msg.lineId) : null);
            if (!res.ok) {
              ws.send(JSON.stringify({ type: "error", message: res.error }));
            }
            break;
          }
          case "mute":
            sip.toggleMute(Number(msg.lineId));
            break;
          case "hold":
            sip.toggleHold(Number(msg.lineId));
            break;
          case "dtmf":
            sip.sendDtmf(Number(msg.lineId), String(msg.digit || ""));
            break;
          case "setActiveAudioLine":
            sip.setActiveAudioLine(Number(msg.lineId));
            break;
          case "setAutoAnswerAi":
            sip.setAutoAnswerAi(Boolean(msg.enabled));
            break;
          case "takeOverFromAi":
            await sip.takeOverFromAi(Number(msg.lineId));
            break;
          case "attachAi":
            await sip.attachAiToLine(Number(msg.lineId));
            break;
          case "reanalyze":
            await sip.runPostCallAnalysis(String(msg.phone || ""), String(msg.sessionId || ""));
            break;
          case "crmUpsertCustomer":
            sip.crmUpsertCustomer(msg.customer || {});
            break;
          case "crmCreateTicket":
            sip.crmCreateTicket(msg.ticket || {});
            break;
          case "crmUpdateTicket":
            sip.crmUpdateTicket(String(msg.id || ""), msg.patch || {}, ws.userName);
            break;
          case "crmAcknowledgeTicket":
            sip.crmAcknowledgeTicket(String(msg.id || ""), ws.userName);
            break;
          case "crmCloseTicket":
            sip.crmCloseTicket(String(msg.id || ""), ws.userName, String(msg.note || ""));
            break;
          case "crmDeleteTicket":
            sip.crmDeleteTicket(String(msg.id || ""));
            break;
          case "setLineLanguage":
            await sip.setLineLanguage(Number(msg.lineId), msg.language ? String(msg.language) : null);
            break;
          case "reregister":
            sip.sendRegister();
            break;
          default:
            break;
        }
      } catch (err) {
        ws.send(JSON.stringify({ type: "error", message: err.message }));
      }
    });
  });

  server.listen(port, hostname, () => {
    console.log(`> BC Call Center ready on http://localhost:${port}`);
  });
});
