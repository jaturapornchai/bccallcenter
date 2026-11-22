import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import next from "next";
import { WebSocketServer } from "ws";
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

app.prepare().then(async () => {
  await sip.start();

  const server = http.createServer((req, res) => {
    handle(req, res);
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
    const payload = JSON.stringify({ type: "state", data: state });
    for (const client of wss.clients) {
      if (client.readyState === 1) {
        client.send(payload);
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
      if (client.readyState === 1) {
        client.send(frame, { binary: true });
      }
    }
  });

  wss.on("connection", (ws) => {
    ws.send(JSON.stringify({ type: "state", data: sip.publicState() }));

    ws.on("message", async (data, isBinary) => {
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
    console.log(`> Yalecom 5-Line Console ready on http://localhost:${port}`);
  });
});
