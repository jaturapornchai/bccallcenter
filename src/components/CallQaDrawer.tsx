"use client";

import { useEffect } from "react";
import type { CSSProperties, ReactNode } from "react";
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  ClipboardList,
  Clock3,
  Headset,
  HeartPulse,
  Lightbulb,
  ListChecks,
  MessageSquareText,
  RefreshCw,
  Smile,
  Sparkles,
  Tag,
  UserRound,
  X,
} from "lucide-react";
import type { CallAnalysis, CallLogEntry, CallerMessage } from "@/types/sip";
import { AudioEqualizer, SignalRadar, callStatusMeta, formatDateTime, formatPhone, formatSeconds } from "@/components/LineCard";

type Tone = "in" | "ai" | "live" | "held" | "idle" | "danger";

const SENTIMENT_META: Record<NonNullable<CallAnalysis["sentiment"]>, { label: string; tone: Tone }> = {
  positive: { label: "เชิงบวก", tone: "in" },
  neutral: { label: "เป็นกลาง", tone: "live" },
  negative: { label: "เชิงลบ", tone: "held" },
  urgent: { label: "เร่งด่วน", tone: "danger" },
};

const URGENCY_META: Record<NonNullable<CallAnalysis["urgency"]>, { label: string; tone: Tone }> = {
  low: { label: "ต่ำ", tone: "idle" },
  medium: { label: "ปานกลาง", tone: "held" },
  high: { label: "สูง", tone: "danger" },
};

const ROLE_META: Record<CallerMessage["role"], { label: string; tone: Tone }> = {
  caller: { label: "ผู้โทร", tone: "idle" },
  ai: { label: "Javis AI", tone: "ai" },
  agent: { label: "เจ้าหน้าที่", tone: "live" },
};

export function sentimentMeta(sentiment: CallAnalysis["sentiment"]): { label: string; tone: Tone } | null {
  return sentiment ? SENTIMENT_META[sentiment] ?? null : null;
}

export function scoreTone(score: number | undefined): Tone {
  if (score === undefined || Number.isNaN(score)) return "idle";
  if (score >= 8) return "in";
  if (score >= 5) return "held";
  return "danger";
}

export function formatScore(score: number | undefined): string {
  if (score === undefined || Number.isNaN(score)) return "-";
  return Number.isInteger(score) ? String(score) : score.toFixed(1);
}

function ScoreGauge({ label, score, icon }: { label: string; score: number | undefined; icon: ReactNode }) {
  const pct = score === undefined ? 0 : Math.min(100, Math.max(0, score * 10));
  return (
    <div className={`cc-inset tone-${scoreTone(score)} flex items-center gap-3.5 p-3.5`}>
      <span className="cc-gauge" style={{ "--pct": pct } as CSSProperties}>
        <span className="inline-flex items-baseline gap-0.5 font-mono leading-none tabular-nums">
          <span className="text-lg font-bold cc-ink">{formatScore(score)}</span>
          <span className="text-[10px] cc-dim">/10</span>
        </span>
      </span>
      <div className="min-w-0">
        <div className="inline-flex items-center gap-1.5 text-xs font-semibold leading-none cc-ink">
          {icon}
          <span>{label}</span>
        </div>
        <p className="mt-1.5 text-[11px] leading-snug cc-dim">
          {score === undefined ? "ยังไม่มีคะแนน" : score >= 8 ? "ดีมาก" : score >= 5 ? "พอใช้ ควรติดตาม" : "ต่ำ ต้องปรับปรุง"}
        </p>
      </div>
    </div>
  );
}

function Section({ icon, title, children }: { icon: ReactNode; title: string; children: ReactNode }) {
  return (
    <section className="cc-inset p-3.5">
      <h3 className="mb-2.5 inline-flex items-center gap-1.5 text-xs font-semibold leading-none cc-muted">
        {icon}
        <span>{title}</span>
      </h3>
      {children}
    </section>
  );
}

function AnalysisBody({ analysis }: { analysis: CallAnalysis }) {
  const sentiment = sentimentMeta(analysis.sentiment);
  const urgency = analysis.urgency ? URGENCY_META[analysis.urgency] : null;
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <ScoreGauge label="CSAT ความพึงพอใจ" score={analysis.csatScore} icon={<Smile className="h-3.5 w-3.5" />} />
        <ScoreGauge label="คุณภาพการตอบของ AI" score={analysis.aiQualityScore} icon={<Bot className="h-3.5 w-3.5" />} />
      </div>

      <Section icon={<HeartPulse className="h-3.5 w-3.5" />} title="อารมณ์และความรู้สึกลูกค้า">
        <div className="flex flex-wrap items-center gap-2">
          {sentiment ? (
            <span className={`cc-chip tone-${sentiment.tone}`}>
              <span className={`cc-led ${analysis.sentiment === "urgent" ? "cc-led--blink" : ""}`} />
              <span>{sentiment.label}</span>
            </span>
          ) : (
            <span className="cc-chip tone-idle">ไม่ระบุ</span>
          )}
          {analysis.emotionLabel && <span className="cc-chip tone-idle">{analysis.emotionLabel}</span>}
        </div>
        {analysis.emotionTrend && <p className="mt-2 text-xs leading-relaxed cc-text">แนวโน้ม: {analysis.emotionTrend}</p>}
      </Section>

      <Section icon={<Tag className="h-3.5 w-3.5" />} title="หัวข้อและสรุปการสนทนา">
        {analysis.topic && <div className="text-sm font-semibold leading-snug cc-text">{analysis.topic}</div>}
        <p className="mt-1.5 whitespace-pre-line text-xs leading-relaxed cc-muted">{analysis.summary || "ไม่มีสรุป"}</p>
      </Section>

      <Section icon={<ListChecks className="h-3.5 w-3.5" />} title="สถานะการแก้ไขปัญหา">
        <span className={`cc-chip ${analysis.resolved ? "tone-in" : "tone-held"}`}>
          {analysis.resolved ? <CheckCircle2 className="h-3 w-3" /> : <AlertTriangle className="h-3 w-3" />}
          <span>{analysis.resolved ? "แก้ไขเรียบร้อย" : "ยังไม่ได้แก้ไข"}</span>
        </span>
        {analysis.resolutionSteps && (
          <p className="mt-2 whitespace-pre-line text-xs leading-relaxed cc-text">{analysis.resolutionSteps}</p>
        )}
      </Section>

      <Section icon={<ClipboardList className="h-3.5 w-3.5" />} title="การติดตามผลและความเร่งด่วน">
        <div className="flex flex-wrap items-center gap-2">
          <span className={`cc-chip ${analysis.needsHumanFollowUp ? "tone-danger" : "tone-in"}`}>
            <UserRound className="h-3 w-3" />
            <span>{analysis.needsHumanFollowUp ? "ต้องให้เจ้าหน้าที่ติดตาม" : "ไม่ต้องติดตาม"}</span>
          </span>
          {urgency && (
            <span className={`cc-chip tone-${urgency.tone}`}>
              <span className={`cc-led ${analysis.urgency === "high" ? "cc-led--blink" : ""}`} />
              <span>ความเร่งด่วน: {urgency.label}</span>
            </span>
          )}
        </div>
        {analysis.followUpAction && (
          <p className="mt-2 whitespace-pre-line text-xs leading-relaxed cc-text">{analysis.followUpAction}</p>
        )}
      </Section>

      {analysis.aiCoachingTip && (
        <div className="cc-quote tone-ai p-3.5">
          <div className="mb-1.5 inline-flex items-center gap-1.5 text-xs font-semibold leading-none">
            <Lightbulb className="h-3.5 w-3.5" />
            <span>คำแนะนำโค้ชชิ่ง AI</span>
          </div>
          <p className="whitespace-pre-line text-xs leading-relaxed">{analysis.aiCoachingTip}</p>
        </div>
      )}

      <p className="inline-flex flex-wrap items-center gap-1.5 text-[11px] leading-none cc-dim">
        <Sparkles className="h-3 w-3" />
        <span>วิเคราะห์โดย</span>
        <span className="font-mono">{analysis.model}</span>
        <span>•</span>
        <span className="tabular-nums">{formatDateTime(analysis.analyzedAt)}</span>
      </p>
    </div>
  );
}

function AnalysisState({ log }: { log: CallLogEntry }) {
  if (log.analysisStatus === "analyzing") {
    return (
      <div className="cc-inset tone-ai flex items-center gap-4 p-4">
        <SignalRadar tone="ai" mode="alert" className="h-14 w-14" coreClassName="h-8 w-8">
          <Sparkles className="h-4 w-4" />
        </SignalRadar>
        <div className="min-w-0">
          <div className="inline-flex items-center gap-2 text-sm font-semibold leading-none cc-ink">
            <span>DeepSeek กำลังวิเคราะห์คุณภาพสาย</span>
            <AudioEqualizer bars={6} className="h-3.5" />
          </div>
          <p className="mt-1.5 text-xs cc-dim">ผลจะแสดงอัตโนมัติเมื่อวิเคราะห์เสร็จ</p>
        </div>
      </div>
    );
  }
  if (log.analysis?.error) {
    return (
      <div className="cc-banner tone-danger px-4 py-3">
        <AlertTriangle className="h-4 w-4 shrink-0" />
        <span>วิเคราะห์ไม่สำเร็จ: {log.analysis.error}</span>
      </div>
    );
  }
  if (log.analysisStatus === "error") {
    return (
      <div className="cc-banner tone-danger px-4 py-3">
        <AlertTriangle className="h-4 w-4 shrink-0" />
        <span>วิเคราะห์ไม่สำเร็จ กรุณากด “วิเคราะห์ใหม่”</span>
      </div>
    );
  }
  if (log.analysis) return <AnalysisBody analysis={log.analysis} />;
  return (
    <div className="cc-empty px-4 py-6 text-center text-xs">ยังไม่มีผลวิเคราะห์ QA ของสายนี้ — กด “วิเคราะห์ใหม่ (DeepSeek)” เพื่อเริ่มวิเคราะห์</div>
  );
}

function Transcript({ messages }: { messages: CallerMessage[] }) {
  if (messages.length === 0) {
    return <div className="cc-empty px-4 py-6 text-center text-xs">ไม่มีข้อความถอดเสียงในสายนี้</div>;
  }
  return (
    <div className="space-y-2">
      {messages.map((m, idx) => {
        const meta = ROLE_META[m.role] ?? ROLE_META.caller;
        const isCaller = m.role === "caller";
        return (
          <div key={`${m.at}-${idx}`} className={`flex ${isCaller ? "justify-start" : "justify-end"}`}>
            <div className={`max-w-[85%] px-3 py-2 text-xs leading-relaxed ${isCaller ? "cc-bubble" : `cc-quote tone-${meta.tone}`}`}>
              <div className="mb-1 inline-flex items-center gap-1.5 text-[11px] font-semibold leading-none">
                {m.role === "ai" ? <Bot className="h-3 w-3" /> : m.role === "agent" ? <Headset className="h-3 w-3" /> : <UserRound className="h-3 w-3" />}
                <span>{meta.label}</span>
                <span className="font-normal tabular-nums opacity-70">
                  {new Date(m.at).toLocaleTimeString("th-TH", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                </span>
              </div>
              <div className="whitespace-pre-line">{m.text}</div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function CallQaDrawer({
  log,
  onClose,
  onReanalyze,
}: {
  log: CallLogEntry;
  onClose: () => void;
  onReanalyze: (phone: string, sessionId: string) => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const status = callStatusMeta(log.status);
  const analyzing = log.analysisStatus === "analyzing";

  return (
    <div className="fixed inset-0 z-50" role="dialog" aria-modal="true" aria-label={`บันทึกสายและผลวิเคราะห์ QA ของ ${formatPhone(log.phone)}`}>
      <button type="button" aria-label="ปิด" onClick={onClose} className="cc-overlay absolute inset-0 cursor-default" />
      <aside className="cc-drawer absolute inset-y-0 right-0 flex w-full max-w-xl flex-col">
        <header className="flex items-start justify-between gap-3 border-b px-5 py-4" style={{ borderColor: "var(--cc-border)" }}>
          <div className="min-w-0">
            <div className="cc-label">บันทึกหลังสาย &amp; AI QA</div>
            <div className="mt-2 font-mono text-2xl font-bold leading-none tracking-wide tabular-nums cc-text">{formatPhone(log.phone)}</div>
            <div className="mt-2.5 flex flex-wrap items-center gap-2">
              <span className={`cc-chip tone-${status.tone}`}>
                <span className="cc-led" />
                <span>{status.label}</span>
              </span>
              <span className="cc-chip tone-idle">
                <Clock3 className="h-3 w-3" />
                <span className="tabular-nums">{formatDateTime(log.startedAt)}</span>
              </span>
              <span className="cc-chip tone-idle font-mono tabular-nums">คุย {formatSeconds(log.durationSec)}</span>
              <span className="cc-chip tone-held">
                <span>ลูกค้าโทรมาแล้ว</span>
                <span className="tabular-nums">{log.callerTotalCalls}</span>
                <span>ครั้ง</span>
              </span>
            </div>
          </div>
          <button type="button" onClick={onClose} className="cc-btn h-9 w-9 shrink-0 px-0" title="ปิด (Esc)">
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="cc-scroll flex-1 space-y-4 overflow-y-auto px-5 py-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="inline-flex items-center gap-1.5 text-sm font-semibold leading-none cc-text">
              <Sparkles className="h-4 w-4" />
              <span>ผลวิเคราะห์คุณภาพสาย (AI QA)</span>
            </h3>
            <button
              type="button"
              disabled={analyzing}
              onClick={() => onReanalyze(log.phone, log.sessionId)}
              className="cc-btn cc-btn--tone tone-ai h-9"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${analyzing ? "animate-spin" : ""}`} />
              <span>{analyzing ? "กำลังวิเคราะห์..." : "วิเคราะห์ใหม่ (DeepSeek)"}</span>
            </button>
          </div>

          <AnalysisState log={log} />

          <div>
            <h3 className="mb-2.5 inline-flex items-center gap-1.5 text-sm font-semibold leading-none cc-text">
              <MessageSquareText className="h-4 w-4" />
              <span>บทสนทนาทั้งหมด</span>
              <span className="cc-chip tone-idle tabular-nums">{log.messages.length} ข้อความ</span>
            </h3>
            <Transcript messages={log.messages} />
          </div>
        </div>
      </aside>
    </div>
  );
}
