"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import {
  AlertTriangle,
  BookOpenCheck,
  Bot,
  Check,
  CheckCircle2,
  ClipboardList,
  Clock3,
  Copy,
  Flame,
  Headset,
  HeartPulse,
  Lightbulb,
  ListChecks,
  Lock,
  MessageSquareText,
  Play,
  RefreshCw,
  Smile,
  Sparkles,
  Tag,
  UserRound,
  Volume2,
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
  ai: { label: "J.A.R.V.I.S", tone: "ai" },
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

const BUYING_META: Record<"high" | "medium" | "low", { label: string; tone: Tone }> = {
  high: { label: "สนใจซื้อสูง", tone: "in" },
  medium: { label: "กำลังพิจารณา", tone: "held" },
  low: { label: "สอบถามทั่วไป", tone: "idle" },
};

function buildDefaultFollowUpMessage(analysis: CallAnalysis): string {
  if (analysis.followUpMessage && analysis.followUpMessage.trim()) {
    return analysis.followUpMessage.trim();
  }
  const mods = (analysis.interestedModules || []).filter(Boolean);
  const lines = [
    "สวัสดีครับ จากทีมระบบ BC Call Center (BC Ai Account) ครับ",
    analysis.topic ? `ขอสรุปเรื่องที่เราคุยกันวันนี้เกี่ยวกับ "${analysis.topic}" นะครับ` : "ขอบคุณที่โทรมาพูดคุยกับเราวันนี้นะครับ",
    analysis.summary ? analysis.summary : "",
    mods.length > 0 ? `โมดูลที่แนะนำเพิ่มเติม: ${mods.join(", ")}` : "",
    "หากสนใจทดลองใช้งานหรืออยากสอบถามรายละเอียดเพิ่มเติม ทักกลับมาได้เลยนะครับ ยินดีดูแลเต็มที่ครับ",
  ].filter(Boolean);
  return lines.join("\n");
}

function AnalysisBody({ analysis }: { analysis: CallAnalysis }) {
  const [copied, setCopied] = useState(false);
  const sentiment = sentimentMeta(analysis.sentiment);
  const urgency = analysis.urgency ? URGENCY_META[analysis.urgency] : null;
  const buying = analysis.buyingIntent ? BUYING_META[analysis.buyingIntent] : null;
  const followUpText = useMemo(() => buildDefaultFollowUpMessage(analysis), [analysis]);

  const handleCopyFollowUp = async () => {
    let ok = false;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(followUpText);
        ok = true;
      }
    } catch {}
    if (!ok && typeof document !== "undefined") {
      try {
        const ta = document.createElement("textarea");
        ta.value = followUpText;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        ok = document.execCommand("copy");
        document.body.removeChild(ta);
      } catch {}
    }
    if (ok) {
      setCopied(true);
      setTimeout(() => setCopied(false), 2200);
    }
  };

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <ScoreGauge label="CSAT ความพึงพอใจ" score={analysis.csatScore} icon={<Smile className="h-3.5 w-3.5" />} />
        <ScoreGauge label="คุณภาพการตอบของ AI" score={analysis.aiQualityScore} icon={<Bot className="h-3.5 w-3.5" />} />
      </div>

      <Section icon={<HeartPulse className="h-3.5 w-3.5" />} title="อารมณ์ & โอกาสปิดการขาย">
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
          {buying && (
            <span className={`cc-chip tone-${buying.tone}`}>
              <Flame className="h-3 w-3" />
              <span>{buying.label}</span>
            </span>
          )}
        </div>
        {analysis.emotionTrend && <p className="mt-2 text-xs leading-relaxed cc-text">แนวโน้ม: {analysis.emotionTrend}</p>}
        {Array.isArray(analysis.interestedModules) && analysis.interestedModules.length > 0 && (
          <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
            <span className="text-[11px] cc-dim">โมดูลที่สนใจ:</span>
            {analysis.interestedModules.map((mod) => (
              <span key={mod} className="cc-chip tone-ai">
                {mod}
              </span>
            ))}
          </div>
        )}
      </Section>

      <Section icon={<Tag className="h-3.5 w-3.5" />} title="หัวข้อและสรุปการสนทนา">
        {analysis.topic && <div className="text-sm font-semibold leading-snug cc-text">{analysis.topic}</div>}
        <p className="mt-1.5 whitespace-pre-line text-xs leading-relaxed cc-muted">{analysis.summary || "ไม่มีสรุป"}</p>
      </Section>

      {/* Auto Follow-up Message Card (1-Click Copy, No external API needed) */}
      <Section icon={<MessageSquareText className="h-3.5 w-3.5" />} title="ข้อความสรุปหลังวางสาย (คัดลอกส่งลูกค้าได้ทันที)">
        <div className="rounded-lg border border-[var(--cc-border)] bg-[var(--cc-surface)] p-3 text-xs leading-relaxed whitespace-pre-line cc-text">
          {followUpText}
        </div>
        <div className="mt-2.5 flex justify-end">
          <button
            type="button"
            onClick={() => void handleCopyFollowUp()}
            className={`cc-btn cc-btn--tone h-8 px-3 text-xs ${copied ? "tone-in" : "tone-live"}`}
          >
            {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
            <span>{copied ? "คัดลอกข้อความแล้ว!" : "คัดลอกข้อความส่งลูกค้า"}</span>
          </button>
        </div>
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

function Transcript({
  messages,
  callStartedAt,
  hasAudio,
  currentAudioSec,
  readOnly,
  onSeekAudio,
  onTeachClick,
}: {
  messages: CallerMessage[];
  callStartedAt: string;
  hasAudio: boolean;
  currentAudioSec: number;
  readOnly: boolean;
  onSeekAudio: (sec: number) => void;
  onTeachClick: (question: string) => void;
}) {
  if (messages.length === 0) {
    return <div className="cc-empty px-4 py-6 text-center text-xs">ไม่มีข้อความถอดเสียงในสายนี้</div>;
  }
  const startMs = new Date(callStartedAt).getTime();
  const offsets = messages.map((m) => {
    const ms = new Date(m.at).getTime();
    return Number.isFinite(startMs) && Number.isFinite(ms) ? Math.max(0, Math.round((ms - startMs) / 1000)) : 0;
  });

  return (
    <div className="space-y-2">
      {messages.map((m, idx) => {
        const meta = ROLE_META[m.role] ?? ROLE_META.caller;
        const isCaller = m.role === "caller";
        const sec = offsets[idx];
        const nextSec = idx + 1 < offsets.length ? offsets[idx + 1] : sec + 8;
        const isActive = hasAudio && currentAudioSec > 0 && currentAudioSec >= sec && currentAudioSec < nextSec;
        return (
          <div key={`${m.at}-${idx}`} className={`flex ${isCaller ? "justify-start" : "justify-end"}`}>
            <div
              className={`max-w-[88%] px-3 py-2 text-xs leading-relaxed transition ${isCaller ? "cc-bubble" : `cc-quote tone-${meta.tone}`} ${isActive ? "ring-2 ring-sky-400/70" : ""}`}
            >
              <div className="mb-1 flex flex-wrap items-center justify-between gap-2 text-[11px] font-semibold leading-none">
                <span className="inline-flex items-center gap-1.5">
                  {m.role === "ai" ? <Bot className="h-3 w-3" /> : m.role === "agent" ? <Headset className="h-3 w-3" /> : <UserRound className="h-3 w-3" />}
                  <span>{meta.label}</span>
                  <span className="font-normal tabular-nums opacity-70">
                    {new Date(m.at).toLocaleTimeString("th-TH", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                  </span>
                </span>
                <span className="inline-flex items-center gap-1.5">
                  {hasAudio && (
                    <button
                      type="button"
                      onClick={() => onSeekAudio(sec)}
                      className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-mono tabular-nums opacity-80 hover:bg-black/10 hover:opacity-100"
                      title={`ข้ามเสียงไปที่ ${formatSeconds(sec)}`}
                    >
                      <Play className="h-2.5 w-2.5" />
                      <span>{formatSeconds(sec)}</span>
                    </button>
                  )}
                  {isCaller && !readOnly && m.text.trim().length >= 3 && (
                    <button
                      type="button"
                      onClick={() => onTeachClick(m.text)}
                      className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium text-emerald-500 hover:bg-emerald-500/10"
                      title="สอนคำตอบที่ถูกต้องให้ J.A.R.V.I.S สำหรับคำถามนี้"
                    >
                      <BookOpenCheck className="h-2.5 w-2.5" />
                      <span>สอน AI</span>
                    </button>
                  )}
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
  readOnly = false,
}: {
  log: CallLogEntry;
  onClose: () => void;
  onReanalyze: (phone: string, sessionId: string) => void;
  readOnly?: boolean;
}) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const teachFormRef = useRef<HTMLFormElement | null>(null);
  const [audioSec, setAudioSec] = useState(0);
  const [playbackRate, setPlaybackRate] = useState(1);
  const [teachOpen, setTeachOpen] = useState(false);
  const [teachQ, setTeachQ] = useState("");
  const [teachA, setTeachA] = useState("");
  const [teachStatus, setTeachStatus] = useState<string | null>(null);
  const [teachSaving, setTeachSaving] = useState(false);

  useEffect(() => {
    setTeachOpen(false);
    setTeachStatus(null);
    setTeachQ("");
    setTeachA("");
    setAudioSec(0);
  }, [log.sessionId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const status = callStatusMeta(log.status);
  const analyzing = log.analysisStatus === "analyzing";
  const canPlayRecording = Boolean(log.hasRecording && !readOnly);

  const handleSeek = (sec: number) => {
    const el = audioRef.current;
    if (!el) return;
    el.currentTime = Math.max(0, sec);
    void el.play().catch(() => {});
  };

  const handleSpeedChange = (rate: number) => {
    setPlaybackRate(rate);
    if (audioRef.current) audioRef.current.playbackRate = rate;
  };

  const handleOpenTeach = (question: string) => {
    setTeachQ(question.trim());
    setTeachA("");
    setTeachStatus(null);
    setTeachOpen(true);
    window.requestAnimationFrame(() => {
      teachFormRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  };

  const handleSubmitTeach = async (e: React.FormEvent) => {
    e.preventDefault();
    if (teachQ.trim().length < 3 || teachA.trim().length < 3) {
      setTeachStatus("กรุณากรอกคำถามและคำตอบอย่างน้อย 3 ตัวอักษร");
      return;
    }
    setTeachSaving(true);
    setTeachStatus(null);
    try {
      const res = await fetch("/api/teach", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: teachQ.trim(), answer: teachA.trim(), lang: "th" }),
      });
      const j = await res.json();
      if (!res.ok || !j.ok) {
        setTeachStatus(`ไม่สำเร็จ: ${j.error || "เกิดข้อผิดพลาด"}`);
      } else {
        setTeachStatus("สอน J.A.R.V.I.S เรียบร้อยแล้ว! สายถัดไปที่ถามเรื่องนี้จะตอบได้ทันที");
        setTeachA("");
      }
    } catch {
      setTeachStatus("ไม่สามารถเชื่อมต่อเซิร์ฟเวอร์เพื่อบันทึกคำตอบได้");
    } finally {
      setTeachSaving(false);
    }
  };

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
          {/* Dual-Channel Call Recording Player (Admin-only) */}
          {log.hasRecording && (
            <section className="cc-inset p-3.5">
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <span className="inline-flex items-center gap-1.5 text-xs font-semibold leading-none cc-text">
                  <Volume2 className="h-3.5 w-3.5" />
                  <span>เสียงบันทึกการสนทนา (ซ้าย: ผู้โทร / ขวา: AI)</span>
                </span>
                {canPlayRecording && (
                  <div className="inline-flex items-center gap-1">
                    {[1, 1.25, 1.5].map((r) => (
                      <button
                        key={r}
                        type="button"
                        onClick={() => handleSpeedChange(r)}
                        className={`cc-btn h-6 px-2 text-[10px] font-mono ${playbackRate === r ? "cc-btn--tone tone-live" : ""}`}
                      >
                        {r}x
                      </button>
                    ))}
                  </div>
                )}
              </div>
              {canPlayRecording ? (
                <>
                  <audio
                    ref={audioRef}
                    controls
                    preload="metadata"
                    src={`/api/recordings/${encodeURIComponent(log.sessionId)}`}
                    onLoadedMetadata={(e) => {
                      e.currentTarget.playbackRate = playbackRate;
                    }}
                    onTimeUpdate={(e) => setAudioSec(e.currentTarget.currentTime)}
                    className="h-9 w-full"
                  />
                  <p className="mt-1.5 text-[11px] cc-dim">คลิกปุ่มเวลาในบทสนทนาข้างล่างเพื่อข้ามเสียงไปยังช่วงนั้นทันที</p>
                </>
              ) : (
                <div className="inline-flex items-center gap-1.5 text-xs cc-muted">
                  <Lock className="h-3.5 w-3.5" />
                  <span>ไฟล์บันทึกเสียงสงวนสิทธิ์เฉพาะผู้ดูแลระบบที่เข้าสู่ระบบแล้ว</span>
                </div>
              )}
            </section>
          )}

          {/* 1-Click Teach AI Panel */}
          {teachOpen && !readOnly && (
            <form ref={teachFormRef} onSubmit={(e) => void handleSubmitTeach(e)} className="cc-inset tone-in space-y-2.5 p-3.5">
              <div className="flex items-center justify-between gap-2">
                <span className="inline-flex items-center gap-1.5 text-xs font-semibold cc-text">
                  <BookOpenCheck className="h-3.5 w-3.5" />
                  <span>สอนคำตอบนี้ให้ J.A.R.V.I.S เพิ่ม</span>
                </span>
                <button type="button" onClick={() => setTeachOpen(false)} className="cc-btn h-6 w-6 px-0">
                  <X className="h-3 w-3" />
                </button>
              </div>
              <input
                type="text"
                value={teachQ}
                onChange={(e) => setTeachQ(e.target.value)}
                placeholder="คำถามของลูกค้า..."
                className="w-full rounded-lg border border-[var(--cc-border)] bg-[var(--cc-surface)] px-3 py-1.5 text-xs cc-text outline-none focus:border-emerald-500"
              />
              <textarea
                rows={2}
                value={teachA}
                onChange={(e) => setTeachA(e.target.value)}
                placeholder="พิมพ์คำตอบที่ถูกต้องแบบภาษาพูดสั้นๆ 1-2 ประโยค..."
                className="w-full rounded-lg border border-[var(--cc-border)] bg-[var(--cc-surface)] px-3 py-1.5 text-xs cc-text outline-none focus:border-emerald-500"
              />
              {teachStatus && <p className="text-[11px] font-medium cc-text">{teachStatus}</p>}
              <div className="flex justify-end">
                <button type="submit" disabled={teachSaving} className="cc-btn cc-btn--tone tone-in h-8 px-3 text-xs">
                  <BookOpenCheck className="h-3.5 w-3.5" />
                  <span>{teachSaving ? "กำลังบันทึก..." : "บันทึกสอน AI ทันที"}</span>
                </button>
              </div>
            </form>
          )}

          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="inline-flex items-center gap-1.5 text-sm font-semibold leading-none cc-text">
              <Sparkles className="h-4 w-4" />
              <span>ผลวิเคราะห์คุณภาพสาย (AI QA)</span>
            </h3>
            {!readOnly && (
              <button
                type="button"
                disabled={analyzing || log.messages.length === 0}
                title={log.messages.length === 0 ? "สายนี้ยังไม่มีข้อความสนทนาสำหรับวิเคราะห์" : "วิเคราะห์คุณภาพสายซ้ำด้วย DeepSeek"}
                onClick={() => onReanalyze(log.phone, log.sessionId)}
                className="cc-btn cc-btn--tone tone-ai h-9"
              >
                <RefreshCw className={`h-3.5 w-3.5 ${analyzing ? "animate-spin" : ""}`} />
                <span>{analyzing ? "กำลังวิเคราะห์..." : "วิเคราะห์ใหม่ (DeepSeek)"}</span>
              </button>
            )}
          </div>

          <AnalysisState log={log} />

          <div>
            <div className="mb-2.5 flex flex-wrap items-center justify-between gap-2">
              <h3 className="inline-flex items-center gap-1.5 text-sm font-semibold leading-none cc-text">
                <MessageSquareText className="h-4 w-4" />
                <span>บทสนทนาทั้งหมด</span>
                <span className="cc-chip tone-idle tabular-nums">{log.messages.length} ข้อความ</span>
              </h3>
              {!readOnly && !teachOpen && (
                <button type="button" onClick={() => handleOpenTeach("")} className="cc-btn h-7 px-2.5 text-[11px]">
                  <BookOpenCheck className="h-3 w-3" />
                  <span>สอน AI เพิ่ม</span>
                </button>
              )}
            </div>
            <Transcript
              messages={log.messages}
              callStartedAt={log.startedAt}
              hasAudio={canPlayRecording}
              currentAudioSec={audioSec}
              readOnly={readOnly}
              onSeekAudio={handleSeek}
              onTeachClick={handleOpenTeach}
            />
          </div>
        </div>
      </aside>
    </div>
  );
}
