"use client";

import { useCallback, useEffect, useState } from "react";
import {
  BarChart3,
  BookOpenCheck,
  Bot,
  Brain,
  CheckCircle2,
  Clock3,
  Coins,
  Flame,
  HardDrive,
  HeartPulse,
  Mic,
  Plus,
  RefreshCw,
  Smile,
  Sparkles,
  Tag,
  Trash2,
  TrendingUp,
} from "lucide-react";
import { formatSeconds } from "@/components/LineCard";

interface AnalyticsData {
  ok: boolean;
  generatedAt: string;
  summary: {
    totalCalls: number;
    todayCalls: number;
    avgDurationSec: number;
    recordedCalls: number;
    analyzedCalls: number;
    avgCsat: number | null;
    avgAiQuality: number | null;
    resolvedRatePct: number | null;
    highIntentLeads: number;
    totalCustomers: number;
    openTickets: number;
    taughtFaqs: number;
  };
  dailyTrend: { date: string; calls: number; answered: number; highIntent: number }[];
  sentiments: { positive: number; neutral: number; negative: number; urgent: number };
  topTopics: { topic: string; count: number }[];
  topModules: { name: string; count: number }[];
  cache: {
    tts?: { files?: number; size_mb?: number; hits?: number; misses?: number; chars_saved?: number; enabled?: boolean };
    qa?: { entries?: number; taught_entries?: number; hits?: number; misses?: number };
  } | null;
}

interface TaughtEntry {
  key: string;
  question: string;
  answer: string;
  lang: string;
  mode: string;
  taught: boolean;
  hits: number;
  saved_at: number;
}

function formatDayShort(isoDate: string): string {
  try {
    const d = new Date(`${isoDate}T00:00:00`);
    return d.toLocaleDateString("th-TH", { day: "numeric", month: "short" });
  } catch {
    return isoDate.slice(5);
  }
}

export function AnalyticsWorkspace({
  readOnly = false,
  syncVersion = 0,
  onNotify,
}: {
  readOnly?: boolean;
  syncVersion?: number;
  onNotify?: (msg: string) => void;
}) {
  const [data, setData] = useState<AnalyticsData | null>(null);
  const [entries, setEntries] = useState<TaughtEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [qInput, setQInput] = useState("");
  const [aInput, setAInput] = useState("");
  const [promoteToBrain, setPromoteToBrain] = useState(true);
  const [savingTeach, setSavingTeach] = useState(false);
  const [promotingKey, setPromotingKey] = useState<string | null>(null);

  const loadAll = useCallback(async (manual = false) => {
    if (manual) setRefreshing(true);
    try {
      const [aRes, tRes] = await Promise.all([
        fetch("/api/analytics", { cache: "no-store" }),
        fetch("/api/teach", { cache: "no-store" }),
      ]);
      if (aRes.ok) {
        const aj = (await aRes.json()) as AnalyticsData;
        setData(aj);
      }
      if (tRes.ok) {
        const tj = (await tRes.json()) as { entries?: TaughtEntry[] };
        setEntries(Array.isArray(tj.entries) ? tj.entries : []);
      }
      if (manual) {
        onNotify?.(aRes.ok ? "อัปเดตข้อมูลสถิติล่าสุดแล้ว" : "ไม่สามารถโหลดข้อมูลสถิติล่าสุดได้");
      }
    } catch {
      if (manual) onNotify?.("ไม่สามารถเชื่อมต่อเซิร์ฟเวอร์เพื่ออัปเดตข้อมูลได้");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [onNotify]);

  useEffect(() => {
    void loadAll(false);
    const t = setInterval(() => void loadAll(false), 15000);
    return () => clearInterval(t);
  }, [loadAll]);

  useEffect(() => {
    if (syncVersion > 0) {
      void loadAll(false);
    }
  }, [syncVersion, loadAll]);

  const handleAddTeach = async (e: React.FormEvent) => {
    e.preventDefault();
    if (readOnly) return;
    const question = qInput.trim();
    const answer = aInput.trim();
    if (question.length < 3 || answer.length < 3) {
      onNotify?.("กรุณากรอกคำถามและคำตอบอย่างน้อย 3 ตัวอักษร");
      return;
    }
    setSavingTeach(true);
    try {
      const res = await fetch("/api/teach", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, answer, lang: "th", promoteToBrain }),
      });
      const j = await res.json();
      if (!res.ok || !j.ok) {
        onNotify?.(j.error || "บันทึกคำตอบไม่สำเร็จ");
      } else {
        setQInput("");
        setAInput("");
        onNotify?.(
          j.promoted?.ok
            ? `สอน AI + บันทึกถาวรเข้าสมอง BC (${j.promoted.slug}) และกำลังซิงก์ pgvector แล้ว!`
            : "สอนคำตอบให้ J.A.R.V.I.S เรียบร้อย รอบหน้าตอบได้ทันที!"
        );
        void loadAll(false);
      }
    } catch {
      onNotify?.("ไม่สามารถเชื่อมต่อเซิร์ฟเวอร์เพื่อบันทึกคำตอบได้");
    } finally {
      setSavingTeach(false);
    }
  };

  const handlePromoteEntry = async (item: TaughtEntry) => {
    if (readOnly) return;
    setPromotingKey(item.key);
    try {
      const res = await fetch("/api/brain/promote-teach", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: item.question, answer: item.answer }),
      });
      const j = (await res.json().catch(() => ({}))) as { ok?: boolean; slug?: string; error?: string };
      if (res.ok && j?.ok) {
        onNotify?.(`ย้ายคำตอบเข้าสมอง BC ถาวรแล้ว (${j.slug}) และกำลังซิงก์ pgvector`);
      } else {
        onNotify?.(j?.error || "ย้ายเข้าสมองไม่สำเร็จ");
      }
    } catch {
      onNotify?.("ไม่สามารถเชื่อมต่อเซิร์ฟเวอร์เพื่อย้ายคำตอบได้");
    } finally {
      setPromotingKey(null);
    }
  };

  const handleDeleteEntry = async (key: string) => {
    if (readOnly) return;
    try {
      const res = await fetch("/api/teach", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key }),
      });
      const j = (await res.json().catch(() => ({}))) as { ok?: boolean; error?: string };
      if (res.ok && j?.ok !== false) {
        onNotify?.("ลบคำตอบออกจากคลัง FAQ แล้ว");
        void loadAll(false);
      } else {
        onNotify?.(j?.error || "ลบคำตอบไม่สำเร็จ");
      }
    } catch {
      onNotify?.("ไม่สามารถเชื่อมต่อเซิร์ฟเวอร์เพื่อลบคำตอบได้");
    }
  };

  const summary = data?.summary;
  const maxDayCalls = Math.max(1, ...(data?.dailyTrend || []).map((d) => d.calls));
  const totalSentiments = data
    ? data.sentiments.positive + data.sentiments.neutral + data.sentiments.negative + data.sentiments.urgent
    : 0;
  const ttsCache = data?.cache?.tts;
  const qaCache = data?.cache?.qa;

  return (
    <div className="space-y-5">
      {/* Top Bar */}
      <div className="cc-panel flex flex-wrap items-center justify-between gap-3 px-5 py-3.5">
        <div className="flex items-center gap-3">
          <span className="cc-chip tone-live h-9 w-9 justify-center rounded-xl px-0">
            <BarChart3 className="h-4 w-4" />
          </span>
          <div>
            <h2 className="text-base font-bold leading-none cc-text">แดชบอร์ดสถิติ Call Center &amp; ความคุ้มค่า Cache ROI</h2>
            <p className="mt-1 text-xs leading-none cc-muted">
              ติดตามปริมาณสาย คะแนน CSAT โอกาสปิดการขาย ความประหยัดเครดิต และสอนคำตอบให้ J.A.R.V.I.S
            </p>
          </div>
        </div>
        <button type="button" disabled={refreshing} onClick={() => void loadAll(true)} className="cc-btn h-9">
          <RefreshCw className={`h-3.5 w-3.5 ${loading || refreshing ? "animate-spin" : ""}`} />
          <span>{refreshing ? "กำลังอัปเดต..." : "อัปเดตข้อมูล"}</span>
        </button>
      </div>

      {/* KPI Row */}
      <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-2 lg:grid-cols-4">
        <div className="cc-panel p-4">
          <div className="inline-flex items-center gap-1.5 text-xs font-medium cc-muted">
            <TrendingUp className="h-3.5 w-3.5" />
            <span>สายเข้าทั้งหมด / วันนี้</span>
          </div>
          <div className="mt-2.5 flex items-baseline gap-2 font-mono tabular-nums">
            <span className="text-2xl font-bold cc-text">{summary?.totalCalls ?? 0}</span>
            <span className="cc-chip tone-in">วันนี้ +{summary?.todayCalls ?? 0}</span>
          </div>
          <div className="mt-2 flex items-center gap-3 text-[11px] cc-dim">
            <span className="inline-flex items-center gap-1">
              <Clock3 className="h-3 w-3" />
              <span>เฉลี่ย {formatSeconds(summary?.avgDurationSec ?? 0)}</span>
            </span>
            <span className="inline-flex items-center gap-1">
              <Mic className="h-3 w-3" />
              <span>อัดเสียง {summary?.recordedCalls ?? 0} สาย</span>
            </span>
          </div>
        </div>

        <div className="cc-panel p-4">
          <div className="inline-flex items-center gap-1.5 text-xs font-medium cc-muted">
            <Smile className="h-3.5 w-3.5" />
            <span>CSAT เฉลี่ย / คุณภาพ AI</span>
          </div>
          <div className="mt-2.5 flex items-baseline gap-2 font-mono tabular-nums">
            <span className="text-2xl font-bold cc-text">
              {summary?.avgCsat !== null && summary?.avgCsat !== undefined ? `${summary.avgCsat}/10` : "-"}
            </span>
            <span className="cc-chip tone-ai">
              <Bot className="h-3 w-3" />
              <span>AI {summary?.avgAiQuality !== null && summary?.avgAiQuality !== undefined ? `${summary.avgAiQuality}/10` : "-"}</span>
            </span>
          </div>
          <div className="mt-2 text-[11px] cc-dim">
            วิเคราะห์แล้ว {summary?.analyzedCalls ?? 0} สาย • ปิดจบในสาย {summary?.resolvedRatePct !== null && summary?.resolvedRatePct !== undefined ? `${summary.resolvedRatePct}%` : "-"}
          </div>
        </div>

        <div className="cc-panel p-4">
          <div className="inline-flex items-center gap-1.5 text-xs font-medium cc-muted">
            <Flame className="h-3.5 w-3.5" />
            <span>โอกาสปิดการขาย (Hot Leads)</span>
          </div>
          <div className="mt-2.5 flex items-baseline gap-2 font-mono tabular-nums">
            <span className="text-2xl font-bold cc-text">{summary?.highIntentLeads ?? 0}</span>
            <span className="cc-chip tone-held">ลูกค้า CRM {summary?.totalCustomers ?? 0}</span>
          </div>
          <div className="mt-2 text-[11px] cc-dim">
            เคสติดตามที่ยังเปิดอยู่ {summary?.openTickets ?? 0} รายการ
          </div>
        </div>

        <div className="cc-panel p-4">
          <div className="inline-flex items-center gap-1.5 text-xs font-medium cc-muted">
            <Coins className="h-3.5 w-3.5" />
            <span>ประหยัดเครดิต (Cache ROI)</span>
          </div>
          <div className="mt-2.5 flex items-baseline gap-2 font-mono tabular-nums">
            <span className="text-2xl font-bold cc-text">{(ttsCache?.chars_saved ?? 0).toLocaleString("th-TH")}</span>
            <span className="text-xs cc-muted">ตัวอักษร TTS</span>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] cc-dim">
            <span className="inline-flex items-center gap-1">
              <HardDrive className="h-3 w-3" />
              <span>{ttsCache?.files ?? 0} ไฟล์ ({ttsCache?.size_mb ?? 0} MB)</span>
            </span>
            <span>•</span>
            <span>QA Hit {qaCache?.hits ?? 0}</span>
          </div>
        </div>
      </div>

      {/* Charts Row: 7-Day Trend + Sentiment Breakdown */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        <section className="cc-panel p-5 lg:col-span-7">
          <div className="mb-4 flex items-center justify-between gap-2">
            <h3 className="inline-flex items-center gap-1.5 text-sm font-semibold cc-text">
              <BarChart3 className="h-4 w-4" />
              <span>ปริมาณสายเข้า 7 วันย้อนหลัง</span>
            </h3>
            <div className="flex items-center gap-3 text-[11px] cc-muted">
              <span className="inline-flex items-center gap-1.5">
                <span className="h-2.5 w-2.5 rounded-sm bg-sky-500/80" />
                <span>สายทั้งหมด</span>
              </span>
              <span className="inline-flex items-center gap-1.5">
                <span className="h-2.5 w-2.5 rounded-sm bg-emerald-500/90" />
                <span>Hot Lead</span>
              </span>
            </div>
          </div>

          <div className="grid h-44 grid-cols-7 items-end gap-2.5 pt-4">
            {(data?.dailyTrend || []).map((day) => {
              const totalPct = Math.max(4, Math.round((day.calls / maxDayCalls) * 100));
              const leadPct = day.highIntent > 0 ? Math.max(8, Math.round((day.highIntent / maxDayCalls) * 100)) : 0;
              return (
                <div key={day.date} className="flex h-full flex-col items-center justify-end gap-1.5">
                  <span className="font-mono text-[11px] font-semibold tabular-nums cc-muted">{day.calls}</span>
                  <div className="relative flex h-28 w-full max-w-[44px] items-end justify-center gap-1 rounded-lg bg-[var(--cc-inset)] p-1">
                    <div
                      className="w-full rounded-md bg-sky-500/80 transition-all duration-300"
                      style={{ height: `${day.calls > 0 ? totalPct : 0}%` }}
                      title={`${formatDayShort(day.date)}: ${day.calls} สาย`}
                    />
                    {day.highIntent > 0 && (
                      <div
                        className="w-2.5 shrink-0 rounded-md bg-emerald-500 transition-all duration-300"
                        style={{ height: `${leadPct}%` }}
                        title={`Hot Lead: ${day.highIntent} สาย`}
                      />
                    )}
                  </div>
                  <span className="text-[11px] leading-none cc-dim">{formatDayShort(day.date)}</span>
                </div>
              );
            })}
          </div>
        </section>

        <section className="cc-panel p-5 lg:col-span-5">
          <h3 className="mb-4 inline-flex items-center gap-1.5 text-sm font-semibold cc-text">
            <HeartPulse className="h-4 w-4" />
            <span>สัดส่วนอารมณ์ลูกค้าจาก AI QA</span>
          </h3>
          <div className="space-y-3">
            {[
              { key: "positive", label: "เชิงบวก / สบายใจ", color: "bg-emerald-500", count: data?.sentiments.positive ?? 0 },
              { key: "neutral", label: "สอบถามทั่วไป", color: "bg-sky-500", count: data?.sentiments.neutral ?? 0 },
              { key: "negative", label: "เชิงลบ / กังวล", color: "bg-amber-500", count: data?.sentiments.negative ?? 0 },
              { key: "urgent", label: "เร่งด่วน / ต้องรีบดูแล", color: "bg-rose-500", count: data?.sentiments.urgent ?? 0 },
            ].map((row) => {
              const pct = totalSentiments > 0 ? Math.round((row.count / totalSentiments) * 100) : 0;
              return (
                <div key={row.key}>
                  <div className="mb-1 flex items-center justify-between text-xs">
                    <span className="cc-text">{row.label}</span>
                    <span className="font-mono tabular-nums cc-muted">
                      {row.count} สาย ({pct}%)
                    </span>
                  </div>
                  <div className="h-2 w-full overflow-hidden rounded-full bg-[var(--cc-inset)]">
                    <div className={`h-full rounded-full ${row.color} transition-all duration-300`} style={{ width: `${pct}%` }} />
                  </div>
                </div>
              );
            })}
          </div>

          {/* Top Modules */}
          <div className="mt-5 border-t pt-4" style={{ borderColor: "var(--cc-border)" }}>
            <div className="mb-2 inline-flex items-center gap-1.5 text-xs font-semibold cc-muted">
              <Sparkles className="h-3.5 w-3.5" />
              <span>โมดูล BC Ai Account ที่ลูกค้าสนใจมากที่สุด</span>
            </div>
            {(data?.topModules || []).length === 0 ? (
              <p className="text-xs cc-dim">ข้อมูลจะสะสมอัตโนมัติหลังวางสายและวิเคราะห์ QA</p>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {(data?.topModules || []).map((m) => (
                  <span key={m.name} className="cc-chip tone-ai">
                    <span>{m.name}</span>
                    <span className="font-mono tabular-nums opacity-80">×{m.count}</span>
                  </span>
                ))}
              </div>
            )}
          </div>
        </section>
      </div>

      {/* Top Topics + 1-Click Teach AI Manager */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        <section className="cc-panel p-5 lg:col-span-5">
          <h3 className="mb-3 inline-flex items-center gap-1.5 text-sm font-semibold cc-text">
            <Tag className="h-4 w-4" />
            <span>หัวข้อที่ลูกค้าถามบ่อย (Top Topics)</span>
          </h3>
          {(data?.topTopics || []).length === 0 ? (
            <div className="cc-empty px-4 py-6 text-center text-xs">ยังไม่มีข้อมูลหัวข้อที่วิเคราะห์</div>
          ) : (
            <div className="space-y-2">
              {(data?.topTopics || []).map((t, idx) => (
                <div key={t.topic} className="cc-inset flex items-center justify-between gap-2 px-3 py-2 text-xs">
                  <div className="flex min-w-0 items-center gap-2">
                    <span className="cc-chip tone-idle font-mono tabular-nums">#{idx + 1}</span>
                    <span className="truncate font-medium cc-text">{t.topic}</span>
                  </div>
                  <span className="cc-chip tone-live shrink-0 font-mono tabular-nums">{t.count} สาย</span>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="cc-panel p-5 lg:col-span-7">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h3 className="inline-flex items-center gap-1.5 text-sm font-semibold cc-text">
              <BookOpenCheck className="h-4 w-4" />
              <span>คลังคำตอบที่สอน J.A.R.V.I.S เพิ่ม (1-Click Teach &amp; QA Cache)</span>
            </h3>
            <span className="cc-chip tone-ai font-mono tabular-nums">
              สอนเอง {qaCache?.taught_entries ?? 0} • ทั้งหมด {entries.length}
            </span>
          </div>

          {!readOnly && (
            <form onSubmit={handleAddTeach} className="cc-inset mb-4 space-y-2.5 p-3.5">
              <div className="text-xs font-semibold cc-text">เพิ่มคำถาม–คำตอบใหม่ให้ J.A.R.V.I.S ตอบได้ทันที (0ms DeepSeek Token)</div>
              <input
                type="text"
                value={qInput}
                onChange={(e) => setQInput(e.target.value)}
                placeholder="เช่น ลูกค้าไปใช้บริการสาขาอื่นแล้วเชื่อมประวัติซ่อมข้ามสาขาได้ไหม"
                className="w-full rounded-lg border border-[var(--cc-border)] bg-[var(--cc-surface)] px-3 py-2 text-xs cc-text outline-none focus:border-sky-500"
              />
              <textarea
                rows={2}
                value={aInput}
                onChange={(e) => setAInput(e.target.value)}
                placeholder="คำตอบภาษาพูดสั้นๆ 1-2 ประโยคที่อยากให้ J.A.R.V.I.S พูดตอบลูกค้า..."
                className="w-full rounded-lg border border-[var(--cc-border)] bg-[var(--cc-surface)] px-3 py-2 text-xs cc-text outline-none focus:border-sky-500"
              />
              <div className="flex flex-wrap items-center justify-between gap-2">
                <label className="inline-flex cursor-pointer items-center gap-1.5 text-[11px] cc-muted">
                  <input
                    type="checkbox"
                    checked={promoteToBrain}
                    onChange={(e) => setPromoteToBrain(e.target.checked)}
                    className="h-3.5 w-3.5 rounded"
                  />
                  <span>บันทึกถาวรเข้าสมอง BC (pgvector)</span>
                </label>
                <button type="submit" disabled={savingTeach} className="cc-btn cc-btn--tone tone-in h-8 px-3 text-xs">
                  <Plus className="h-3.5 w-3.5" />
                  <span>{savingTeach ? "กำลังบันทึก..." : "บันทึกสอน AI ทันที"}</span>
                </button>
              </div>
            </form>
          )}

          {entries.length === 0 ? (
            <div className="cc-empty px-4 py-6 text-center text-xs">
              ยังไม่มีคำตอบที่สอนไว้ — กด “สอน AI เพิ่ม” จากหน้านี้ หรือจากบทสนทนาในลิ้นชัก QA ได้เลย
            </div>
          ) : (
            <div className="max-h-72 space-y-2 overflow-y-auto pr-1">
              {entries.map((item) => (
                <div key={item.key} className="cc-inset flex items-start justify-between gap-3 p-3 text-xs">
                  <div className="min-w-0 flex-1 space-y-1">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className={`cc-chip ${item.taught ? "tone-in" : "tone-idle"}`}>
                        {item.taught ? <CheckCircle2 className="h-3 w-3" /> : <Sparkles className="h-3 w-3" />}
                        <span>{item.taught ? "แอดมินสอน" : "Auto Cache"}</span>
                      </span>
                      <span className="font-semibold cc-text">{item.question}</span>
                    </div>
                    <p className="leading-relaxed cc-muted">{item.answer}</p>
                    <div className="text-[11px] cc-dim">ใช้งานแล้ว {item.hits} ครั้ง</div>
                  </div>
                  {!readOnly && (
                    <div className="flex shrink-0 items-center gap-1.5">
                      <button
                        type="button"
                        disabled={promotingKey === item.key}
                        onClick={() => void handlePromoteEntry(item)}
                        className="cc-btn cc-btn--tone tone-ai h-7 px-2 text-[11px]"
                        title="ย้ายคำตอบนี้เป็นโน้ตถาวรเข้าสมอง BC + pgvector"
                      >
                        <Brain className="h-3 w-3" />
                        <span>{promotingKey === item.key ? "กำลังย้าย..." : "เข้าสมอง"}</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleDeleteEntry(item.key)}
                        className="cc-btn h-7 w-7 px-0 tone-danger"
                        title="ลบคำตอบนี้"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
