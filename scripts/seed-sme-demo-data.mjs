#!/usr/bin/env node
/**
 * Permanently wipes local call/CRM/recording data and seeds a realistic, rich
 * Thai & international SME demo dataset across 48 customers, 6 languages, 165 calls, and 32 tickets.
 * Synthetic non-real demo phones only (020010101..020010148).
 */
import fs from "node:fs";
import path from "node:path";
import { buildCallbackAdvice, cleanTopicText } from "../lib/crm-store.mjs";

const ROOT = process.cwd();
const DATA_DIR = path.join(ROOT, "data");
const RECORDINGS_DIR = path.join(DATA_DIR, "recordings");
const CRM_PATH = path.join(DATA_DIR, "crm-db.json");
const MEM_PATH = path.join(DATA_DIR, "call-memory.json");
const PROFILES_PATH = path.join(ROOT, "jarvis", "sessions", "caller_profiles.json");
const QA_CACHE_PATH = path.join(ROOT, "jarvis", "cache", "qa_cache.json");

function ensureDir(dir) {
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
}

function wipeExistingData() {
  for (const fp of [CRM_PATH, MEM_PATH, PROFILES_PATH]) {
    if (fs.existsSync(fp)) fs.unlinkSync(fp);
  }
  if (fs.existsSync(RECORDINGS_DIR)) {
    for (const name of fs.readdirSync(RECORDINGS_DIR)) {
      const target = path.join(RECORDINGS_DIR, name);
      if (fs.statSync(target).isFile()) fs.unlinkSync(target);
    }
  }
}

const DEMO_SMES = [
  { name: "สมเกียรติ", company: "ร้านไทยเจริญยางยนต์ (5 สาขา)", lang: "th", tier: "vip", stage: "customer", topic: "คำนวณต้นทุน FIFO & แพ็กเกจ BC AutoCare", mod: "BC AutoCare" },
  { name: "สมศักดิ์", company: "หจก. ศรีนครการช่างก่อสร้าง", lang: "th", tier: "vip", stage: "prospect", topic: "ระบบคุมงวดงาน เงินประกันผลงาน 6018", mod: "คุมงวดงาน/เงินประกัน 6018" },
  { name: "พิมพ์ลดา", company: "คลินิกทันตกรรมสไมล์แคร์", lang: "th", tier: "regular", stage: "customer", topic: "ระบบนัดหมายและสรุปรายได้รายแพทย์", mod: "รายงานรายได้ & คอมมิชชั่น" },
  { name: "ธนกร", company: "บริษัท มหานครวัสดุก่อสร้าง จำกัด", lang: "th", tier: "vip", stage: "customer", topic: "สต็อกหลายคลังและการออกใบกำกับภาษี e-Tax", mod: "e-Tax Invoice & VAT" },
  { name: "วิไลพร", company: "ร้านโกลเด้นเบเกอรี่ เฮาส์ (8 สาขา)", lang: "th", tier: "regular", stage: "prospect", topic: "ระบบผลิต BOM วัตถุดิบและ Lot วันหมดอายุ FEFO", mod: "สต็อกหลายสาขา & FEFO" },
  { name: "ประเสริฐ", company: "อู่ประเสริฐเซอร์วิส แอนด์ ไทร์", lang: "th", tier: "vip", stage: "customer", topic: "รับรถเปิดใบซ่อมอัตโนมัติและประวัติทะเบียนรถ", mod: "BC AutoCare" },
  { name: "กัญญา", company: "ร้านฟาร์มาพลัส เภสัชกรรม (ขายส่ง)", lang: "th", tier: "regular", stage: "customer", topic: "เตือนสินค้าใกล้หมดอายุและควบคุมราคาส่ง 5 ระดับ", mod: "สต็อกหลายสาขา & FEFO" },
  { name: "ณัฐวุฒิ", company: "บริษัท อีสานอิเล็กทริค ซัพพลาย จำกัด", lang: "th", tier: "vip", stage: "customer", topic: "รับชำระหลายบิลในใบเสร็จเดียวและเช็คคืน", mod: "ลูกหนี้ & วางบิล" },
  { name: "สุภาวดี", company: "คลินิกความงามออร่า เฮลท์", lang: "th", tier: "regular", stage: "prospect", topic: "ระบบตัดคอร์สและคิดค่าคอมมิชชั่นพนักงานขาย", mod: "รายงานรายได้ & คอมมิชชั่น" },
  { name: "ชัชวาล", company: "ร้านแสงทองฮาร์ดแวร์ ค้าส่ง", lang: "th", tier: "regular", stage: "customer", topic: "ระบบนับสต็อกด้วยบาร์โค้ดมือถือและจัดโปรโมชั่น", mod: "POS ค้าปลีก-ส่ง" },
  { name: "นฤมล", company: "ร้านบ้านสวนซูเปอร์มาร์เก็ต", lang: "th", tier: "regular", stage: "customer", topic: "POS หน้าแคชเชียร์ความเร็วสูงรองรับ 12 จุดขาย", mod: "POS ค้าปลีก-ส่ง" },
  { name: "ธีรเดช", company: "บริษัท บูรพาโลหะกิจ จำกัด", lang: "th", tier: "vip", stage: "prospect", topic: "บัญชีต้นทุนอุตสาหกรรมและปิดงบการเงินรายเดือน", mod: "บัญชีต้นทุน & งบการเงิน" },
  { name: "อรัญญา", company: "ร้านกาแฟและโรงคั่วอรุณสวัสดิ์", lang: "th", tier: "new", stage: "lead", topic: "แพ็กเกจเริ่มต้น Starter และโอนยอดขายเข้าระบบบัญชี", mod: "แพ็กเกจ BC Ai Account" },
  { name: "เกรียงไกร", company: "บริษัท รวมช่างแอร์เซอร์วิส จำกัด", lang: "th", tier: "regular", stage: "customer", topic: "ติดตามงานบริการหลังการขายและสัญญาบำรุงรักษา", mod: "งานบริการ & สัญญา" },
  { name: "ปิยะนุช", company: "หจก. เจริญพรค้าข้าวและเกษตรภัณฑ์", lang: "th", tier: "regular", stage: "customer", topic: "ขายเงินเชื่อและออกใบแจ้งหนี้วางบิลอัตโนมัติ", mod: "ลูกหนี้ & วางบิล" },
  { name: "อนุชา", company: "ร้านเอสพี ออโต้พาร์ท ค้าส่ง", lang: "th", tier: "vip", stage: "customer", topic: "รหัสอะไหล่เทียบรุ่นและเช็กสต็อกข้ามสาขาเรียลไทม์", mod: "BC AutoCare" },
  { name: "จิตรา", company: "โรงพิมพ์ดิจิทัล เอ็กซ์เพรส", lang: "th", tier: "regular", stage: "prospect", topic: "คำนวณต้นทุนงานสั่งผลิตตามจ๊อบและใบเสนอราคา", mod: "คุมงวดงาน/เงินประกัน 6018" },
  { name: "พงศธร", company: "บริษัท ไทยเฟอร์นิเจอร์ เดคอร์ จำกัด", lang: "th", tier: "regular", stage: "customer", topic: "รับจองสินค้ามัดจำและแจ้งเตือนขายเกินยอดคงเหลือ", mod: "POS ค้าปลีก-ส่ง" },
  { name: "ศิริลักษณ์", company: "ร้านอาหารทะเลริมชล (4 สาขา)", lang: "th", tier: "new", stage: "lead", topic: "กระทบยอด Statement ธนาคารและการรับโอน QR", mod: "กระทบยอดธนาคารอัตโนมัติ" },
  { name: "วรพจน์", company: "บริษัท ตะวันตกขนส่งและคลังสินค้า จำกัด", lang: "th", tier: "regular", stage: "customer", topic: "ภาษีหัก ณ ที่จ่าย ภ.ง.ด.3/53 และวางบิลรายเดือน", mod: "e-Tax Invoice & VAT" },
  { name: "กนกวรรณ", company: "ร้านมินิมาร์ท ชุมชนพลัส", lang: "th", tier: "new", stage: "lead", topic: "เปรียบเทียบรุ่น Standard กับ Pro สำหรับ 3 สาขา", mod: "แพ็กเกจ BC Ai Account" },
  { name: "ทวีศักดิ์", company: "อู่เจริญยนต์ 2026 เซอร์วิส", lang: "th", tier: "regular", stage: "customer", topic: "ตั้งค่าประวัติเช็กระยะลูกค้าและการแจ้งเตือนล่วงหน้า", mod: "BC AutoCare" },
  { name: "มัลลิกา", company: "บริษัท สยามบรรจุภัณฑ์ จำกัด", lang: "th", tier: "regular", stage: "customer", topic: "ระบบเจ้าหนี้ สั่งซื้อ PO และรับสินค้าเกินใบสั่ง", mod: "จัดซื้อ & เจ้าหนี้" },
  { name: "สุรชัย", company: "หจก. พัฒนาไฟฟ้าและประปา", lang: "th", tier: "regular", stage: "prospect", topic: "รับเหมาระบบไฟและคุมต้นทุนวัสดุรายโครงการ", mod: "คุมงวดงาน/เงินประกัน 6018" },
  { name: "เบญจมาศ", company: "ร้านเพชรพลอย จิวเวลรี่ เฮาส์", lang: "th", tier: "vip", stage: "customer", topic: "คุมสินค้ามี Serial Number รายชิ้นและบิลซื้อคืน", mod: "สต็อกหลายสาขา & FEFO" },
  { name: "เอกชัย", company: "บริษัท ไทยแลนด์ไอที โซลูชั่น จำกัด", lang: "th", tier: "regular", stage: "customer", topic: "คุมสต็อก Serial สินค้าไอทีและการรับประกันสินค้า", mod: "POS ค้าปลีก-ส่ง" },
  { name: "ชนาธิป", company: "ร้านปุ๋ยและเคมีเกษตร ภาคกลาง", lang: "th", tier: "regular", stage: "customer", topic: "คุมสินเชื่อเกษตรกรและแยกภาษี Vat/Non-Vat ในบิลเดียว", mod: "e-Tax Invoice & VAT" },
  { name: "ลัดดาวัลย์", company: "ศูนย์บริการความงาม ลัดดา สปา", lang: "th", tier: "new", stage: "lead", topic: "รายงานกำไรขาดทุนรายสาขาและค่าคอมมิชชั่นพนักงาน", mod: "รายงานรายได้ & คอมมิชชั่น" },
  { name: "สมบัติ", company: "บริษัท รุ่งเรืองสตีล ค้าเหล็ก จำกัด", lang: "th", tier: "vip", stage: "customer", topic: "ขายสินค้าหลายหน่วยนับ (เส้น/ตัน/มัด) และเช็กต้นทุนเรียลไทม์", mod: "บัญชีต้นทุน & งบการเงิน" },
  { name: "ดวงใจ", company: "ร้านเครื่องเขียนและอุปกรณ์สำนักงาน เอวัน", lang: "th", tier: "regular", stage: "customer", topic: "ออกใบกำกับภาษีเต็มรูปจากบิลขายหน้าร้าน 1 คลิก", mod: "POS ค้าปลีก-ส่ง" },
  { name: "ภาณุวัฒน์", company: "หจก. อุตสาหกรรมยางพาราไทย", lang: "th", tier: "at_risk", stage: "churn_risk", topic: "ตรวจสอบการปิดงวดบัญชีปลายปีและยอดยกมา", mod: "บัญชีต้นทุน & งบการเงิน" },
  { name: "รุ่งทิวา", company: "ร้านเวชสำอาง รุ่งอรุณ (ออนไลน์ + หน้าร้าน)", lang: "th", tier: "regular", stage: "prospect", topic: "รวมสต็อกหน้าร้านกับคำสั่งซื้อออนไลน์ในพอร์ทัลเดียว", mod: "สต็อกหลายสาขา & FEFO" },
  { name: "จักรพันธ์", company: "บริษัท นครชัยเครื่องเย็น จำกัด", lang: "th", tier: "regular", stage: "customer", topic: "เปิดบิลส่งของชั่วคราวก่อนเปลี่ยนเป็นใบกำกับภาษี", mod: "e-Tax Invoice & VAT" },
  { name: "พัชรา", company: "คลินิกกายภาพบำบัด แฮปปี้มูฟ", lang: "th", tier: "new", stage: "lead", topic: "สอนพนักงานใหม่บันทึกค่าใช้จ่ายและภาษีซื้อ", mod: "e-Tax Invoice & VAT" },
  // English speakers (4)
  { name: "David Miller", company: "Siam Global Parts Co., Ltd.", lang: "en", tier: "vip", stage: "prospect", topic: "Multi-warehouse inventory & bilingual TH/EN financial reports", mod: "สต็อกหลายสาขา & FEFO" },
  { name: "Sarah Jenkins", company: "Bangkok Gourmet Import Ltd.", lang: "en", tier: "regular", stage: "customer", topic: "FEFO expiry batch tracking & import cost allocation", mod: "สต็อกหลายสาขา & FEFO" },
  { name: "Michael Tan", company: "Eastern Solar Engineering Co.", lang: "en", tier: "regular", stage: "prospect", topic: "Project milestone billing & retention tracking", mod: "คุมงวดงาน/เงินประกัน 6018" },
  { name: "Rachel Brooks", company: "Andaman Wellness Group", lang: "en", tier: "new", stage: "lead", topic: "Multi-branch POS & daily bank reconciliation", mod: "กระทบยอดธนาคารอัตโนมัติ" },
  // Chinese speakers (4)
  { name: "คุณหลิน", company: "บริษัท เอเชีย ฟู้ดเทค จำกัด", lang: "zh", tier: "vip", stage: "prospect", topic: "ระบบ Lot/วันหมดอายุ FEFO โรงงานอาหาร 15 ผู้ใช้งาน", mod: "สต็อกหลายสาขา & FEFO" },
  { name: "คุณเฉิน", company: "บริษัท หัวซิน โลจิสติกส์ ไทย จำกัด", lang: "zh", tier: "regular", stage: "customer", topic: "รายงานกำไรรายเที่ยวและวางบิลลูกหนี้นำเข้า-ส่งออก", mod: "บัญชีต้นทุน & งบการเงิน" },
  { name: "คุณหวัง", company: "ห้างหุ้นส่วน ไทย-ไชน่า ฮาร์ดแวร์", lang: "zh", tier: "regular", stage: "customer", topic: "ระบบขายส่งหลายราคาและพิมพ์บาร์โค้ดสต็อก", mod: "POS ค้าปลีก-ส่ง" },
  { name: "คุณจาง", company: "บริษัท จางซิน แพ็คเกจจิ้ง จำกัด", lang: "zh", tier: "new", stage: "lead", topic: "แพ็กเกจ Pro สำหรับโรงงานบรรจุภัณฑ์", mod: "แพ็กเกจ BC Ai Account" },
  // Japanese speakers (2)
  { name: "คุณซาโต้", company: "บริษัท นิปปอน พรีซิชั่น พาร์ท (ไทยแลนด์)", lang: "ja", tier: "vip", stage: "customer", topic: "BOM การผลิตชิ้นส่วนและต้นทุนวัตถุดิบมาตรฐาน", mod: "บัญชีต้นทุน & งบการเงิน" },
  { name: "คุณทานากะ", company: "ร้านอาหารญี่ปุ่น ซากุระ คิทเช่น (3 สาขา)", lang: "ja", tier: "regular", stage: "prospect", topic: "สต็อกวัตถุดิบแยกสาขาและสรุปยอดขายรายวัน", mod: "POS ค้าปลีก-ส่ง" },
  // Korean speakers (2)
  { name: "คุณคิม", company: "บริษัท เค-บิวตี้ คอสเมติก ไทยแลนด์", lang: "ko", tier: "regular", stage: "prospect", topic: "คลังสินค้าค้าส่งเครื่องสำอางและตัวแทนจำหน่าย", mod: "สต็อกหลายสาขา & FEFO" },
  { name: "คุณปาร์ค", company: "บริษัท โซลเทค เมคคาทรอนิคส์ จำกัด", lang: "ko", tier: "regular", stage: "customer", topic: "จัดการ PO นำเข้าเครื่องจักรและภาษีมูลค่าเพิ่ม", mod: "จัดซื้อ & เจ้าหนี้" },
  // Lao speakers (2)
  { name: "คุณคำปัน", company: "บริษัท เวียงจันทน์ เทรดดิ้ง คอร์ป", lang: "lo", tier: "regular", stage: "customer", topic: "ขายส่งชายแดนไทย-ลาวและคุมยอดค้างชำระหลายสกุลเงิน", mod: "ลูกหนี้ & วางบิล" },
  { name: "คุณแสงเดือน", company: "ร้านทองและของฝาก นครพนม-คำม่วน", lang: "lo", tier: "new", stage: "lead", topic: "ระบบ POS ขายปลีก-ส่งและเช็กสต็อกข้ามสาขา", mod: "POS ค้าปลีก-ส่ง" },
];

const LANG_DIALOGUES = {
  th: (c) => [
    { role: "caller", text: `สวัสดีครับ จาก${c.company} อยากสอบถามเรื่อง${c.topic}ครับ` },
    { role: "ai", text: `สวัสดีครับ ผม J.A.R.V.I.S ผู้ช่วย AI ของระบบ BC Call Center ยินดีดูแลเลยครับ สำหรับ${c.company} สามารถใช้งานผ่านเมนูของ ${c.mod} ได้ครบถ้วนเลยครับ` },
    { role: "caller", text: `แล้วพนักงานหน้าร้านกับฝ่ายบัญชีใช้งานพร้อมกันหลายเครื่องได้ไหมครับ` },
    { role: "ai", text: `ได้ทันทีครับ รุ่น Standard และ Pro รองรับการใช้งานพร้อมกันหลายผู้ใช้งาน พร้อมเก็บสิทธิ์แยกตามแผนก และข้อมูลซิงก์อัปเดตเรียลไทม์ครับ` },
  ],
  en: (c) => [
    { role: "caller", text: `Hello, I'm calling from ${c.company}. Does your software handle ${c.topic}?` },
    { role: "ai", text: `Certainly! Switching to English for you. BC Ai Account supports ${c.mod} out of the box with multi-user real-time sync and Thai/English accounting reports.` },
    { role: "caller", text: `That really helps since our Thai accounting staff and foreign managers work together.` },
    { role: "ai", text: `I'll note your company requirements in CRM and have our bilingual specialist send a tailored demo schedule.` },
  ],
  zh: (c) => [
    { role: "caller", text: `你好，我是${c.company}的，想咨询一下${c.topic}。` },
    { role: "ai", text: `您好！为您切换为中文服务。我们的 BC Ai Account 原生支持 ${c.mod}，多仓库与多语言报表可同时给泰国会计团队使用。` },
    { role: "caller", text: `太好了，请安排销售早上回电话给我们团队。` },
    { role: "ai", text: `好的，我已经用中文为您记录需求并生成中文+泰文回电建议卡，明天一早团队会立即联系您。` },
  ],
  ja: (c) => [
    { role: "caller", text: `こんにちは、${c.company}です。${c.topic}について質問があります。` },
    { role: "ai", text: `お電話ありがとうございます。日本語でお答えします。BC Ai Account の ${c.mod} 機能で部品表と原価管理をリアルタイムに把握できます。` },
    { role: "caller", text: `タイ人スタッフでも簡単に使えますか？` },
    { role: "ai", text: `はい、画面はタイ語・英語・日本語データに対応しており、日本の管理者様にも分かりやすくご利用いただけます。` },
  ],
  ko: (c) => [
    { role: "caller", text: `안녕하세요, ${c.company}입니다. ${c.topic} 기능 문의드립니다.` },
    { role: "ai", text: `안녕하세요! 한국어로 안내해 드리겠습니다. BC Ai Account는 ${c.mod} 기능을 통해 태국 본사와 지점 재고를 실시간으로 연동할 수 있습니다.` },
    { role: "caller", text: `정말 편리하네요. 견적서 안내도 부탁드립니다.` },
    { role: "ai", text: `네, CRM에 한국어 선호 고객으로 저장해 두었으며 담당자가 바로 맞춤 견적을 안내해 드리겠습니다.` },
  ],
  lo: (c) => [
    { role: "caller", text: `ສະບາຍດີ ຈາກ${c.company} ຢາກຖາມເລື່ອງ${c.topic}` },
    { role: "ai", text: `ສະບາຍດີ ຂໍຕອບເປັນພາສາລາວໃຫ້ເລີຍຄັບ ລະບົບ BC Ai Account ຮອງຮັບ ${c.mod} ແລະການຄ້າຊາຍແດນໄດ້ຄົບຖ້ວນ` },
    { role: "caller", text: `ພະນັກງານໄທກັບລາວໃຊ້ນຳກັນໄດ້ບໍ່` },
    { role: "ai", text: `ໃຊ້ນຳກັນໄດ້ທັນທີຄັບ ລະບົບຈຳພາສາປະຈຳເບີຂອງລູກຄ້າໄວ້ໃຫ້ອັດຕະໂນມັດເລີຍ` },
  ],
};

function isoAt(daysAgo, hour, minute) {
  const d = new Date(Date.now() - daysAgo * 86_400_000);
  d.setHours(hour, minute, Math.floor((hour * 13 + minute) % 55), 0);
  if (d.getTime() > Date.now() - 120_000) {
    d.setTime(Date.now() - (5 + minute % 30) * 60_000);
  }
  return d.toISOString();
}

function main() {
  wipeExistingData();
  ensureDir(DATA_DIR);
  ensureDir(RECORDINGS_DIR);
  ensureDir(path.dirname(PROFILES_PATH));
  ensureDir(path.dirname(QA_CACHE_PATH));

  const customersObj = {};
  const callersObj = {};
  const profilesObj = {};

  // Distribution over last 7 days totaling 165 calls (daysAgo 6 -> 0 rising to 36 today)
  const dayCounts = [16, 19, 22, 23, 24, 25, 36]; // sum = 165
  const callSchedule = [];
  for (let dIdx = 0; dIdx < dayCounts.length; dIdx++) {
    const daysAgo = 6 - dIdx;
    const count = dayCounts[dIdx];
    for (let j = 0; j < count; j++) {
      // Include realistic business hours AND after-hours / weekend SME calls
      const afterHoursSlots = [6, 7, 18, 19, 20, 21, 22, 23];
      const workSlots = [9, 10, 11, 13, 14, 15, 16, 17];
      const isAfterHours = j % 3 === 0;
      const hourPool = isAfterHours ? afterHoursSlots : workSlots;
      const hour = hourPool[(j * 3 + dIdx) % hourPool.length];
      const minute = (j * 11 + dIdx * 7) % 58;
      callSchedule.push({ daysAgo, hour, minute, isAfterHours });
    }
  }

  let seqCounter = 1;
  // Seed customer shells first
  for (let i = 0; i < DEMO_SMES.length; i++) {
    const c = DEMO_SMES[i];
    const phone = `02001${String(101 + i).padStart(4, "0")}`;
    customersObj[phone] = {
      phone,
      name: c.name,
      company: c.company,
      tier: c.tier,
      stage: c.stage,
      preferredLanguage: c.lang,
      tags: [c.mod, c.lang !== "th" ? `ภาษา ${c.lang.toUpperCase()}` : "SME ไทย", c.tier === "vip" ? "VIP" : "สายอัตโนมัติ"].slice(0, 5),
      notes: `ลูกค้าสาธิตกลุ่มธุรกิจ SME (${c.company}) สนใจโมดูล ${c.mod} — สื่อสารหลักด้วยภาษา ${c.lang.toUpperCase()}`,
      avgCsat: 9.0,
      csatCount: 0,
      lastSentiment: "positive",
      totalCalls: 0,
      createdAt: isoAt(6, 9, 0),
      updatedAt: isoAt(0, 8, 0),
      lastCallAt: isoAt(0, 8, 0),
    };
    callersObj[phone] = {
      phone,
      totalCalls: 0,
      lastCallAt: isoAt(6, 9, 0),
      lastDirection: "inbound",
      sessions: [],
    };
    profilesObj[phone] = {
      phone,
      name: c.name,
      company: c.company,
      totalCalls: 0,
      lastTopic: c.topic,
      updatedAt: isoAt(0, 8, 0),
    };
  }

  // Assign 165 sessions round-robin biased toward VIP/multi-call SMEs
  for (let sIdx = 0; sIdx < callSchedule.length; sIdx++) {
    const { daysAgo, hour, minute, isAfterHours } = callSchedule[sIdx];
    const custIdx = sIdx < 48 ? sIdx : (sIdx * 7) % DEMO_SMES.length;
    const c = DEMO_SMES[custIdx];
    const phone = `02001${String(101 + custIdx).padStart(4, "0")}`;
    const startIso = isoAt(daysAgo, hour, minute);
    const durSec = 52 + ((sIdx * 29) % 165);
    const endIso = new Date(Date.parse(startIso) + durSec * 1000).toISOString();
    const sessionId = `demo-sess-${String(seqCounter++).padStart(4, "0")}`;

    const isHighIntent = c.stage === "prospect" || c.tier === "vip" || sIdx % 4 === 0;
    const isResolved = !(c.tier === "at_risk" || sIdx % 5 === 0);
    const sentiment =
      c.tier === "at_risk"
        ? "urgent"
        : isHighIntent
          ? "positive"
          : sIdx % 6 === 0
            ? "neutral"
            : "positive";
    const csatScore = c.tier === "at_risk" ? 7 : isHighIntent ? 10 : sIdx % 3 === 0 ? 8 : 9;
    const aiQualityScore = sIdx % 4 === 0 ? 10 : 9;
    const afterHoursLabel = isAfterHours ? " (รับสายอัตโนมัตินอกเวลาทำการ)" : "";

    const rawMsgs = (LANG_DIALOGUES[c.lang] || LANG_DIALOGUES.th)(c);
    const messages = rawMsgs.map((m, mIdx) => ({
      at: new Date(Date.parse(startIso) + (mIdx + 1) * 14_000).toISOString(),
      role: m.role,
      text: m.text,
    }));

    const analysis = {
      analyzedAt: endIso,
      model: "deepseek-v4.1-flash",
      csatScore,
      aiQualityScore,
      sentiment,
      emotionLabel:
        sentiment === "urgent" ? "เร่งด่วน/กังวล" : sentiment === "positive" ? "พึงพอใจมาก" : "สอบถามปกติ",
      emotionTrend: sentiment === "urgent" ? "กังวล -> รับทราบแนวทาง" : "สนใจ -> มั่นใจพร้อมใช้งาน",
      topic: `${c.topic}${afterHoursLabel}`,
      callerName: c.name,
      callerCompany: c.company,
      summary: `ลูกค้าจาก ${c.company} ติดต่อสอบถามเรื่อง ${c.topic}${afterHoursLabel} โดย J.A.R.V.I.S ตอบครบถ้วนด้วยภาษา ${c.lang.toUpperCase()} พร้อมบันทึกข้อมูลลง CRM เรียบร้อย`,
      resolved: isResolved,
      resolutionSteps: `แนะนำขั้นตอนการใช้งาน ${c.mod} พร้อมยกตัวอย่างการตั้งค่าที่ตรงกับประเภทธุรกิจ SME`,
      needsHumanFollowUp: !isResolved || isHighIntent,
      urgency: c.tier === "at_risk" ? "high" : isHighIntent ? "medium" : "low",
      buyingIntent: isHighIntent ? "high" : sIdx % 2 === 0 ? "medium" : "low",
      interestedModules: [c.mod, "ระบบ J.A.R.V.I.S Call Center AI"],
      followUpAction: isHighIntent
        ? `ส่งใบเสนอราคาแพ็กเกจที่รองรับ ${c.mod} และนัดเดโมออนไลน์ให้ทีม ${c.company}`
        : `ส่งคู่มือการใช้งาน ${c.mod} เพิ่มเติมหากลูกค้าต้องการทดสอบต่อ`,
      callbackAdvice: isHighIntent
        ? `• เปิดสายขอบคุณที่ติดต่อเข้ามา${isAfterHours ? "นอกเวลาทำการ" : ""} และทวนความต้องการเรื่อง ${c.mod}\n• เตรียมสไลด์/เดโมฟังก์ชันที่ตรงธุรกิจของ ${c.company} พร้อมส่งใบเสนอราคาภายในวัน`
        : `• โทรยืนยันผลการใช้งาน ${c.mod} และแนะนำช่องทางสายด่วน AI 02-124-4148 สำหรับถามได้ตลอด 24 ชม.`,
      followUpMessage: `สวัสดีครับคุณ${c.name} (${c.company}) จากทีมงาน BC Call Center นะครับ ขอบคุณที่ติดต่อเข้ามาสอบถามเรื่อง ${c.mod} หากต้องการข้อมูลเพิ่มเติมหรือให้ทีมเดโมดูแล สามารถแจ้งได้เลยนะครับ`,
      aiCoachingTip: `ตอบตรงประเด็น รวดเร็ว และรักษาภาษาสื่อสาร (${c.lang.toUpperCase()}) ได้สม่ำเสมอ`,
    };

    const sessionObj = {
      id: sessionId,
      startedAt: startIso,
      endedAt: endIso,
      durationSec: durSec,
      status: "answered",
      direction: "inbound",
      messages,
      analysisStatus: "done",
      analysis,
      hasRecording: false,
      recordingDurationSec: 0,
    };

    callersObj[phone].sessions.unshift(sessionObj);
    callersObj[phone].totalCalls += 1;
    if (startIso > callersObj[phone].lastCallAt) {
      callersObj[phone].lastCallAt = startIso;
    }

    const crmCust = customersObj[phone];
    const prevSum = crmCust.avgCsat * crmCust.csatCount;
    crmCust.csatCount += 1;
    crmCust.avgCsat = Math.round(((prevSum + csatScore) / crmCust.csatCount) * 10) / 10;
    crmCust.totalCalls += 1;
    crmCust.lastSentiment = sentiment;
    if (startIso > crmCust.lastCallAt) {
      crmCust.lastCallAt = startIso;
      crmCust.updatedAt = endIso;
    }
    profilesObj[phone].totalCalls = crmCust.totalCalls;
    profilesObj[phone].updatedAt = crmCust.updatedAt;
  }

  // Sort caller sessions newest-first
  for (const rec of Object.values(callersObj)) {
    rec.sessions.sort((a, b) => b.startedAt.localeCompare(a.startedAt));
  }

  // Build 32 clean CRM Tickets:
  // 4 Open Urgent Consolidated Callbacks + 10 Open AI Auto + 18 Closed/Resolved
  const tickets = [];
  let tSeq = 32;
  const makeTicketId = (daysAgo) => {
    const d = new Date(Date.now() - daysAgo * 86_400_000);
    const ymd = `${String(d.getFullYear()).slice(-2)}${String(d.getMonth() + 1).padStart(2, "0")}${String(d.getDate()).padStart(2, "0")}`;
    return `TCK-${ymd}-${String(tSeq--).padStart(3, "0")}`;
  };

  const makeTicket = ({
    daysAgo,
    hour,
    minute,
    custIdx,
    source,
    priority,
    status,
    topics,
    mergedCount,
    assignee,
    lineId = null,
    ack = false,
    closeNote = "",
    customAdvice = "",
  }) => {
    const c = DEMO_SMES[custIdx];
    const phone = `02001${String(101 + custIdx).padStart(4, "0")}`;
    const createdAt = isoAt(daysAgo, hour, minute);
    const updatedAt = isoAt(daysAgo, hour, Math.min(59, minute + 6));
    const cleanTopics = topics.map((tp) => cleanTopicText(tp)).filter(Boolean);
    const latestTopic = cleanTopics[cleanTopics.length - 1] || c.topic;

    let subject;
    if (source === "ai_callback") {
      subject =
        mergedCount > 1 || cleanTopics.length > 1
          ? `ขอให้โทรกลับ (รวม ${mergedCount} ครั้ง • ${cleanTopics.length} ประเด็น): ${latestTopic}`
          : `ขอให้โทรกลับ: ${latestTopic}`;
    } else {
      subject = mergedCount > 1 ? `(รวม ${mergedCount} ครั้ง) ${latestTopic}` : latestTopic;
    }

    const descBlocks = [];
    if (cleanTopics.length > 1) {
      descBlocks.push(`ประเด็นที่ลูกค้าแจ้ง (รวม ${mergedCount} ครั้ง):`);
      cleanTopics.forEach((tp, idx) => descBlocks.push(`${idx + 1}. ${tp}`));
    } else {
      descBlocks.push(`เรื่องที่ให้โทรกลับ: ${latestTopic}`);
    }
    if (closeNote) {
      descBlocks.push("");
      descBlocks.push(`[ปิดเคสโดย เจ้าหน้าที่ฝ่ายขาย/ซัพพอร์ต]: ${closeNote}`);
    }
    descBlocks.push("");
    descBlocks.push(`เบอร์ให้โทรกลับ: ${phone}`);
    descBlocks.push(`ชื่อลูกค้า: ${c.name}`);
    descBlocks.push(`บริษัท: ${c.company}`);

    const draft = {
      id: makeTicketId(daysAgo),
      phone,
      callbackPhone: phone,
      callerPhone: phone,
      customerName: c.name,
      company: c.company,
      subject: subject.slice(0, 160),
      description: descBlocks.join("\n").slice(0, 2000),
      topics: cleanTopics,
      mergedCount,
      status,
      priority,
      assignee,
      source,
      sessionId: callersObj[phone]?.sessions?.[0]?.id || null,
      sessionIds: (callersObj[phone]?.sessions || []).slice(0, mergedCount).map((s) => s.id),
      lineId,
      aiCallbackAdvice: customAdvice || "",
      callbackAdvice: "",
      acknowledgedAt: ack || status === "closed" || status === "resolved" ? updatedAt : null,
      acknowledgedBy: ack || status === "closed" || status === "resolved" ? assignee : null,
      closedAt: status === "closed" || status === "resolved" ? updatedAt : null,
      closedBy: status === "closed" || status === "resolved" ? assignee : null,
      createdAt,
      updatedAt,
    };
    draft.callbackAdvice = customAdvice ? customAdvice.slice(0, 1200) : buildCallbackAdvice(draft);
    return draft;
  };

  // Top 4 Open Urgent Consolidated Callbacks
  tickets.push(
    makeTicket({
      daysAgo: 0,
      hour: 7,
      minute: 48,
      custIdx: 0,
      source: "ai_callback",
      priority: "urgent",
      status: "open",
      lineId: 1,
      mergedCount: 3,
      assignee: "ทีมโทรกลับลูกค้า",
      topics: [
        "สอบถามวิธีใช้ระบบคำนวณต้นทุนสินค้าแบบ FIFO และเฉลี่ยสำหรับร้านยาง 5 สาขา",
        "สอบถามวิธีรับชำระหนี้หลายบิลในใบเสร็จเดียวและการวางบิลลูกหนี้บริษัท",
        "ขอใบเสนอราคาแพ็กเกจ Pro พร้อมระบบ BC AutoCare และนัดเดโมออนไลน์",
      ],
      customAdvice:
        "• ลูกค้าติดต่อเข้ามาซ้ำรวม 3 ครั้ง (ร้านยาง 5 สาขา • เกรด VIP) ควรรีบโทรกลับเป็นลำดับแรกและช่วยตอบให้ครบทุกประเด็นในสายเดียว\n• เตรียมไฟล์ใบเสนอราคาแพ็กเกจ Pro + โมดูล BC AutoCare และสไลด์เปรียบเทียบต้นทุน FIFO/เฉลี่ย\n• เตรียมอธิบายขั้นตอนการรับชำระหลายบิลในใบเสร็จเดียวพร้อมนัดช่วงเวลาเดโมออนไลน์ 30 นาที\n• เมื่อตกลงนัดหมายและส่งข้อมูลครบแล้ว กด \"ปิด Ticket (พนักงานรับรู้แล้ว)\" บนหน้าจอทันที",
    }),
    makeTicket({
      daysAgo: 0,
      hour: 7,
      minute: 35,
      custIdx: 1,
      source: "ai_callback",
      priority: "urgent",
      status: "open",
      lineId: 2,
      mergedCount: 2,
      assignee: "ทีมขายโครงการ",
      topics: [
        "สอบถามระบบคุมงวดงาน เงินประกันผลงานรับเหมา 5% และต้นทุนโครงการ รหัส 6018",
        "ขอให้ทีมเดโมโทรอธิบายวิธีออกใบกำกับภาษีและใบลดหนี้ตามงวดงานก่อสร้าง",
      ],
      customAdvice:
        "• ลูกค้าเป็นบริษัทรับเหมาก่อสร้าง ติดต่อมา 2 ครั้ง สนใจเมนูรหัส 6018 โดยเฉพาะ\n• เตรียมหน้าจอตัวอย่างการตั้งค่างวดงาน เงินประกันผลงาน 5–10% และรายงานเทียบงบประมาณกับต้นทุนจริง\n• เตรียมแนะนำการออกใบกำกับภาษีตามงวดงานและแนบลิงก์วิดีโอสาธิตหลังวางสาย",
    }),
    makeTicket({
      daysAgo: 0,
      hour: 7,
      minute: 22,
      custIdx: 34, // English speaker (David Miller)
      source: "ai_callback",
      priority: "urgent",
      status: "open",
      lineId: 3,
      mergedCount: 1,
      assignee: "ทีมซัพพอร์ตและฝ่ายขาย",
      topics: [
        "ลูกค้าต่างชาติคุยภาษาอังกฤษผ่าน J.A.R.V.I.S สอบถามระบบสต็อกหลายคลังและการออกรายงานบัญชี 2 ภาษาสำหรับโรงงาน",
      ],
      customAdvice:
        "• J.A.R.V.I.S สื่อสารภาษาอังกฤษกับลูกค้าให้เรียบร้อยแล้ว พนักงานไม่ต้องกังวลเรื่องกำแพงภาษา\n• เตรียมสรุปโครงสร้างสต็อกหลายคลังและตัวอย่างรายงานงบการเงิน TH/EN ส่งทางอีเมลก่อนโทรยืนยัน\n• หากต้องการนัดประชุมที่มีฝ่ายบัญชีไทยร่วมด้วย เสนอช่วงบ่ายวันนี้เพื่อนำเสนอร่วมกันได้ทันที",
    }),
    makeTicket({
      daysAgo: 0,
      hour: 6,
      minute: 50,
      custIdx: 38, // Chinese speaker after-hours (คุณหลิน)
      source: "ai_callback",
      priority: "urgent",
      status: "open",
      lineId: 1,
      mergedCount: 1,
      assignee: "ทีมดูแลลูกค้านานาชาติ",
      topics: [
        "สายนอกเวลาทำการ (คุยภาษาจีน) สอบถามระบบ Lot/วันหมดอายุ FEFO โรงงานอาหารและต้องการให้ฝ่ายขายโทรกลับช่วงเช้า",
      ],
      customAdvice:
        "• ลูกค้าโทรเข้ามาช่วงนอกเวลาทำการและคุยภาษาจีนกับ J.A.R.V.I.S สำเร็จแล้ว ช่วยไม่ให้พลาดลีดโรงงานขนาดกลาง (15 ผู้ใช้)\n• เตรียมรายละเอียดแพ็กเกจ Pro + ตัวอย่างหน้าจอ Lot/FEFO และส่งข้อความทักทายเบื้องต้นก่อนโทรติดตาม",
    })
  );

  // 10 Open AI Auto follow-up tickets (different synthetic phones)
  const autoCustIndices = [3, 4, 8, 11, 16, 23, 30, 36, 42, 44];
  for (let k = 0; k < autoCustIndices.length; k++) {
    const idx = autoCustIndices[k];
    const c = DEMO_SMES[idx];
    tickets.push(
      makeTicket({
        daysAgo: k < 4 ? 0 : 1,
        hour: 9 + (k % 8),
        minute: (k * 9 + 12) % 55,
        custIdx: idx,
        source: "ai_auto",
        priority: c.tier === "at_risk" ? "urgent" : k % 2 === 0 ? "high" : "medium",
        status: k < 2 ? "in_progress" : "open",
        mergedCount: k % 3 === 0 ? 2 : 1,
        assignee: c.tier === "at_risk" ? "ฝ่ายซัพพอร์ตเร่งด่วน" : "ทีมดูแลลูกค้า",
        topics: [c.topic],
        ack: k < 2,
      })
    );
  }

  // 18 Closed / Resolved tickets across past 6 days
  const closedCustIndices = [2, 5, 6, 7, 9, 10, 12, 13, 14, 15, 17, 18, 19, 20, 21, 22, 24, 46];
  for (let k = 0; k < closedCustIndices.length; k++) {
    const idx = closedCustIndices[k];
    const c = DEMO_SMES[idx];
    tickets.push(
      makeTicket({
        daysAgo: 1 + (k % 6),
        hour: 10 + (k % 7),
        minute: (k * 7 + 5) % 55,
        custIdx: idx,
        source: k % 3 === 0 ? "ai_callback" : "ai_auto",
        priority: k % 4 === 0 ? "high" : "medium",
        status: k % 5 === 0 ? "resolved" : "closed",
        mergedCount: k % 4 === 0 ? 2 : 1,
        assignee: "ทีมดูแลลูกค้า SME",
        topics: [c.topic],
        closeNote: `ติดต่อแนะนำ ${c.mod} และส่งคู่มือการใช้งานให้คุณ${c.name} เรียบร้อยแล้ว`,
      })
    );
  }

  // Write clean QA FAQ Cache for Analytics ROI tab
  const nowSec = Date.now() / 1000;
  const qaCacheEntries = [
    {
      q: "ราคาโปรแกรม BC Ai Account มีกี่รุ่น และคิดราคายังไง",
      norm: "ราคาโปรแกรมบีซีเอไอแอคเคาท์มีกี่รุ่นและคิดราคายังไง",
      lang: "th",
      hits: 28,
      taught: true,
      sentences: [
        "โปรแกรม BC Ai Account มี 4 รุ่นครับ ได้แก่ Starter ฟรีศูนย์บาท และรุ่น Mini Standard Pro ที่คิดตามจำนวนผู้ใช้งานพร้อมกัน",
        "ทุกแพ็กเกจสร้างรหัสพนักงานได้ไม่จำกัดคนครับ อยากให้ช่วยแนะนำรุ่นที่เหมาะกับธุรกิจไหนดีครับ",
      ],
    },
    {
      q: "คุมงวดงานก่อสร้างและเงินประกันผลงานใช้เมนูไหน",
      norm: "คุมงวดงานก่อสร้างและเงินประกันผลงานใช้เมนูไหน",
      lang: "th",
      hits: 19,
      taught: true,
      sentences: [
        "อยู่ที่เมนูลูกหนี้ หัวข้อคุมงวดงานกับเงินประกันผลงานรับเหมาและต้นทุนโครงการ รหัสหกศูนย์หนึ่งแปดครับ",
        "รองรับทั้งงานรับเหมาหลักและจ้างช่วง พร้อมเทียบงบประมาณกับต้นทุนจริงให้อัตโนมัติครับ",
      ],
    },
    {
      q: "รองรับการรับชำระหนี้หลายบิลในใบเสร็จเดียวไหม",
      norm: "รองรับการรับชำระหนี้หลายบิลในใบเสร็จเดียวไหม",
      lang: "th",
      hits: 16,
      taught: true,
      sentences: [
        "รองรับครับ สามารถเลือกใบแจ้งหนี้หรือใบวางบิลหลายใบมารวมออกใบเสร็จรับเงินใบเดียวได้เลย",
        "และยังรองรับการรับชำระบางส่วนหรือตัดเช็คคืนครบวงจรครับ",
      ],
    },
    {
      q: "J.A.R.V.I.S คุยได้กี่ภาษาและช่วยพนักงานยังไง",
      norm: "จาร์วิสคุยได้กี่ภาษาและช่วยพนักงานยังไง",
      lang: "th",
      hits: 22,
      taught: true,
      sentences: [
        "J.A.R.V.I.S รองรับ 6 ภาษาอัตโนมัติครับ ทั้งไทย อังกฤษ จีน ญี่ปุ่น เกาหลี และลาว",
        "ช่วยรับสายลูกค้าต่างชาติและบันทึกสรุปความต้องการเป็นภาษาไทยให้พนักงานโทรกลับหรือส่งต่อได้ทันทีครับ",
      ],
    },
    {
      q: "ระบบ BC AutoCare สำหรับร้านยางและอู่ซ่อมรถทำอะไรได้บ้าง",
      norm: "ระบบบีซีออโต้แคร์สำหรับร้านยางและอู่ซ่อมรถทำอะไรได้บ้าง",
      lang: "th",
      hits: 15,
      taught: true,
      sentences: [
        "ช่วยเปิดใบซ่อมจากทะเบียนรถ เก็บประวัติเช็กระยะ คุมสต็อกยางและอะไหล่หลายสาขา",
        "พร้อมคำนวณต้นทุนและเชื่อมเข้าสู่ระบบบัญชีและภาษีอัตโนมัติครับ",
      ],
    },
    {
      q: "ออกใบกำกับภาษีอิเล็กทรอนิกส์ e-Tax และปิดงบการเงินได้ไหม",
      norm: "ออกใบกำกับภาษีอิเล็กทรอนิกส์etaxและปิดงบการเงินได้ไหม",
      lang: "th",
      hits: 14,
      taught: false,
      sentences: [
        "ได้ครับ ระบบรองรับ e-Tax Invoice รายงานภาษีซื้อ-ขาย ภ.พ.30 หัก ณ ที่จ่าย และปิดงบการเงินครบชุดครับ",
      ],
    },
    {
      q: "Does BC Ai Account support English and multi-warehouse inventory?",
      norm: "doesbcaiaccountsupportenglishandmultiwarehouseinventory",
      lang: "en",
      hits: 11,
      taught: false,
      sentences: [
        "Yes! BC Ai Account supports multi-warehouse FIFO and average costing along with real-time branch transfers.",
        "I can also capture your inquiry and schedule a bilingual product walkthrough for your team.",
      ],
    },
    {
      q: "你们的系统支持食品工厂批次号和效期先进先出管理吗？",
      norm: "你们的系统支持食品工厂批次号和效期先进先出管理吗",
      lang: "zh",
      hits: 9,
      taught: false,
      sentences: [
        "支持的！系统原生提供 Lot 批次追踪、效期提醒以及 FEFO 先到期先出功能，非常适合食品和医疗批发企业。",
      ],
    },
    {
      q: "ระบบตัดสต็อกแบบ FEFO สำหรับคลินิกและร้านเบเกอรี่ตั้งค่ายังไง",
      norm: "ระบบตัดสต็อกแบบfefoสำหรับคลินิกและร้านเบเกอรี่ตั้งค่ายังไง",
      lang: "th",
      hits: 8,
      taught: false,
      sentences: [
        "เปิดใช้งานระบบ Lot และวันหมดอายุในเมนูสินค้า ระบบจะแนะนำล็อตที่ใกล้หมดอายุก่อนให้อัตโนมัติตอนตัดบิลขายครับ",
      ],
    },
    {
      q: "ถ้าอยากทดลองใช้งานหรือติดต่อเจ้าหน้าที่ต้องทำยังไง",
      norm: "ถ้าอยากทดลองใช้งานหรือติดต่อเจ้าหน้าที่ต้องทำยังไง",
      lang: "th",
      hits: 17,
      taught: false,
      sentences: [
        "สามารถแจ้งชื่อ เบอร์ติดต่อกลับ และเรื่องที่ต้องการให้เจ้าหน้าที่โทรกลับได้เลยครับ หรือโทรสายตรงเข้ามาทดลองคุยที่ศูนย์สอง หนึ่งสองสี่ สี่หนึ่งสี่แปดได้ตลอด 24 ชั่วโมงครับ",
      ],
    },
  ];

  const qaCacheObj = {};
  qaCacheEntries.forEach((e, idx) => {
    const k = `${e.lang}|phone|${e.norm}`;
    qaCacheObj[k] = {
      question: e.q,
      norm: e.norm,
      lang: e.lang,
      mode: "phone",
      sentences: e.sentences,
      saved_at: nowSec - (qaCacheEntries.length - idx) * 3600,
      last_hit_at: nowSec - idx * 300,
      hits: e.hits,
      taught: e.taught,
    };
  });

  fs.writeFileSync(
    CRM_PATH,
    JSON.stringify({ customers: customersObj, tickets }, null, 2),
    "utf8"
  );
  fs.writeFileSync(
    MEM_PATH,
    JSON.stringify({ callers: callersObj }, null, 2),
    "utf8"
  );
  fs.writeFileSync(PROFILES_PATH, JSON.stringify(profilesObj, null, 2), "utf8");
  fs.writeFileSync(QA_CACHE_PATH, JSON.stringify(qaCacheObj, null, 2), "utf8");

  const langs = Object.values(customersObj).reduce((acc, c) => {
    acc[c.preferredLanguage] = (acc[c.preferredLanguage] || 0) + 1;
    return acc;
  }, {});
  const totalCalls = Object.values(callersObj).reduce((sum, r) => sum + r.sessions.length, 0);
  const openCb = tickets.filter((t) => t.source === "ai_callback" && t.status === "open").length;
  const openAll = tickets.filter((t) => t.status !== "closed" && t.status !== "resolved").length;

  console.log(
    JSON.stringify(
      {
        wiped: true,
        customers: Object.keys(customersObj).length,
        languages: langs,
        totalCalls,
        ticketsTotal: tickets.length,
        openCallbacks: openCb,
        openTickets: openAll,
        qaCacheEntries: Object.keys(qaCacheObj).length,
      },
      null,
      2
    )
  );
}

main();
