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

# 26 comprehensive Thai call-center queries spanning modules 1xxx–9xxx + spoken paraphrases + 4 OOV abstain traps
_BENCHMARK_CASES: list[tuple[str, str, tuple[str, ...]]] = [
    # (question, expected_mode: "hit" | "abstain", expected_slug_or_keyword_any)
    ("โปรแกรมมีโปรโมชั่นกี่แบบครับ", "hit", ("howto-pos-hardware-display-promo", "10 รูปแบบ", "2015")),
    ("ซื้อสองแถมหนึ่งตั้งค่ายังไง", "hit", ("howto-pos-hardware-display-promo", "howto-price-center-discount-formula", "โปรโมชัน")),
    ("บาร์โค้ดใช้ได้กี่หลักครับ", "hit", ("howto-stock-units-barcodes", "EAN-13", "บาร์โค้ด")),
    ("โปรแกรมคิดต้นทุนแบบเข้าทีหลังออกก่อนได้ไหม", "hit", ("term-cost-method", "faq-stock-cost", "LIFO", "ถัวเฉลี่ย")),
    ("พักบิลหน้าร้านกับปิดกะทำยังไง", "hit", ("howto-pos-cashier-shift", "พักบิล", "ปิดกะ")),
    ("สต็อกติดลบห้ามขายติดลบตั้งค่าตรงไหน", "hit", ("ts-stock-negative-control", "สต็อกติดลบ", "1004")),
    ("ขึ้นแจ้งเตือนงวดบัญชีปิดแล้วแก้ยังไง", "hit", ("ts-common-period-closed", "howto-gl-period-open-close", "ปิดงวด")),
    ("ยอดสต็อกไม่ตรงกับของจริงคำนวณใหม่ตรงไหน", "hit", ("ts-stock-cost-mismatch-rebuild", "คำนวณยอดสต็อกใหม่")),
    ("เปิดบิลขายเชื่อออกใบกำกับภาษีทำเมนูไหน", "hit", ("howto-sale-invoice", "4001", "ขาย")),
    ("รับชำระหนี้ลูกหนี้ตัดบิลยังไง", "hit", ("howto-ar-receipt-fifo", "รับชำระหนี้", "ลูกหนี้")),
    ("ทำใบลดหนี้ลูกค้าคืนสินค้ากดเมนูอะไร", "hit", ("howto-sale-return", "howto-credit-note", "ใบลดหนี้", "ลดหนี้")),
    ("บันทึกซื้อเชื่อเจ้าหนี้เข้าเมนูไหน", "hit", ("howto-buy-invoice", "3001", "ซื้อ")),
    ("หัก ณ ที่จ่าย ภ.ง.ด. 3 กับ 53 ออกหนังสือรับรองตรงไหน", "hit", ("wht", "ภ.ง.ด.", "หัก ณ ที่จ่าย")),
    ("ยื่นภาษีมูลค่าเพิ่ม ภ.พ.30 ภาษีซื้อภาษีขายดูรายงานไหน", "hit", ("howto-tax-vat-input-output", "ภ.พ.30", "ภาษีซื้อ")),
    ("ลงทะเบียนเช็ครับแล้วเช็คเด้งจัดการยังไง", "hit", ("howto-cheque-received", "เช็คเด้ง", "เช็ครับ")),
    ("เงินมัดจำกับเงินล่วงหน้าต่างกันยังไง", "hit", ("faq-deposit-vs-advance", "เงินมัดจำ", "ล่วงหน้า")),
    ("ตั้งค่าแยกสาขากับแผนกทำยังไง", "hit", ("howto-setup-branch-dept", "สาขา", "แผนก")),
    ("สร้างบริษัทใหม่หลายนิติบุคคลแยกฐานข้อมูลได้ไหม", "hit", ("นิติบุคคล", "บริษัท", "หลายนิติบุคคล")),
    ("คิดค่าเสื่อมราคาสินทรัพย์ถาวรรายเดือนทำยังไง", "hit", ("สินทรัพย์", "ค่าเสื่อม", "8001")),
    ("ดึงงบทดลองงบกำไรขาดทุนงบดุลดูตรงไหน", "hit", ("งบทดลอง", "งบกำไรขาดทุน", "งบดุล", "9001")),
    ("ขายของบนช้อปปี้ลาซาด้าติ๊กต็อกออกใบกำกับอย่างย่อได้ไหม", "hit", ("shopee", "lazada", "อย่างย่อ", "marketplace")),
    ("มีระบบเฝ้าระวังทุจริตพนักงานหน้าร้านไหม", "hit", ("ทุจริต", "เฝ้าระวัง", "workmonitor")),
    ("โปรแกรมมีกี่แพ็กเกจราคาเท่าไหร่ครับ", "hit", ("sales-pricing-packages", "แพ็กเกจ", "ราคา")),
    ("หาเมนูไม่เจอเมนูหายไปทำยังไงดี", "hit", ("ts-menu-missing-switchboard", "Switchboard", "เมนูหาย")),
    ("คีย์ลัดปุ่มลัดที่ใช้บ่อยมีอะไรบ้าง", "hit", ("shortcut", "คีย์ลัด", "F")),
    ("รองรับธุรกิจร้านยางรถยนต์หรืออู่ซ่อมรถไหม", "hit", ("autocare", "ยาง", "อู่ซ่อมรถ")),
    # OOV / Abstain traps — MUST abstain cleanly without fabricating facts
    ("เมนู 9999 ใช้ทำอะไรครับ", "abstain", ()),
    ("ราคาทองคำแท่งวันนี้บาทละเท่าไหร่ครับ", "abstain", ()),
    ("อากาศที่เชียงใหม่พรุ่งนี้ฝนจะตกไหม", "abstain", ()),
    ("วิธีปลูกทุเรียนหมอนทองให้ได้ผลดก", "abstain", ()),
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

        if mode == "hit":
            hit_total += 1
            ans_low = ans.lower()
            matched = (not is_abstain) and (not has_media) and any(k.lower() in ans_low for k in expected)
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
