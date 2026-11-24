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
  const { isAdminEmail } = await import(pathToFileURL(path.join(origCwd, "lib", "auth.mjs")).href);
  const { SipEngine } = await import(pathToFileURL(path.join(origCwd, "lib", "sip-engine.mjs")).href);

  // 1. Strict Single-Admin check (case-insensitive exact match against ADMIN_EMAIL)
  process.env.ADMIN_EMAIL = "admin@example.test";
  assert.equal(isAdminEmail("admin@example.test"), true);
  assert.equal(isAdminEmail("ADMIN@EXAMPLE.TEST"), true);
  assert.equal(isAdminEmail("other@example.test"), false);
  assert.equal(isAdminEmail(""), false);
  process.env.ADMIN_EMAIL = "";
  assert.equal(isAdminEmail("admin@example.test"), false);

  // 2. Real-time phone audio suppression in SipEngine
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

  // Answering via AI / default must NOT auto-route live caller audio to browser
  await engine.answer(1);
  assert.equal(line1.state, "connected");
  assert.equal(engine.activeAudioLine, null, "default inbound answer must keep activeAudioLine null");

  // Switching activeAudioLine to a non-AI line works; switching to an AI line is refused
  engine.setActiveAudioLine(1);
  assert.equal(engine.activeAudioLine, 1);
  engine.setActiveAudioLine(null);
  assert.equal(engine.activeAudioLine, null);
  line1.aiActive = true;
  engine.setActiveAudioLine(1);
  assert.equal(engine.activeAudioLine, null, "setActiveAudioLine must refuse AI-handled lines");

  // Hanging up an active audio line must NOT auto-jump web audio to another caller
  const line2 = engine.lines[1];
  line2.state = "connected";
  line2.remoteNumber = "020000002";
  engine.activeAudioLine = 1;
  engine.resetLine(line1);
  assert.equal(engine.activeAudioLine, null, "resetLine must clear activeAudioLine instead of switching lines");

  // Explicit human answer enables web audio only when requested
  line2.state = "ringing_in";
  line2._dialog = { vias: [], fromHdr: "", toHdr: "", callId: "c2", cseqHdr: "", routes: [] };
  await engine.answer(2, { enableWebAudio: true });
  assert.equal(engine.activeAudioLine, 2);

  // 3. Source-level invariants on server.mjs & page.tsx
  const serverSrc = fs.readFileSync(path.join(origCwd, "server.mjs"), "utf8");
  assert.ok(serverSrc.includes("/api/internal/idle") && serverSrc.includes("isDirectLoopback"), "server.mjs must restrict /api/internal/idle to loopback");
  assert.ok(serverSrc.includes("HTTP/1.1 401 Unauthorized"), "server.mjs must reject non-admin WS upgrade with 401");
  assert.ok(serverSrc.includes("if (!line || line.aiActive || sip.activeAudioLine !== lineId) return;"), "server.mjs must mute live RTP on AI calls");

  const pageSrc = fs.readFileSync(path.join(origCwd, "src", "app", "page.tsx"), "utf8");
  assert.ok(!pageSrc.includes("startRingtone"), "page.tsx must not ring on browser when callers dial in");
  assert.ok(pageSrc.includes("if (!auth.isAdmin)"), "page.tsx must render Admin Lock screen before showing any call center UI");

  console.log("ALL_SECURITY_AND_AUDIO_TESTS_PASSED");
} finally {
  if (origAdminEmail === undefined) delete process.env.ADMIN_EMAIL;
  else process.env.ADMIN_EMAIL = origAdminEmail;
  process.chdir(origCwd);
  fs.rmSync(tmpDir, { recursive: true, force: true });
}
