import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import next from "next";
import { WebSocketServer } from "ws";
import {
  buildClearSessionCookie,
  buildSessionSetCookie,
  createSessionToken,
  sanitizeStateForViewer,
  verifyGoogleIdToken,
  verifySessionFromCookieHeader,
} from "./lib/auth.mjs";
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

app.prepare().then(async () => {
  await sip.start();

  const server = http.createServer(async (req, res) => {
    const urlPath = (req.url || "").split("?")[0];

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
              : "เข้าสู่ระบบแล้ว แต่บัญชีนี้ไม่มีสิทธิ์ผู้ดูแล (โหมดผู้ชมดูได้อย่างเดียว)",
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

  sip.on("rtp", ({ lineId, payload }) => {
    if (sip.activeAudioLine !== lineId) return;
    const frame = Buffer.allocUnsafe(1 + payload.length);
    frame[0] = lineId & 0xff;
    payload.copy(frame, 1);
    for (const client of wss.clients) {
      // Only authenticated admin can listen to live audio stream
      if (client.readyState === 1 && client.isAdmin) {
        client.send(frame, { binary: true });
      }
    }
  });

  wss.on("connection", (ws, req) => {
    const session = verifySessionFromCookieHeader(req.headers.cookie);
    ws.isAdmin = Boolean(session?.isAdmin);
    ws.userEmail = session?.email || null;

    const current = sip.publicState();
    ws.send(
      JSON.stringify({
        type: "state",
        data: ws.isAdmin ? current : sanitizeStateForViewer(current),
        readOnly: !ws.isAdmin,
      })
    );

    ws.on("message", async (data, isBinary) => {
      // Strict server-side read-only enforcement for non-admin viewers
      if (!ws.isAdmin) {
        if (!isBinary) {
          ws.send(
            JSON.stringify({
              type: "error",
              message: "โหมดผู้ชม (ดูได้อย่างเดียว) กรุณาเข้าสู่ระบบผู้ดูแลด้วย Google ก่อนดำเนินการ",
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
            await sip.answer(Number(msg.lineId));
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
            sip.crmUpdateTicket(String(msg.id || ""), msg.patch || {});
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
