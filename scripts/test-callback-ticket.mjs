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
  const { CrmStore, isPlausibleCustomerName } = await import(pathToFileURL(path.join(origCwd, "lib", "crm-store.mjs")).href);
  const { extractCallerNameFromText } = await import(pathToFileURL(path.join(origCwd, "lib", "sip-engine.mjs")).href);
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
  assert.equal(r1.updated, false);
  const t1 = r1.ticket;
  assert.equal(t1.source, "ai_callback");
  assert.equal(t1.priority, "urgent");
  assert.equal(t1.status, "open");
  assert.equal(t1.callbackPhone, "020000009");
  assert.equal(t1.callerPhone, "020000001");
  assert.equal(t1.lineId, 2);
  assert.equal(t1.mergedCount, 1);
  assert.ok(t1.subject.includes("ปัญหาสต็อกติดลบ"), "subject must carry topic");
  assert.ok(String(t1.callbackAdvice || "").includes("•"), "callback advice must be generated");

  // 2. Dedup same session + same message
  const rDup = store.createCallbackTicket({
    phone: "020000009",
    callbackPhone: "020000009",
    callerPhone: "020000001",
    customerName: "สมชาย",
    message: "ปัญหาสต็อกติดลบ",
    sessionId: "sess-cb-1",
    lineId: 2,
  });
  assert.equal(rDup.created, false, "duplicate callback request in same session must dedup");
  assert.equal(rDup.updated, false);
  assert.equal(rDup.ticket.id, t1.id);

  // 3. Acknowledge first, then repeat calls from the SAME phone (020000001) must consolidate into t1 and re-open it
  const acked = store.acknowledgeTicket(t1.id, "แอดมินเอ");
  assert.equal(acked.status, "in_progress");
  assert.equal(acked.acknowledgedBy, "แอดมินเอ");

  const r2SamePhone = store.createCallbackTicket({
    phone: "020000001",
    customerName: "สมชาย",
    message: "ขอใบเสนอราคา",
    sessionId: "sess-cb-2",
    lineId: 1,
  });
  assert.equal(r2SamePhone.created, false, "same phone follow-up must consolidate into existing open ticket");
  assert.equal(r2SamePhone.updated, true, "consolidated update must trigger notification");
  assert.equal(r2SamePhone.ticket.id, t1.id);
  assert.equal(r2SamePhone.ticket.mergedCount, 2);
  assert.equal(r2SamePhone.ticket.status, "open", "new callback request must re-open ticket for staff alert");
  assert.equal(r2SamePhone.ticket.acknowledgedAt, null);

  const r3SamePhone = store.createCallbackTicket({
    phone: "020000001",
    customerName: "สมชาย",
    message: "ปัญหาโปรแกรมคำนวณต้นทุนสินค้าผิด",
    sessionId: "sess-cb-3",
    lineId: 3,
  });
  assert.equal(r3SamePhone.created, false);
  assert.equal(r3SamePhone.updated, true);
  assert.equal(r3SamePhone.ticket.id, t1.id);
  assert.equal(r3SamePhone.ticket.mergedCount, 3);
  assert.equal(r3SamePhone.ticket.topics.length, 3);
  assert.ok(r3SamePhone.ticket.description.includes("ปัญหาโปรแกรมคำนวณต้นทุนสินค้าผิด"));
  assert.ok(r3SamePhone.ticket.callbackAdvice.includes("ติดต่อเข้ามาซ้ำรวม 3 ครั้ง"));
  assert.ok(r3SamePhone.ticket.callbackAdvice.includes("AnyDesk"));

  // 4. Different phone creates a distinct ticket + ID uniqueness after deletion
  const rDiff1 = store.createCallbackTicket({
    phone: "020000002",
    customerName: "สมหญิง",
    message: "สอบถามแพ็กเกจ Pro",
    sessionId: "sess-diff-1",
    lineId: 1,
  });
  const rDiff2 = store.createCallbackTicket({
    phone: "020000003",
    customerName: "มานะ",
    message: "สอบถามการปิดงบ",
    sessionId: "sess-diff-2",
    lineId: 1,
  });
  assert.equal(rDiff1.created, true);
  assert.equal(rDiff2.created, true);
  assert.notEqual(rDiff1.ticket.id, t1.id);
  assert.notEqual(rDiff2.ticket.id, rDiff1.ticket.id);

  store.deleteTicket(rDiff1.ticket.id);
  const rDiff3 = store.createCallbackTicket({
    phone: "020000004",
    customerName: "มานี",
    message: "สอบถามภาษีมูลค่าเพิ่ม",
    sessionId: "sess-diff-3",
    lineId: 1,
  });
  assert.notEqual(rDiff3.ticket.id, rDiff1.ticket.id, "sequence must not reuse deleted id");
  assert.notEqual(rDiff3.ticket.id, rDiff2.ticket.id, "sequence must not collide with rDiff2");

  // 5. Stats check before closing t1
  let pub = store.publicState();
  assert.equal(pub.stats.pendingCallbacks, 3);
  assert.equal(pub.stats.unacknowledgedCallbacks, 3);

  // 6. Close ticket (staff acknowledged & closed), then next call from 020000001 creates a NEW ticket
  const closed = store.closeTicket(t1.id, "แอดมินเอ", "โทรกลับลูกค้าเรียบร้อยแล้ว");
  assert.equal(closed.status, "closed");
  assert.equal(closed.closedBy, "แอดมินเอ");
  assert.ok(closed.closedAt);
  assert.ok(closed.description.includes("โทรกลับลูกค้าเรียบร้อยแล้ว"));

  pub = store.publicState();
  assert.equal(pub.stats.pendingCallbacks, 2);
  assert.equal(pub.stats.unacknowledgedCallbacks, 2);

  const rAfterClose = store.createCallbackTicket({
    phone: "020000001",
    customerName: "สมชาย",
    message: "ปัญหาพิมพ์บิลไม่ออก",
    sessionId: "sess-cb-4",
    lineId: 1,
  });
  assert.equal(rAfterClose.created, true, "after closing t1, new call from same phone opens a new ticket");
  assert.notEqual(rAfterClose.ticket.id, t1.id);

  // 7. Post-call QA on open phone merges into open ticket without creating a duplicate + closed session also skips
  const qaOpen = store.applyPostCallAnalysis("020000001", "sess-cb-5", {
    csatScore: 4,
    needsHumanFollowUp: true,
    resolved: false,
    topic: "ขอเช็กฟอร์มใบเสร็จ",
    summary: "ลูกค้าติดปัญหาฟอร์มใบเสร็จ",
    followUpAction: "ส่งไฟล์ฟอร์มมาตรฐานให้ลูกค้า",
    callbackAdvice: "• เตรียมไฟล์ฟอร์มใบเสร็จมาตรฐานและทดสอบพิมพ์ผ่านรีโมต",
  });
  assert.equal(qaOpen.ticket, null, "post-call QA must merge into existing open ticket for same phone");
  assert.ok(rAfterClose.ticket.topics.includes("ขอเช็กฟอร์มใบเสร็จ"));
  assert.ok(rAfterClose.ticket.callbackAdvice.includes("เตรียมไฟล์ฟอร์มใบเสร็จมาตรฐาน"));

  const qaClosed = store.applyPostCallAnalysis("020000001", "sess-cb-1", {
    csatScore: 6,
    needsHumanFollowUp: true,
    resolved: false,
    topic: "ปัญหาสต็อกติดลบ",
  });
  assert.equal(qaClosed.ticket, null, "post-call QA must not recreate ticket for already-closed sessionId");

  // 8. Load consolidation: pre-existing open duplicate tickets collapse into 1 open ticket per phone on CrmStore.load()
  const dupFixtureDir = fs.mkdtempSync(path.join(os.tmpdir(), "bc-cc-consolidate-"));
  try {
    fs.mkdirSync(path.join(dupFixtureDir, "data"), { recursive: true });
    fs.writeFileSync(
      path.join(dupFixtureDir, "data", "crm-db.json"),
      JSON.stringify({
        customers: {},
        tickets: [
          {
            id: "TCK-261124-080",
            phone: "020000005",
            callbackPhone: "020000005",
            callerPhone: "020000005",
            customerName: "วิชัย",
            company: "บริษัททดสอบ",
            subject: "ขอให้โทรกลับ: แจ้งปัญหาต้นทุนผิด",
            description: "เรื่องที่ให้โทรกลับ: แจ้งปัญหาต้นทุนผิด\nเบอร์ให้โทรกลับ: 020000005",
            status: "open",
            priority: "urgent",
            assignee: "ทีมโทรกลับลูกค้า",
            source: "ai_callback",
            sessionId: "s-3",
            createdAt: "2026-11-24T00:11:00.000Z",
            updatedAt: "2026-11-24T00:11:00.000Z",
          },
          {
            id: "TCK-261124-079",
            phone: "020000005",
            callbackPhone: "020000005",
            callerPhone: "020000005",
            customerName: "วิชัย",
            company: "บริษัททดสอบ",
            subject: "ขอให้โทรกลับ: ต้นทุนและตัวเลขคำนวณเพี้ยน",
            description: "เรื่องที่ให้โทรกลับ: ต้นทุนและตัวเลขคำนวณเพี้ยน\nเบอร์ให้โทรกลับ: 020000005",
            status: "open",
            priority: "urgent",
            assignee: "ทีมโทรกลับลูกค้า",
            source: "ai_callback",
            sessionId: "s-2",
            createdAt: "2026-11-24T00:10:00.000Z",
            updatedAt: "2026-11-24T00:10:00.000Z",
          },
          {
            id: "TCK-261124-078",
            phone: "020000005",
            callbackPhone: "020000005",
            callerPhone: "020000005",
            customerName: "วิชัย",
            company: "บริษัททดสอบ",
            subject: "ขอให้โทรกลับ: สอบถามรหัสสินค้าและการชำระเงิน",
            description: "เรื่องที่ให้โทรกลับ: สอบถามรหัสสินค้าและการชำระเงิน\nเบอร์ให้โทรกลับ: 020000005",
            status: "open",
            priority: "urgent",
            assignee: "ทีมโทรกลับลูกค้า",
            source: "ai_callback",
            sessionId: "s-1",
            createdAt: "2026-11-24T00:00:00.000Z",
            updatedAt: "2026-11-24T00:00:00.000Z",
          },
        ],
      })
    );
    const consolidatedStore = new CrmStore(path.join(dupFixtureDir, "data"));
    assert.equal(consolidatedStore.tickets.length, 1, "3 open duplicates for same phone must merge into 1");
    assert.equal(consolidatedStore.tickets[0].mergedCount, 3);
    assert.equal(consolidatedStore.tickets[0].topics.length, 3);
    assert.ok(consolidatedStore.tickets[0].callbackAdvice.includes("ติดต่อเข้ามาซ้ำรวม 3 ครั้ง"));
  } finally {
    process.chdir(tmpDir);
    fs.rmSync(dupFixtureDir, { recursive: true, force: true });
  }

  // 9. Viewer redaction hides phone numbers, callbackPhone, callerPhone, names, actor names across topics & callbackAdvice
  const maskedTicket = sanitizeTicketForViewer(r3SamePhone.ticket, "020000099");
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
  for (const secret of ["020000001", "020000002", "020000003", "020000004", "020000009", "สมชาย", "สมหญิง", "มานะ", "มานี", "บีซี", "แอดมินเอ"]) {
    assert.equal(maskedStateJson.includes(secret), false, `viewer state leaked ${secret}`);
  }

  // 10. Regression (call 1795527470144-wok98): Thai words containing "ชื่อ" ("เชื่อม...", "เชื่อว่า...", "ขายเชื่อไหม")
  // must never overwrite or capture garbled caller names in Node sip-engine or CRM.
  for (const badUtterance of [
    "ให้เซลฟเวอร์อยู่คนละที่อะ คนลักสาขาเชื่อมกันได้หรือเปล่า",
    "แล้วสาขากับคลางสินค้าเชื่อมโยงกันยังไง",
    "สินค้าที่ขายเชื่อไหม",
    "ผมเชื่อว่าโปรแกรมนี้ใช้งานได้ดี",
    "ชื่อเมนูอะไรครับ",
    "ชื่อสาขาต้องกรอกยังไง",
  ]) {
    assert.equal(extractCallerNameFromText(badUtterance, ""), null, `must not capture name from: ${badUtterance}`);
  }
  assert.equal(isPlausibleCustomerName("มโยงกันยังไง", { explicit: true }), false);
  assert.equal(isPlausibleCustomerName("มกันได้หรือเปล่า", { explicit: true }), false);
  assert.equal(isPlausibleCustomerName("น้องไหม", { explicit: true }), true);

  const selfIntro = extractCallerNameFromText("ผมชื่อสมชายครับ", "");
  assert.deepEqual(selfIntro, { name: "สมชาย", explicit: true }, "self-intro must allow updating caller name");
  const bareNamed = extractCallerNameFromText("เรียกว่าก้องครับ", "");
  assert.deepEqual(bareNamed, { name: "ก้อง", explicit: false }, "bare เรียกว่า must capture only when no existing name");

  // Post-call QA must heal a previously corrupted CRM name while rejecting garbled QA names
  store.upsertCustomer({ phone: "020000008", name: "มโยงกันยังไง" });
  store.applyPostCallAnalysis("020000008", "sess-heal-1", { callerName: "มกันได้หรือเปล่า", csatScore: 6 });
  assert.equal(store.getCustomer("020000008").name, "มโยงกันยังไง", "garbled QA name must not replace anything");
  store.applyPostCallAnalysis("020000008", "sess-heal-2", { callerName: "สมชาย", csatScore: 8 });
  assert.equal(store.getCustomer("020000008").name, "สมชาย", "plausible QA name must heal corrupted CRM name");

  console.log("ALL_CALLBACK_TICKET_TESTS_PASSED");
} finally {
  process.chdir(origCwd);
  fs.rmSync(tmpDir, { recursive: true, force: true });
}
