"""Sync Embedded BC Brain vault into PostgreSQL 18 pgvector (HNSW + Thai FTS) and run accuracy/speed benchmark.

Usage:
  D:\\bctest01\\.venv\\Scripts\\python.exe -X utf8 scripts\\sync-brain-pgvector.py
  D:\\bctest01\\.venv\\Scripts\\python.exe -X utf8 scripts\\sync-brain-pgvector.py --force --benchmark
  D:\\bctest01\\.venv\\Scripts\\python.exe -X utf8 scripts\\sync-brain-pgvector.py --stats
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
JARVIS_DIR = os.path.join(ROOT, "jarvis")
if JARVIS_DIR not in sys.path:
    sys.path.insert(0, JARVIS_DIR)

from brain_mirror import BrainVaultMirror, DEFAULT_LOCAL_VAULT_DIR, segment_thai_text  # noqa: E402
from brain_pgvector import get_pgvector_store  # noqa: E402

# 80-query Golden Evaluation Suite spanning all modules 1xxx–9xxx, new user-zone notes, spoken paraphrases & 8 OOV traps
_BENCHMARK_CASES: list[tuple[str, str, tuple[str, ...]]] = [
    # --- Promotions, Pricing & POS (10) ---
    ("โปรแกรมมีโปรโมชั่นกี่แบบครับ", "hit", ("howto-pos-hardware-display-promo", "10 รูปแบบ", "2015")),
    ("ซื้อสองแถมหนึ่งตั้งค่ายังไง", "hit", ("howto-pos-hardware-display-promo", "howto-price-center-discount-formula", "โปรโมชัน")),
    ("พักบิลหน้าร้านกับปิดกะทำยังไง", "hit", ("howto-pos-cashier-shift", "พักบิล", "ปิดกะ")),
    ("ต่อเครื่องพิมพ์ใบเสร็จ 58 หรือ 80 มิล กับลิ้นชักเก็บเงินได้ไหม", "hit", ("howto-pos-hardware-display-promo", "ลิ้นชัก", "ใบเสร็จ")),
    ("อินเทอร์เน็ตที่ร้านหลุดหน้าร้าน POS ยังขายได้ไหม", "hit", ("ออฟไลน์", "ซิงก์", "pos")),
    ("ตั้งราคาขายหลายระดับตามกลุ่มลูกค้าหรือสูตรราคาทำยังไง", "hit", ("howto-price-center-discount-formula", "ศูนย์ราคา", "ราคา")),
    ("ปรับราคาสินค้าทีละหลายตัวทำเมนูไหน", "hit", ("ปรับราคา", "2014", "ราคา")),
    ("โปรแกรมมีกี่แพ็กเกจราคาเท่าไหร่ครับ", "hit", ("sales-pricing-packages", "แพ็กเกจ", "ราคา")),
    ("สำนักงานบัญชีใช้ฟรีไหมมีโครงการพาร์ตเนอร์หรือเปล่า", "hit", ("sales-accounting-partner", "สำนักงานบัญชี", "ฟรี")),
    ("ทดลองใช้งานหรือขอใบเสนอราคาติดต่อช่องทางไหน", "hit", ("sales-support-implementation-contact", "bcaccount", "ใบเสนอราคา")),
    # --- Inventory, Costing, Barcodes, Warehouse & WMS (16) ---
    ("บาร์โค้ดใช้ได้กี่หลักครับ", "hit", ("howto-stock-units-barcodes", "EAN-13", "บาร์โค้ด")),
    ("โปรแกรมคิดต้นทุนแบบเข้าทีหลังออกก่อนได้ไหม", "hit", ("term-cost-method", "faq-stock-cost", "ถัวเฉลี่ย", "FIFO")),
    ("ต้นทุนถัวเฉลี่ยกับ FIFO และ FEFO ต่างกันยังไง", "hit", ("term-cost-method", "FEFO", "FIFO")),
    ("เปิดบิลไปแล้วเปลี่ยนวิธีคิดต้นทุนทีหลังได้ไหม", "hit", ("term-cost-method", "ล็อก", "เปลี่ยน")),
    ("คิดต้นทุนแยกตามแต่ละคลังสินค้าได้ไหม", "hit", ("term-cost-method", "แยกคลัง", "คลัง")),
    ("สต็อกติดลบห้ามขายติดลบตั้งค่าตรงไหน", "hit", ("ts-stock-negative-control", "faq-negative-stock", "1004", "สต็อกติดลบ")),
    ("ยอดสต็อกไม่ตรงกับของจริงคำนวณใหม่ตรงไหน", "hit", ("ts-stock-cost-mismatch-rebuild", "2050", "คำนวณยอดสต็อกใหม่")),
    ("ยอดคงเหลือตามคลังแยกกันหรือดูรวมทุกคลังดูเมนูไหน", "hit", ("rpt-stock-balance-warehouse-summary", "2054", "2055")),
    ("ดูยอดคงเหลือสินค้าตามที่เก็บหรือตาม LOT กดเมนูอะไร", "hit", ("rpt-stock-balance-warehouse-summary", "2056", "2057")),
    ("ซื้อเป็นลังขายปลีกเป็นชิ้นตั้งค่าหน่วยนับตรงไหน", "hit", ("howto-setup-warehouse-unit", "howto-stock-units-barcodes", "1030", "หน่วยนับ")),
    ("คุมสินค้าด้วย Serial Number หรือ IMEI ทำได้ไหม", "hit", ("serial", "imei", "2053")),
    ("สร้างสูตรผลิตหรือประกอบชุดสินค้า BOM ทำยังไง", "hit", ("bom", "ประกอบ", "สูตร")),
    ("ตรวจนับสต็อกแล้วปรับปรุงยอดต่างทำเมนูไหน", "hit", ("ตรวจนับ", "ปรับปรุง", "สต็อก")),
    ("โอนสินค้าระหว่างคลังทำยังไง", "hit", ("โอนสินค้า", "คลัง", "20")),
    ("ระบบคลังสินค้า WMS แผนผังจุดเก็บ Bin Map หยิบแพ็คทำงานยังไง", "hit", ("howto-wms-pick-pack-bin", "sys-wms-warehouse", "ชั้นวาง")),
    ("ต้นทุนแฝงอย่างค่าขนส่งนำเข้าเอามาเฉลี่ยต้นทุนสินค้าได้ไหม", "hit", ("ต้นทุนแฝง", "2051", "เฉลี่ย")),
    # --- Sales, AR, Credit Notes, Deposits & Marketplace (12) ---
    ("เปิดบิลขายเชื่อออกใบกำกับภาษีทำเมนูไหน", "hit", ("howto-sale-invoice", "4001", "4013", "ขาย")),
    ("ทำใบเสนอราคาแล้วดึงมาเป็นใบสั่งขายและบิลขายยังไง", "hit", ("ใบเสนอราคา", "ใบสั่งขาย", "40")),
    ("ทำใบลดหนี้ลูกค้าคืนสินค้ากดเมนูอะไร", "hit", ("howto-sale-return-cn-dn", "ใบลดหนี้", "ลดหนี้")),
    ("วางบิลรวมหลายบิลให้ลูกหนี้ทำยังไง", "hit", ("วางบิล", "ลูกหนี้", "60")),
    ("รับชำระหนี้ลูกหนี้ตัดบิลเก่าสุด FIFO ยังไง", "hit", ("howto-ar-receipt-fifo", "รับชำระหนี้", "FIFO")),
    ("เงินมัดจำกับเงินล่วงหน้าต่างกันยังไง", "hit", ("faq-deposit-vs-advance", "เงินมัดจำ", "ล่วงหน้า")),
    ("ยอดลูกหนี้เจ้าหนี้ค้างไม่ตรงบิลคำนวณใหม่ยังไง", "hit", ("ts-arap-balance-recalc", "คำนวณ", "ลูกหนี้")),
    ("อยากสรุปยอดขายรายวันว่าวันนี้ขายได้เท่าไหร่ดูรายงานไหน", "hit", ("rpt-sales-by-branch", "4052", "ยอดขาย")),
    ("อยากดูยอดขายและงบกำไรขาดทุนแยกตามสาขาเปิดเมนูไหน", "hit", ("rpt-sales-by-branch", "9063", "9058", "สาขา")),
    ("ขายของบนช้อปปี้ลาซาด้าติ๊กต็อกออกใบกำกับอย่างย่อได้ไหม", "hit", ("shopee", "lazada", "อย่างย่อ", "marketplace")),
    ("เช็กอายุหนี้ลูกหนี้ค้างชำระเกินกำหนดดูรายงานไหน", "hit", ("อายุหนี้", "ลูกหนี้", "1001")),
    ("ตั้งวงเงินสินเชื่อและวันปิดเครดิตลูกค้าทำตรงไหน", "hit", ("วงเงิน", "เครดิต", "ลูกหนี้")),
    # --- Purchasing & AP (7) ---
    ("บันทึกซื้อเชื่อเจ้าหนี้เข้าเมนูไหน", "hit", ("howto-buy-invoice", "menu-cat-3-purchase", "3001", "ซื้อ")),
    ("ใบขอซื้อ PR ใบสั่งซื้อ PO ขั้นตอนอนุมัติทำงานยังไง", "hit", ("ใบสั่งซื้อ", "ขอซื้อ", "อนุมัติ")),
    ("จ่ายชำระหนี้เจ้าหนี้ตัด FIFO ทำที่เมนูไหน", "hit", ("howto-ap-payment", "5016", "เจ้าหนี้")),
    ("ยกเลิกใบจ่ายชำระหนี้เจ้าหนี้ที่คีย์ผิดกดเมนูไหน", "hit", ("howto-ap-payment", "5023", "ยกเลิก")),
    ("สแกนใบกำกับภาษีซื้อเทียบ PO แบบ 3-Way Match ใช้เมนูไหน", "hit", ("howto-ap-payment", "5017", "3-Way")),
    ("ทำใบส่งคืนสินค้าหรือลดหนี้ซื้อจากผู้ขายทำยังไง", "hit", ("ส่งคืน", "ลดหนี้", "ซื้อ")),
    ("รับวางบิลเจ้าหนี้ก่อนจ่ายเงินทำยังไง", "hit", ("รับวางบิล", "เจ้าหนี้", "50")),
    # --- Cash, Bank, Cheques, Payroll & Fixed Assets (8) ---
    ("ลงทะเบียนเช็ครับแล้วเช็คเด้งจัดการยังไง", "hit", ("howto-cheque-received", "เช็คเด้ง", "เช็ครับ")),
    ("บันทึกเช็คจ่ายและตัดผ่านบัญชีธนาคารทำยังไง", "hit", ("เช็คจ่าย", "ธนาคาร", "70")),
    ("นำเข้าสเตทเมนต์ธนาคารมากระทบยอดทำยังไง", "hit", ("สเตทเมนต์", "กระทบยอด", "ธนาคาร")),
    ("ระบบมีคิดเงินเดือน ประกันสังคม และ ภ.ง.ด.1 ไหม", "hit", ("7005", "เงินเดือน", "ประกันสังคม")),
    ("คิดค่าเสื่อมราคาสินทรัพย์ถาวรรายเดือนทำยังไง", "hit", ("howto-asset-overview", "ค่าเสื่อม", "8001")),
    ("บันทึกสัญญาเช่าซื้อรถยนต์หรือลีสซิ่งทำยังไง", "hit", ("เช่าซื้อ", "ลีสซิ่ง", "สินทรัพย์")),
    ("บริหารเงินสดย่อยและใบเบิกค่าใช้จ่ายทำยังไง", "hit", ("เงินสด", "ค่าใช้จ่าย", "70")),
    ("รับชำระเงินด้วยบัตรเครดิตหรือ QR พร้อมเพย์ทำยังไง", "hit", ("พร้อมเพย์", "บัตรเครดิต", "QR")),
    # --- GL, Tax, Period Close, Reports & Setup (13) ---
    ("ขึ้นแจ้งเตือนงวดบัญชีปิดแล้วแก้ยังไง", "hit", ("ts-common-period-closed", "howto-gl-period-open-close", "ปิดงวด")),
    ("วิธีปิดงวดบัญชีสิ้นปีขึ้นปีบัญชีใหม่ต้องทำอะไรบ้าง", "hit", ("ปิดงวด", "สิ้นปี", "howto-gl-period-open-close")),
    ("ยื่นภาษีมูลค่าเพิ่ม ภ.พ.30 ภาษีซื้อภาษีขายดูรายงานไหน", "hit", ("howto-tax-vat-input-output", "ภ.พ.30", "ภาษีซื้อ")),
    ("หัก ณ ที่จ่าย ภ.ง.ด. 3 กับ 53 ออกหนังสือรับรองตรงไหน", "hit", ("howto-tax-wht-and-rd-export", "ภ.ง.ด.", "หัก ณ ที่จ่าย")),
    ("ดึงงบทดลองงบกำไรขาดทุนงบดุลดูตรงไหน", "hit", ("rpt-gl-financial", "งบทดลอง", "งบกำไรขาดทุน", "งบดุล")),
    ("ส่งงบการเงิน DBD e-Filing สบช.3 ทำที่เมนูไหน", "hit", ("dbd", "สบช", "9029")),
    ("ตั้งค่าแยกสาขากับแผนกและบังคับเลือกสาขาก่อนบันทึกทำยังไง", "hit", ("howto-setup-branch-dept", "1007", "1004")),
    ("สร้างบริษัทใหม่หลายนิติบุคคลแยกฐานข้อมูลได้ไหม", "hit", ("sales-multi-branch-permissions-security", "นิติบุคคล", "บริษัท")),
    ("ระบบนี้มีเมนูกี่ตัวทั้งหมดและแบ่งหมวดยังไง", "hit", ("sys-menu-count-overview", "735", "1001")),
    ("กำหนดสิทธิ์พนักงานไม่ให้เห็นข้อมูลข้ามสาขาหรือห้ามลบบิลทำยังไง", "hit", ("สิทธิ์", "สาขา", "ห้าม")),
    ("หาเมนูไม่เจอเมนูหายไปทำยังไงดี", "hit", ("ts-menu-missing-switchboard", "Switchboard", "เมนูหาย")),
    ("คีย์ลัดปุ่มลัดที่ใช้บ่อยมีอะไรบ้าง", "hit", ("howto-screen-shortcuts-workbench", "คีย์ลัด", "Ctrl")),
    ("พิมพ์ฟอร์มเอกสารไม่ออกหรืออยากออกแบบแบบฟอร์มเองทำเมนูไหน", "hit", ("1096", "1097", "แบบฟอร์ม", "พิมพ์")),
    # --- Specialized Modules & Honest System Boundaries (6) ---
    ("มีระบบเฝ้าระวังทุจริตพนักงานหน้าร้านไหม", "hit", ("sys-workmonitor-3d", "ทุจริต", "เฝ้าระวัง")),
    ("รองรับธุรกิจร้านยางรถยนต์หรืออู่ซ่อมรถไหม", "hit", ("sys-autocare-garage", "ยาง", "อู่ซ่อมรถ")),
    ("ศูนย์แจ้งเตือนยามเช้าเมนู 1001 ดูอะไรได้บ้าง", "hit", ("sys-companybrain-briefing", "1001", "ลูกหนี้")),
    ("ใช้กับร้านอาหารที่มีผังโต๊ะกับจอครัวได้ไหม", "hit", ("faq-system-boundaries-not-supported", "sales-current-limits", "ผังโต๊ะ")),
    ("มีระบบรวมงบการเงินหลายบริษัทอัตโนมัติ Consolidation ไหม", "hit", ("faq-system-boundaries-not-supported", "sales-current-limits", "Consolidation")),
    ("ย้ายข้อมูลจาก BC 5.5 หรือ Excel มา BC Ai Account ทำได้ไหม", "hit", ("sales-data-migration", "sales-faq-before-buy", "Excel")),
    # --- 12 Real-Call STT-Noisy & Holistic Context Cases (2026-11-25 cetb1 / 5acje) ---
    ("เอ่อแล้วถ้าผมมีสิบสาขาห้ามบริษัท หนึ่งร้อยยูเซอร์ต้องใช้แบบไหน", "hit", ("sales-pricing-packages", "Enterprise", "9,990", "51–100")),
    ("ผมอยากรู้ว่าโปรแกรมคุณทำโปโมชั่นได้กี่แบบ", "hit", ("howto-pos-hardware-display-promo", "10 รูปแบบ", "โปรโมชัน")),
    ("โปรแกรมไม่มีโปโมชั่นเหรอระบบโป้โมเมชั่นนัดสโนมัติ", "hit", ("howto-pos-hardware-display-promo", "10 รูปแบบ", "โปรโมชัน")),
    ("แล้วคุณมีกี่แบบโปรโมชั่นมี กี่แอบ", "hit", ("howto-pos-hardware-display-promo", "10 รูปแบบ", "โปรโมชัน")),
    ("แล้วถ้าซื้อสามแถมหนึ่งหรือซือห้าแถบสิบ", "hit", ("howto-pos-hardware-display-promo", "howto-price-center-discount-formula", "โปรโมชัน")),
    ("แล้วคุณรองรับระบบขนส่งไหมผมเป็นธุรกิจขนทรง", "hit", ("sys-tms-transport", "TMS", "ขนส่ง")),
    ("ถ้าผมใช้บ้านเชียงไว้ท่านเก่าอยู่ทำยังไง", "hit", ("sales-data-migration-bc55-champ-excel", "BC 5.5", "ย้าย")),
    ("ผมใช้โปรแกรมเก่าอยู่อ่ะ โประแก็มรุ่นห้าจุดห้าร้อยทำยังไง", "hit", ("sales-data-migration-bc55-champ-excel", "BC 5.5", "ย้าย")),
    ("แล้วคุณมีเว็บไซต์หรือเปล่า", "hit", ("sales-support-implementation-contact", "bcaccount.com")),
    ("คุณมีเบอร์หรือเปล่าเบลอออฟฟิศของบ้านเชียง", "hit", ("sales-support-implementation-contact", "บ้านเชียงซอฟท์", "bcaccount.com")),
    ("แล้วคุณอยู่ที่ไหนบ้าง ที่เชียงใหม่อยู่ไหน", "hit", ("sales-support-implementation-contact", "เชียงใหม่", "แจ้งวัฒนะ")),
    ("แล้วถ้าผมมีหลายสาขาทำยังไงขอพิธีการด้วย บริษัทเดียวแต่มีหลายสาขา", "hit", ("howto-setup-branch-dept", "1007", "สาขา")),
    # --- 14 Real-Call Target-Precision Cases (2026-11-25 t8o1y — verify EXACT fact/number/menu in answer!) ---
    ("แบบนี้ราคาเท่าไหร่ตัวใหม่เนี่ย", "hit_all", ("199", "499", "999")),
    ("ถ้าผมมีการซื้อสินค้าจากต่างประเทศระบบนี้รองรับไหม", "hit_all", ("2051", "CIF")),
    ("ลงบัญชีลงมัญติเงินผลต่างอัตราแรกเปลี่ยนทำยังไง", "hit_all", ("9021", "อัตราแลกเปลี่ยน")),
    ("การซื้อขายต่างประเทศใช่ไหมซือขาสินค้าต่ำประเด็น", "hit_all", ("2051", "ศุลกากร")),
    ("ถ้าผมมีคังสินค้าอยู่คนละจังหวัดต้องทำยังไง", "hit_all", ("1027", "คลัง")),
    ("ผมใช้โปรแกรมบ้านเชียงเวอร์ชั่นเก่าทำยังไง", "hit_all", ("BC 5.5", "ย้าย")),
    ("สรุประบบคุณมีโปรโมชั่นกี่แบบ", "hit_all", ("10 รูปแบบ", "2015")),
    ("ถ้าผมจะตั้งโปรโมชั่นซื้อห้าแถมหนึ่งต้องทำยังไง", "hit_all", ("2015", "ซื้อครบแถม")),
    ("แล้วระบบสะสมแต้มทำยังไง", "hit_all", ("1022", "แต้มสะสม")),
    ("เอาแต้มไปเป็นส่วนรถเลยได้ไหม", "hit_all", ("บันทึกตัดแต้ม", "1022")),
    ("แล้วมีระบบแต้มแบบครอบครัวไหมลูกค้าหลายคนแต่รวมแต้", "hit_all", ("กลุ่มสมาชิกรวมแต้ม", "กองกลาง")),
    ("แล้วเบอร์โทรศัพท์ลูกค้าได้กี่เบิร์", "hit_all", ("หลายรายไม่จำกัด", "3 ช่อง")),
    ("แล้วที่อยู่จากส่งมีพิกัดดาวเทียมไหม", "hit_all", ("ละติจูด", "ลองจิจูด")),
    ("แล้วระบบนี้เก็บรูปได้กี่ลูก", "hit_all", ("5 ช่องต่อสินค้า", "25 MB")),
    # --- 2 Competitor Comparison Cases (2026-11-25 0gr19 — stay on POS/BC highlights, never jump to Excel) ---
    ("เทียบกับซอฟต์แวร์ของวงในของคุณดีกว่ายังไง POS", "hit_all", ("POS", "ออฟไลน์")),
    ("ดีกับของคนอื่นยังไงบ้างวงในระบบ POS", "hit_all", ("POS", "โปรโมชัน")),
    # --- 4 Real-Call Cases (2026-11-26 2dg88 — competitor highlights, 15 systems count, 735 menus) ---
    ("โปรแกรมคุณเก่งกว่าโประแกมป์อื่นยังไง", "hit_all", ("BC Ai Account", "15 ระบบ")),
    ("คุณมีกี่ระบบ", "hit_all", ("15 ระบบ", "4 กลุ่ม")),
    ("มีระบบอะไรบ้าง", "hit_all", ("15 ระบบ", "POS")),
    ("แล้วระบบบัญชีของคุณมีกี่เมนู", "hit_all", ("735", "9 หมวด")),
    # --- 8 OOV / Abstain Traps — MUST abstain cleanly without fabricating facts ---
    ("เมนู 9999 ใช้ทำอะไรครับ", "abstain", ()),
    ("เมนู 8888 อยู่ตรงไหนครับ", "abstain", ()),
    ("ราคาทองคำแท่งวันนี้บาทละเท่าไหร่ครับ", "abstain", ()),
    ("อากาศที่เชียงใหม่พรุ่งนี้ฝนจะตกไหม", "abstain", ()),
    ("วิธีปลูกทุเรียนหมอนทองให้ได้ผลดก", "abstain", ()),
    ("ผลบอลพรีเมียร์ลีกเมื่อคืนใครชนะ", "abstain", ()),
    ("สูตรต้มยำกุ้งน้ำข้นใส่อะไรบ้าง", "abstain", ()),
    ("ดาวอังคารห่างจากโลกกี่กิโลเมตร", "abstain", ()),
]


def run_benchmark(mirror: BrainVaultMirror) -> dict:
    # Warm query path once
    pg = get_pgvector_store()
    pg.warmup()

    hit_pass = 0
    hit_total = 0
    abstain_pass = 0
    abstain_total = 0
    latencies_ms: list[float] = []
    details: list[dict] = []

    for q, mode, expected in _BENCHMARK_CASES:
        t0 = time.perf_counter()
        ans = mirror.quick_answer(q, audience="user", mode="ultra")
        ms = round((time.perf_counter() - t0) * 1000.0, 1)
        latencies_ms.append(ms)
        is_abstain = "🛑 Abstain" in ans
        has_media = ("youtube.com" in ans.lower()) or ("youtu.be" in ans.lower()) or ("![" in ans)

        if mode in ("hit", "hit_all"):
            hit_total += 1
            ans_low = ans.lower()
            kw_ok = all(k.lower() in ans_low for k in expected) if mode == "hit_all" else any(k.lower() in ans_low for k in expected)
            matched = (not is_abstain) and (not has_media) and kw_ok
            if matched:
                hit_pass += 1
            details.append({"q": q, "mode": mode, "ok": matched, "ms": ms, "abstain": is_abstain, "media": has_media, "head": ans.splitlines()[0] if ans else ""})
        else:
            abstain_total += 1
            matched = is_abstain
            if matched:
                abstain_pass += 1
            details.append({"q": q, "mode": mode, "ok": matched, "ms": ms, "abstain": is_abstain, "head": ans.splitlines()[0] if ans else ""})

    latencies_sorted = sorted(latencies_ms)
    p50 = latencies_sorted[len(latencies_sorted) // 2] if latencies_sorted else 0.0
    p95 = latencies_sorted[min(len(latencies_sorted) - 1, int(len(latencies_sorted) * 0.95))] if latencies_sorted else 0.0
    avg = round(sum(latencies_ms) / max(1, len(latencies_ms)), 1)

    return {
        "hitPass": f"{hit_pass}/{hit_total}",
        "abstainPass": f"{abstain_pass}/{abstain_total}",
        "avgMs": avg,
        "p50Ms": p50,
        "p95Ms": p95,
        "failures": [d for d in details if not d["ok"]],
        "details": details,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Sync Embedded BC Brain vault into PostgreSQL 18 pgvector")
    ap.add_argument("--force", action="store_true", help="Force re-embedding all chunks even if hashes match")
    ap.add_argument("--stats", action="store_true", help="Print pgvector index stats only")
    ap.add_argument("--benchmark", action="store_true", help="Run 30-query Thai accuracy & latency benchmark")
    ap.add_argument("--vault-dir", default=DEFAULT_LOCAL_VAULT_DIR, help="Path to bcbrain/vault")
    args = ap.parse_args()

    pg = get_pgvector_store()
    if args.stats:
        print(json.dumps(pg.stats(), ensure_ascii=False, indent=2))
        return 0

    mirror = BrainVaultMirror(vault_dir=args.vault_dir, enable_pgvector=True)
    load_st = mirror.load_from_dir(args.vault_dir, force=True, sync_pgvector=False)
    print(f"[vault] loaded {load_st['notes']} notes, {load_st['menus']} menus, {load_st['errors']} errors")

    with mirror._lock:
        notes_copy = dict(mirror._notes_by_slug)
    sync_res = pg.sync_vault(notes_copy, segment_thai_text, force=args.force)
    print("[pgvector sync]", json.dumps(sync_res, ensure_ascii=False))
    if not sync_res.get("ok"):
        return 1

    print("[pgvector stats]", json.dumps(pg.stats(), ensure_ascii=False))

    if args.benchmark:
        bm = run_benchmark(mirror)
        summary = {k: v for k, v in bm.items() if k != "details"}
        print("[benchmark]", json.dumps(summary, ensure_ascii=False, indent=2))
        if bm["failures"]:
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
