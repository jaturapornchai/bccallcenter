#!/usr/bin/env node
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

const origCwd = process.cwd();
const origAdminEmail = process.env.ADMIN_EMAIL;
const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "bc-cc-sec-"));

try {
  process.chdir(tmpDir);
  const {
    isAdminEmail,
    sanitizeFreeTextForViewer,
    sanitizeStateForViewer,
    sanitizeTicketForViewer,
  } = await import(pathToFileURL(path.join(origCwd, "lib", "auth.mjs")).href);
  const { SipEngine } = await import(pathToFileURL(path.join(origCwd, "lib", "sip-engine.mjs")).href);
  const { decodeMuLawSample, encodeMuLawSample } = await import(pathToFileURL(path.join(origCwd, "lib", "g711.mjs")).href);

  // 1. Strict Single-Admin check (case-insensitive exact match against ADMIN_EMAIL)
  process.env.ADMIN_EMAIL = "admin@example.test";
  assert.equal(isAdminEmail("admin@example.test"), true);
  assert.equal(isAdminEmail("ADMIN@EXAMPLE.TEST"), true);
  assert.equal(isAdminEmail("other@example.test"), false);
  assert.equal(isAdminEmail(""), false);
  process.env.ADMIN_EMAIL = "";
  assert.equal(isAdminEmail("admin@example.test"), false);

  // 2. Free-text PII redaction (redacts nicknames, emails, IPs, domestic & international phones while keeping Thai accounting words intact)
  const sanitized = sanitizeFreeTextForViewer(
    "ติดต่อคุณแบงค์ คุณพิมพ์ และคุณป้อม เรื่องคุณภาพ คุณสมบัติ ขอบคุณครับ คุณลูกค้า การพิมพ์บิล โทร 020000001 หรือ +697102000001 หรือ secret@example.test IP 10.0.0.5 วันที่ 2026-11-24 โทรเข้าเบอร์ศูนย์ 020000099",
    {
      publicTrunkNumber: "020000099",
      secretNames: ["แบงค์", "พิมพ์", "ป้อม"],
      secretCompanies: ["ร้านยางรถยนต์"],
      secretActors: ["แอดมินเอ"],
    }
  );
  for (const forbidden of ["คุณแบงค์", "คุณพิมพ์", "คุณป้อม", "020000001", "+6971", "secret@example.test", "10.0.0.5"]) {
    assert.equal(sanitized.includes(forbidden), false, `sanitized prose leaked ${forbidden}: ${sanitized}`);
  }
  for (const preserved of ["คุณภาพ", "คุณสมบัติ", "ขอบคุณครับ", "คุณลูกค้า", "การพิมพ์บิล", "2026-11-24", "020000099"]) {
    assert.ok(sanitized.includes(preserved), `sanitized prose mangled common Thai/date/trunk word ${preserved}: ${sanitized}`);
  }

  // 3. Full state & ticket sanitization for public Viewer mode
  const fullState = {
    trunk: { user: "020000099", host: "sip.internal.example", ip: "198.51.100.2", port: 5060, status: "registered" },
    jarvisUrl: "http://127.0.0.1:7861",
    lines: [
      {
        lineId: 1,
        state: "connected",
        remoteNumber: "020000001",
        remoteName: "สมชาย",
        crmName: "สมชาย",
        crmCompany: "ร้านยางรถยนต์",
        aiTranscript: "ผู้โทร: ผมชื่อสมชาย จากร้านยางรถยนต์",
        aiPartial: "",
        aiStatus: "",
        liveMessages: [{ at: "2026-11-24T00:00:00Z", role: "caller", text: "ติดต่อคุณป้อม 020000009" }],
      },
    ],
    history: [{ id: "h1", at: "2026-11-24T00:00:00Z", direction: "inbound", number: "020000001", status: "answered", durationSec: 30 }],
    callers: [
      {
        phone: "020000001",
        totalCalls: 1,
        sessions: [
          {
            id: "s1",
            messages: [{ at: "2026-11-24T00:00:00Z", role: "caller", text: "โทรกลับ 020000009 หาคุณแบงค์" }],
            analysis: {
              callerName: "สมชาย",
              callerCompany: "ร้านยางรถยนต์",
              topic: "ปัญหาบิลคุณแบงค์",
              summary: "คุณแบงค์โทรสอบถามคุณภาพสินค้าและขอให้คุณพิมพ์ติดต่อกลับที่ 020000009",
            },
          },
        ],
      },
    ],
    callLogs: [],
    crm: {
      customers: [
        {
          phone: "020000001",
          name: "สมชาย",
          company: "ร้านยางรถยนต์",
          tags: ["สมชาย"],
          notes: "โน้ตลับลูกค้า secret@example.test 020000001",
        },
      ],
      tickets: [
        {
          id: "TCK-261124-001",
          phone: "020000009",
          callbackPhone: "020000009",
          callerPhone: "020000001",
          customerName: "สมชาย",
          company: "ร้านยางรถยนต์",
          subject: "ขอให้โทรกลับ: คุณสมชาย 020000009",
          description: "ชื่อลูกค้า: สมชาย\nบริษัท: ร้านยางรถยนต์\nเบอร์ให้โทรกลับ: 020000009",
          acknowledgedBy: "แอดมินเอ",
          closedBy: "แอดมินเอ",
        },
      ],
      stats: { totalCustomers: 1, openTickets: 1 },
    },
  };
  const redacted = sanitizeStateForViewer(fullState);
  const redactedJson = JSON.stringify(redacted);
  for (const leaked of [
    "020000001",
    "020000009",
    "สมชาย",
    "ร้านยางรถยนต์",
    "คุณแบงค์",
    "คุณพิมพ์",
    "คุณป้อม",
    "แอดมินเอ",
    "sip.internal.example",
    "198.51.100.2",
    "secret@example.test",
    "โน้ตลับลูกค้า",
  ]) {
    assert.equal(redactedJson.includes(leaked), false, `viewer state leaked ${leaked}`);
  }
  assert.ok(redactedJson.includes("คุณภาพ"), "viewer state must keep non-PII words intact");
  assert.equal(redacted.trunk.host, "SIP Trunk (ปกปิด)");
  assert.equal(redacted.trunk.ip, null);

  const singleTicket = sanitizeTicketForViewer(fullState.crm.tickets[0], "020000099");
  assert.equal(JSON.stringify(singleTicket).includes("020000009"), false);
  assert.equal(JSON.stringify(singleTicket).includes("สมชาย"), false);

  // 4. Real-time phone audio suppression in SipEngine
  const engine = new SipEngine({
    host: "127.0.0.1",
    port: 5060,
    user: "020000099",
    password: "x",
    maxLines: 5,
    autoAnswerAi: true,
  });
  engine.openRtpSocket = async () => 10000;
  engine.sendSipReply = () => {};
  engine.cleanupRtp = () => {};

  const line1 = engine.lines[0];
  line1.state = "ringing_in";
  line1.remoteNumber = "020000001";
  line1._dialog = { vias: [], fromHdr: "", toHdr: "", callId: "c1", cseqHdr: "", routes: [] };

  await engine.answer(1);
  assert.equal(line1.state, "connected");
  assert.equal(engine.activeAudioLine, null, "default inbound answer must keep activeAudioLine null");

  engine.setActiveAudioLine(1);
  assert.equal(engine.activeAudioLine, 1);
  engine.setActiveAudioLine(null);
  assert.equal(engine.activeAudioLine, null);
  line1.aiActive = true;
  engine.setActiveAudioLine(1);
  assert.equal(engine.activeAudioLine, null, "setActiveAudioLine must refuse AI-handled lines");

  const line2 = engine.lines[1];
  line2.state = "connected";
  line2.remoteNumber = "020000002";
  engine.activeAudioLine = 1;
  engine.resetLine(line1);
  assert.equal(engine.activeAudioLine, null, "resetLine must clear activeAudioLine instead of switching lines");

  line2.state = "ringing_in";
  line2._dialog = { vias: [], fromHdr: "", toHdr: "", callId: "c2", cseqHdr: "", routes: [] };
  await engine.answer(2, { enableWebAudio: true });
  assert.equal(engine.activeAudioLine, 2);

  // 5. Source-level invariants on server.mjs & page.tsx
  const serverSrc = fs.readFileSync(path.join(origCwd, "server.mjs"), "utf8");
  assert.ok(serverSrc.includes("/api/internal/idle") && serverSrc.includes("isDirectLoopback"), "server.mjs must restrict /api/internal/idle to loopback");
  assert.ok(serverSrc.includes("sanitizeStateForViewer"), "server.mjs must sanitize state for viewer mode");
  assert.ok(serverSrc.includes("if (!line || line.aiActive || sip.activeAudioLine !== lineId) return;"), "server.mjs must mute live RTP on AI calls");

  const pageSrc = fs.readFileSync(path.join(origCwd, "src", "app", "page.tsx"), "utf8");
  assert.ok(!pageSrc.includes("startRingtone"), "page.tsx must not ring on browser when callers dial in");
  assert.ok(pageSrc.includes("activeAudioLineRef.current === lineId"), "page.tsx must play live RTP only for selected human line");

  // 6. ITU-T G.711 mu-law (0x84 bias, 32635 max) monotonicity & sine-wave SNR check
  assert.equal(decodeMuLawSample(0xff), 0, "mu-law 0xff must decode to 0");
  let prevPos = -1;
  for (let code = 0xff; code >= 0x80; code--) {
    const val = decodeMuLawSample(code);
    assert.ok(val >= prevPos, `non-monotonic G.711 decode at 0x${code.toString(16)}: ${val} < ${prevPos}`);
    assert.equal(decodeMuLawSample(code ^ 0x80), val === 0 ? 0 : -val, `asymmetric G.711 decode at 0x${code.toString(16)}`);
    prevPos = val;
  }
  assert.equal(prevPos, 32124, "ITU-T G.711 peak decode must be 32124 (16-bit), never folded 13-bit");

  let sigPower = 0;
  let errPower = 0;
  for (let i = 0; i < 400; i++) {
    const x = Math.round(16000 * Math.sin((2 * Math.PI * 1000 * i) / 8000));
    const y = decodeMuLawSample(encodeMuLawSample(x));
    sigPower += x * x;
    errPower += (x - y) * (x - y);
  }
  const snrDb = 10 * Math.log10(sigPower / Math.max(errPower, 1e-9));
  assert.ok(snrDb > 35, `ITU-T G.711 1kHz sine round-trip SNR too low: ${snrDb.toFixed(2)} dB`);

  const bridgeSrc = fs.readFileSync(path.join(origCwd, "src", "lib", "audio-bridge.ts"), "utf8");
  assert.ok(bridgeSrc.includes("const MULAW_MAX = 32635") && bridgeSrc.includes("const MULAW_BIAS = 0x84"), "audio-bridge.ts must use ITU-T G.711 16-bit table");

  console.log("ALL_SECURITY_AND_AUDIO_TESTS_PASSED");
} finally {
  if (origAdminEmail === undefined) delete process.env.ADMIN_EMAIL;
  else process.env.ADMIN_EMAIL = origAdminEmail;
  process.chdir(origCwd);
  fs.rmSync(tmpDir, { recursive: true, force: true });
}
