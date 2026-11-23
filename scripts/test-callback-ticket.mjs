#!/usr/bin/env node
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

const origCwd = process.cwd();
const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "bc-cc-test-"));

try {
  process.chdir(tmpDir);
  const { CrmStore } = await import(pathToFileURL(path.join(origCwd, "lib", "crm-store.mjs")).href);
  const { sanitizeStateForViewer, sanitizeTicketForViewer } = await import(
    pathToFileURL(path.join(origCwd, "lib", "auth.mjs")).href
  );

  const store = new CrmStore();

  // 1. Create urgent callback ticket (different callbackPhone vs callerPhone)
  const r1 = store.createCallbackTicket({
    phone: "020000009",
    callbackPhone: "020000009",
    callerPhone: "020000001",
    customerName: "สมชาย",
    company: "บีซี",
    message: "ปัญหาสต็อกติดลบ",
    sessionId: "sess-cb-1",
    lineId: 2,
  });
  assert.equal(r1.created, true, "first callback ticket must be created");
  const t1 = r1.ticket;
  assert.equal(t1.source, "ai_callback");
  assert.equal(t1.priority, "urgent");
  assert.equal(t1.status, "open");
  assert.equal(t1.callbackPhone, "020000009");
  assert.equal(t1.callerPhone, "020000001");
  assert.equal(t1.lineId, 2);
  assert.ok(t1.subject.includes("ปัญหาสต็อกติดลบ"), "subject must carry topic");

  // 2. Dedup same session + same message within 60s
  const rDup = store.createCallbackTicket({
    phone: "020000009",
    callbackPhone: "020000009",
    callerPhone: "020000001",
    customerName: "สมชาย",
    message: "ปัญหาสต็อกติดลบ",
    sessionId: "sess-cb-1",
    lineId: 2,
  });
  assert.equal(rDup.created, false, "duplicate callback request must dedup");
  assert.equal(rDup.ticket.id, t1.id);

  // 3. New topic / session creates distinct ticket + ID uniqueness after delete
  const r2 = store.createCallbackTicket({
    phone: "020000001",
    customerName: "สมหญิง",
    message: "ขอใบเสนอราคา",
    sessionId: "sess-cb-2",
    lineId: 1,
  });
  const r3 = store.createCallbackTicket({
    phone: "020000001",
    customerName: "สมหญิง",
    message: "สอบถามแพ็กเกจ Pro",
    sessionId: "sess-cb-3",
    lineId: 1,
  });
  assert.equal(r2.created, true);
  assert.equal(r3.created, true);
  assert.notEqual(r2.ticket.id, t1.id);
  assert.notEqual(r3.ticket.id, r2.ticket.id);

  store.deleteTicket(r2.ticket.id);
  const r4 = store.createCallbackTicket({
    phone: "020000001",
    customerName: "สมหญิง",
    message: "ปัญหาพิมพ์บิลไม่ออก",
    sessionId: "sess-cb-4",
    lineId: 1,
  });
  assert.notEqual(r4.ticket.id, r3.ticket.id, "sequence must not collide with r3 after deletion");
  assert.notEqual(r4.ticket.id, r2.ticket.id, "sequence must not reuse deleted ticket id");

  // 4. Stats check before ack/close
  let pub = store.publicState();
  assert.equal(pub.stats.pendingCallbacks, 3);
  assert.equal(pub.stats.unacknowledgedCallbacks, 3);

  // 5. Acknowledge ticket
  const acked = store.acknowledgeTicket(t1.id, "แอดมินเอ");
  assert.equal(acked.status, "in_progress");
  assert.equal(acked.acknowledgedBy, "แอดมินเอ");
  assert.ok(acked.acknowledgedAt);

  pub = store.publicState();
  assert.equal(pub.stats.pendingCallbacks, 3);
  assert.equal(pub.stats.unacknowledgedCallbacks, 2);

  // 6. Close ticket (staff acknowledged & closed)
  const closed = store.closeTicket(t1.id, "แอดมินเอ", "โทรกลับลูกค้าเรียบร้อยแล้ว");
  assert.equal(closed.status, "closed");
  assert.equal(closed.closedBy, "แอดมินเอ");
  assert.ok(closed.closedAt);
  assert.ok(closed.description.includes("โทรกลับลูกค้าเรียบร้อยแล้ว"));

  pub = store.publicState();
  assert.equal(pub.stats.pendingCallbacks, 2);
  assert.equal(pub.stats.unacknowledgedCallbacks, 2);

  // 7. Post-call QA on same sessionId must NOT create duplicate ticket
  const qaRes = store.applyPostCallAnalysis("020000001", "sess-cb-1", {
    csatScore: 6,
    needsHumanFollowUp: true,
    resolved: false,
    topic: "ปัญหาสต็อกติดลบ",
  });
  assert.equal(qaRes.ticket, null, "post-call QA must not duplicate existing callback ticket");
  assert.equal(store.tickets.filter((t) => t.sessionId === "sess-cb-1").length, 1);

  // 8. Viewer redaction hides phone numbers, callbackPhone, callerPhone, names, and actor names
  const maskedTicket = sanitizeTicketForViewer(closed, "020000099");
  const maskedTicketJson = JSON.stringify(maskedTicket);
  for (const secret of ["020000001", "020000009", "สมชาย", "บีซี", "แอดมินเอ"]) {
    assert.equal(maskedTicketJson.includes(secret), false, `viewer ticket leaked ${secret}`);
  }

  const maskedState = sanitizeStateForViewer({
    trunk: { user: "020000099", host: "internal", ip: "127.0.0.1", port: 5060, status: "registered", lastRegisterAt: null, expires: 120, error: null },
    autoAnswerAi: true,
    jarvisUrl: "http://127.0.0.1:7861",
    activeAudioLine: null,
    lines: [],
    history: [],
    callers: [],
    callLogs: [],
    crm: store.publicState(),
  });
  const maskedStateJson = JSON.stringify(maskedState);
  for (const secret of ["020000001", "020000009", "สมชาย", "สมหญิง", "บีซี", "แอดมินเอ"]) {
    assert.equal(maskedStateJson.includes(secret), false, `viewer state leaked ${secret}`);
  }

  console.log("ALL_CALLBACK_TICKET_TESTS_PASSED");
} finally {
  process.chdir(origCwd);
  fs.rmSync(tmpDir, { recursive: true, force: true });
}
