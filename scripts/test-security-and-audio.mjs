#!/usr/bin/env node
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

const origCwd = process.cwd();
const origAdminEmail = process.env.ADMIN_EMAIL;
const origAdminEmails = process.env.ADMIN_EMAILS;
const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "bc-cc-sec-"));

try {
  process.chdir(tmpDir);
  const {
    getAdminEmails,
    isAdminEmail,
    sanitizeFreeTextForViewer,
    sanitizeStateForViewer,
    sanitizeTicketForViewer,
  } = await import(pathToFileURL(path.join(origCwd, "lib", "auth.mjs")).href);
  const { SipEngine } = await import(pathToFileURL(path.join(origCwd, "lib", "sip-engine.mjs")).href);
  const { decodeMuLawSample, encodeMuLawSample } = await import(pathToFileURL(path.join(origCwd, "lib", "g711.mjs")).href);

  // 1. Admin allowlist check (single + comma-separated ADMIN_EMAIL / ADMIN_EMAILS, case-insensitive)
  process.env.ADMIN_EMAILS = "";
  process.env.ADMIN_EMAIL = "admin@example.test";
  assert.equal(isAdminEmail("admin@example.test"), true);
  assert.equal(isAdminEmail("ADMIN@EXAMPLE.TEST"), true);
  assert.equal(isAdminEmail("other@example.test"), false);
  assert.equal(isAdminEmail(""), false);
  process.env.ADMIN_EMAIL = "";
  assert.equal(isAdminEmail("admin@example.test"), false);

  process.env.ADMIN_EMAIL = "admin1@example.test, admin2@example.test";
  process.env.ADMIN_EMAILS = "staff1@example.test;staff2@example.test\n";
  const adminSet = getAdminEmails();
  assert.equal(adminSet.size, 4);
  for (const okEmail of ["admin1@example.test", "ADMIN2@EXAMPLE.TEST", "staff1@example.test", "Staff2@example.test"]) {
    assert.equal(isAdminEmail(okEmail), true, `expected admin allowlist match for ${okEmail}`);
  }
  assert.equal(isAdminEmail("viewer@example.test"), false);

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

  // 5. Source-level invariants on server.mjs, page.tsx, LineCard.tsx & new SipEngine control guards
  let sentRegisterCount = 0;
  engine.sendRegister = () => {
    sentRegisterCount += 1;
  };
  engine.sock = { mock: true };
  engine.resolvedIp = "127.0.0.1";
  engine.trunk.status = "error";
  engine.trunk.error = "timeout";
  await engine.reregister();
  assert.equal(sentRegisterCount, 1, "reregister must send REGISTER when socket is initialized");
  assert.equal(engine.trunk.status, "connecting");
  assert.equal(engine.trunk.error, null);

  const reregStartEngine = new SipEngine({ host: "127.0.0.1", port: 5060, user: "020000099", password: "x" });
  let startCalled = false;
  reregStartEngine.started = true;
  reregStartEngine.start = async () => {
    startCalled = true;
  };
  await reregStartEngine.reregister();
  assert.equal(startCalled, true, "reregister must restart engine when socket/DNS failed initially");

  const noMsgQa = await engine.runPostCallAnalysis("020000001", "missing-sess", 0);
  assert.equal(noMsgQa.ok, false, "runPostCallAnalysis must return ok:false when session has no messages");
  engine.memory.startSession("020000008", "inbound");
  const sessWithMsg = engine.memory.listCallLogs(1)[0];
  engine.memory.appendMessage("020000008", sessWithMsg.sessionId, "caller", "สอบถามราคา");
  const noKeyQa = await engine.runPostCallAnalysis("020000008", sessWithMsg.sessionId, 5);
  assert.equal(noKeyQa.ok, false, "runPostCallAnalysis must return ok:false when OLLAMA_API_KEY is missing");

  line2.state = "held";
  line2.held = true;
  line2.aiActive = false;
  line2._dialog = { vias: [], fromHdr: "", toHdr: "", callId: "c2", cseqHdr: "", routes: [] };
  const heldAttach = await engine.attachAiToLine(2);
  assert.equal(heldAttach.ok, false, "attachAiToLine must reject held lines with guidance");
  assert.ok(heldAttach.error.includes("คุยต่อ"));

  line2.aiActive = true;
  line2.aiPartial = "กำลังพูด";
  line2.aiStatus = "กำลังค้นหา";
  line2.aiTranscript = "J.A.R.V.I.S: สวัสดี";
  await engine.takeOverFromAi(2);
  assert.equal(line2.aiActive, false);
  assert.equal(line2.held, false, "takeOverFromAi must unhold line");
  assert.equal(line2.state, "connected");
  assert.equal(line2.aiPartial, "");
  assert.equal(line2.aiStatus, "");
  assert.equal(line2.aiTranscript, "");
  assert.equal(engine.activeAudioLine, 2);

  const serverSrc = fs.readFileSync(path.join(origCwd, "server.mjs"), "utf8");
  assert.ok(serverSrc.includes("/api/internal/idle") && serverSrc.includes("isDirectLoopback"), "server.mjs must restrict /api/internal/idle to loopback");
  assert.ok(serverSrc.includes("sanitizeStateForViewer"), "server.mjs must sanitize state for viewer mode");
  assert.ok(serverSrc.includes("if (!line || line.aiActive || sip.activeAudioLine !== lineId) return;"), "server.mjs must mute live RTP on AI calls");
  assert.ok(
    serverSrc.includes("broadcastToOtherAdmins") && serverSrc.includes("client.isAdmin"),
    "server.mjs must restrict admin_action broadcasts to authenticated admins only"
  );
  assert.ok(serverSrc.includes("broadcastDataSync") && serverSrc.includes("await sip.reregister()"), "server.mjs must handle data_sync and async reregister");

  const pageSrc = fs.readFileSync(path.join(origCwd, "src", "app", "page.tsx"), "utf8");
  assert.ok(!pageSrc.includes("startRingtone"), "page.tsx must not ring on browser when callers dial in");
  assert.ok(pageSrc.includes("activeAudioLineRef.current === lineId"), "page.tsx must play live RTP only for selected human line");
  assert.ok(pageSrc.includes("toggleAudio") && pageSrc.includes("disableAudio"), "page.tsx must allow turning audio/mic off cleanly");

  const lineCardSrc = fs.readFileSync(path.join(origCwd, "src", "components", "LineCard.tsx"), "utf8");
  assert.ok(lineCardSrc.includes("หยุดฟัง") && lineCardSrc.includes("onSelectAudio(isActiveAudio ? null : line.lineId)"), "LineCard must support toggling listen off");

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
  assert.ok(bridgeSrc.includes("async disableAudio()") && bridgeSrc.includes("if (!this.isReady || !this.ctx"), "audio-bridge.ts must cleanly stop mic and output on disableAudio()");

  // 7. Embedded LocalBrainStore viewer isolation, path-traversal block & 4-digit menu search
  const { LocalBrainStore } = await import(pathToFileURL(path.join(origCwd, "lib", "brain-store.mjs")).href);
  const brainStore = new LocalBrainStore({
    vaultDir: path.join(origCwd, "bcbrain", "vault"),
    wishlistPath: path.join(origCwd, "docs", "brain-wishlist.md"),
  });
  const viewerOv = brainStore.getOverview({ isAdmin: false, audience: "dev", limit: 400 });
  assert.equal(viewerOv.audience, "user", "viewer must be pinned to user audience even if requesting dev");
  assert.ok(viewerOv.stats.totalNotes >= 240 && viewerOv.stats.devNotes === 0, `viewer must only see user notes: ${JSON.stringify(viewerOv.stats)}`);
  assert.ok(viewerOv.notes.every((n) => n.audience === "user"), "viewer overview must never leak dev notes");

  const adminOv = brainStore.getOverview({ isAdmin: true, audience: "all", limit: 400 });
  assert.ok(adminOv.stats.totalNotes >= 350 && adminOv.stats.devNotes >= 100, `admin must see full embedded vault: ${JSON.stringify(adminOv.stats)}`);

  const viewerDevNote = brainStore.getNote("api-overview", { isAdmin: false });
  assert.equal(viewerDevNote.ok, false);
  assert.equal(viewerDevNote.status, 403, "viewer must get 403 for dev note");
  const adminDevNote = brainStore.getNote("api-overview", { isAdmin: true });
  assert.equal(adminDevNote.ok, true);
  assert.equal(adminDevNote.status, 200);

  const traversal = brainStore.getNote("../../.env.local", { isAdmin: true });
  assert.equal(traversal.ok, false);
  assert.equal(traversal.status, 400, "path traversal slug must be rejected with 400");

  const viewerSalesNote = brainStore.getNote("sales-overview", { isAdmin: false });
  assert.equal(viewerSalesNote.ok, true);
  assert.equal(/\b0[689]\d-\d{3}-\d{4}\b/.test(viewerSalesNote.note.body), false, "viewer note body must mask mobile digits");

  const menuRes = brainStore.searchMenus({ q: "4013" });
  assert.ok(menuRes.total >= 700 && menuRes.menus.some((m) => m.code === "4013"), "menu search must find 4013");

  // Hybrid overview keeps strict viewer isolation, normalizes Thai promo phrases, & strips media
  const hybridOv = await brainStore.getOverviewHybrid({ isAdmin: false, audience: "dev", q: "ซื้อสองแถมหนึ่ง", limit: 30 });
  assert.equal(hybridOv.audience, "user", "hybrid overview must pin viewer to user audience");
  assert.ok(hybridOv.notes.every((n) => n.audience === "user"), "hybrid overview must never leak dev notes");
  assert.ok(hybridOv.notes.every((n) => n.mediaCount === 0), "voice-first brain must strip image/YouTube media");
  assert.ok(
    hybridOv.notes.slice(0, 3).some((n) => n.slug === "howto-pos-hardware-display-promo" || n.slug === "howto-price-center-discount-formula"),
    `buy-get query should surface promo notes in top 3: ${hybridOv.notes.slice(0, 3).map((n) => n.slug).join(",")}`
  );
  await brainStore.pgVector?.close?.();

  // 8. Admin Brain Editor, Wishlist auto-archive, Secret Guard & Brain Miss Log (isolated in tmpDir)
  const tmpVault = path.join(tmpDir, "vault");
  const tmpWl = path.join(tmpDir, "wishlist.md");
  const tmpMiss = path.join(tmpDir, "brain-miss-log.jsonl");
  fs.mkdirSync(path.join(tmpVault, "faq"), { recursive: true });
  fs.writeFileSync(
    tmpWl,
    "# Wishlist\n\n## 1. Pending\n\n- [ ] **ทดสอบเติมเข้าสมอง** (slug แนะนำ: `faq-test-fill`)\n  - คำถาม: ทดสอบ\n\n## 3. Archive\n\n",
    "utf8"
  );
  const fakePhone = ["081", "234", "5678"].join("-");
  fs.writeFileSync(
    tmpMiss,
    JSON.stringify({ ts: "2026-11-25T01:00:00Z", query: `ทดสอบคำถาม ${fakePhone}`, ctxTopic: "", reason: "abstain", topCosine: 0.41 }) + "\n",
    "utf8"
  );
  const isolatedBrain = new LocalBrainStore({ vaultDir: tmpVault, wishlistPath: tmpWl, missLogPath: tmpMiss });
  const badFolder = isolatedBrain.saveUserNote({ slug: "bad-dev", folder: "schema", title: "Dev", body: "SELECT * FROM x", syncPgvector: false });
  assert.equal(badFolder.ok, false, "must block writing to dev folders");
  const badSecret = isolatedBrain.saveUserNote({ slug: "bad-sec", folder: "faq", title: "Key", body: "token " + "sk" + "-" + "a".repeat(26), syncPgvector: false });
  assert.equal(badSecret.ok, false, "must block saving API keys into vault");
  const saved = isolatedBrain.saveUserNote({
    slug: "faq-test-fill",
    folder: "faq",
    title: "ทดสอบเติมเข้าสมอง",
    aliases: ["ทดสอบคำถาม"],
    body: "คำตอบทดสอบยืนยันครบถ้วน พร้อม https://youtu.be/abc12345",
    resolveWishlistSlug: "faq-test-fill",
    syncPgvector: false,
  });
  assert.equal(saved.ok, true);
  assert.equal(saved.wishlistResolved, true, "must archive matching wishlist entry");
  const readBack = isolatedBrain.getNote("faq-test-fill", { isAdmin: false });
  assert.equal(readBack.ok, true);
  assert.equal(readBack.note.body.includes("youtu.be"), false, "saved note must strip YouTube URLs");

  const missViewer = isolatedBrain.getMissReport({ isAdmin: false });
  assert.equal(missViewer.uniqueCount, 1);
  assert.equal(missViewer.items[0].query.includes(fakePhone), false, "viewer miss log must mask mobile numbers");

  const promoRes = isolatedBrain.promoteTaughtEntry({ question: "คำถามสอนถาวร", answer: "คำตอบยืนยันสำหรับลูกค้า", syncPgvector: false });
  assert.equal(promoRes.ok, true, "promoteTaughtEntry must create permanent FAQ note");
  assert.ok(isolatedBrain.getNote(promoRes.slug, { isAdmin: false }).ok);
  await isolatedBrain.pgVector?.close?.();

  console.log("ALL_SECURITY_AND_AUDIO_TESTS_PASSED");
} finally {
  if (origAdminEmail === undefined) delete process.env.ADMIN_EMAIL;
  else process.env.ADMIN_EMAIL = origAdminEmail;
  if (origAdminEmails === undefined) delete process.env.ADMIN_EMAILS;
  else process.env.ADMIN_EMAILS = origAdminEmails;
  process.chdir(origCwd);
  fs.rmSync(tmpDir, { recursive: true, force: true });
}
