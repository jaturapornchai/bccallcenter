"use client";

import type { CSSProperties, ReactNode } from "react";
import {
  Bot,
  Globe,
  Headphones,
  History,
  Mic,
  MicOff,
  Pause,
  Phone,
  PhoneCall,
  PhoneIncoming,
  PhoneOff,
  Play,
  Timer,
  UserCheck,
} from "lucide-react";
import { SUPPORTED_LANGUAGES, languageShortLabel } from "@/types/sip";
import type { SipLine } from "@/types/sip";

interface LineCardProps {
  line: SipLine;
  isDark: boolean;
  isActiveAudio: boolean;
  nowMs: number;
  onAnswer: (lineId: number) => void;
  onReject: (lineId: number) => void;
  onHangup: (lineId: number) => void;
  onMute: (lineId: number) => void;
  onHold: (lineId: number) => void;
  onSelectAudio: (lineId: number) => void;
  onTakeOverAi: (lineId: number) => void;
  onAttachAi: (lineId: number) => void;
  onViewCallerHistory?: (phone: string) => void;
  onChangeLanguage?: (lineId: number, language: string | null) => void;
}

/** Visual signal tone of a line — maps to `.tone-*` classes in globals.css */
export type SignalTone = "in" | "ai" | "live" | "held" | "idle";

export function lineTone(line: SipLine): SignalTone {
  if (line.state === "ringing_in") return "in";
  if (line.state === "connected") return line.aiActive ? "ai" : "live";
  if (line.state === "held") return "held";
  return "idle";
}

const TONE_LABEL: Record<SignalTone, string> = {
  in: "มีสายเรียกเข้า",
  ai: "J.A.R.V.I.S กำลังสนทนา",
  live: "เจ้าหน้าที่กำลังสนทนา",
  held: "พักสายอยู่",
  idle: "พร้อมรับสาย",
};

export function lineStatusLabel(line: SipLine): string {
  return TONE_LABEL[lineTone(line)];
}

export function formatPhone(num: string): string {
  const digits = num.replace(/\D/g, "");
  if (digits.length === 10 && digits.startsWith("0")) {
    return `${digits.slice(0, 3)}-${digits.slice(3, 6)}-${digits.slice(6)}`;
  }
  if (digits.length === 9 && digits.startsWith("02")) {
    return `${digits.slice(0, 2)}-${digits.slice(2, 5)}-${digits.slice(5)}`;
  }
  return num || "-";
}

/** mm:ss from a number of seconds */
export function formatSeconds(totalSec: number): string {
  const sec = Math.max(0, Math.floor(totalSec || 0));
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export function formatDuration(startMs: number | null, nowMs: number): string {
  if (!startMs) return "00:00";
  return formatSeconds((nowMs - startMs) / 1000);
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  try {
    return new Date(iso).toLocaleString("th-TH", {
      day: "2-digit",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "-";
  }
}

/** Call / session end status (sip-engine addHistory + call-memory session.status) */
const CALL_STATUS: Record<string, { label: string; tone: SignalTone | "danger" }> = {
  active: { label: "กำลังสนทนา", tone: "live" },
  answered: { label: "รับสายแล้ว", tone: "in" },
  missed: { label: "ไม่ได้รับสาย", tone: "danger" },
  rejected: { label: "ปฏิเสธสาย", tone: "danger" },
  cancelled: { label: "ยกเลิกสาย", tone: "held" },
  busy: { label: "สายไม่ว่าง", tone: "held" },
  failed: { label: "ไม่สำเร็จ", tone: "danger" },
};

export function callStatusMeta(status: string): { label: string; tone: SignalTone | "danger" } {
  return CALL_STATUS[status] ?? { label: status || "-", tone: "idle" };
}

/* Deterministic bar timings (no Math.random → no SSR hydration mismatch) */
const EQ_DURATIONS = [0.82, 0.64, 1.02, 0.71, 0.93, 0.58, 0.88, 0.76, 1.1, 0.67, 0.95, 0.6, 0.84, 0.72, 1.0, 0.69];

export function AudioEqualizer({
  bars = 7,
  active = true,
  className = "",
}: {
  bars?: number;
  active?: boolean;
  className?: string;
}) {
  return (
    <span className={`cc-eq ${active ? "" : "cc-eq--idle"} ${className}`} aria-hidden="true">
      {Array.from({ length: bars }, (_, i) => {
        const d = EQ_DURATIONS[i % EQ_DURATIONS.length];
        const style = {
          "--eq-d": `${d}s`,
          "--eq-delay": `${(-((i * 0.13) % d)).toFixed(2)}s`,
        } as CSSProperties;
        return <span key={i} className="cc-eq__bar" style={style} />;
      })}
    </span>
  );
}

/**
 * Tactical radar: concentric field + rotating sweep.
 * mode "alert" adds expanding signal rings and blips, "scan" = slow sweep only, "static" = field only.
 */
export function SignalRadar({
  tone,
  mode,
  className = "",
  coreClassName = "",
  children,
}: {
  tone: SignalTone;
  mode: "alert" | "scan" | "static";
  className?: string;
  coreClassName?: string;
  children: ReactNode;
}) {
  const quietCore = mode === "static" && tone === "idle";
  return (
    <span className={`cc-radar tone-${tone} ${mode === "scan" ? "cc-radar--scan" : ""} ${className}`} aria-hidden="true">
      <span className="cc-radar__field" />
      {mode !== "static" && <span className="cc-radar__sweep" />}
      {mode === "alert" && (
        <>
          <span className="cc-radar__ping" />
          <span className="cc-radar__ping" style={{ animationDelay: "0.8s" }} />
          <span className="cc-radar__ping" style={{ animationDelay: "1.6s" }} />
          <span className="cc-radar__blip" style={{ top: "22%", left: "64%" }} />
          <span className="cc-radar__blip" style={{ top: "64%", left: "26%", animationDelay: "1.1s" }} />
        </>
      )}
      <span className={`cc-radar__core ${quietCore ? "cc-radar__core--quiet" : ""} ${coreClassName}`}>{children}</span>
    </span>
  );
}

function radarMode(tone: SignalTone): "alert" | "scan" | "static" {
  if (tone === "in" || tone === "ai") return "alert";
  if (tone === "live") return "scan";
  return "static";
}

function CoreIcon({ line, tone }: { line: SipLine; tone: SignalTone }) {
  switch (tone) {
    case "in":
      return <PhoneIncoming className="h-4 w-4 cc-float" />;
    case "ai":
      return <Bot className="h-4 w-4" />;
    case "live":
      return <PhoneCall className="h-4 w-4" />;
    case "held":
      return <Pause className="h-4 w-4" />;
    default:
      return <span className="text-xs tabular-nums">L{line.lineId}</span>;
  }
}

export function LineCard({
  line,
  isActiveAudio,
  nowMs,
  onAnswer,
  onReject,
  onHangup,
  onMute,
  onHold,
  onSelectAudio,
  onTakeOverAi,
  onAttachAi,
  onViewCallerHistory,
  onChangeLanguage,
}: LineCardProps) {
  const tone = lineTone(line);
  const isIdle = line.state === "idle";
  const isRingingIn = line.state === "ringing_in";
  const isConnected = line.state === "connected";
  const isHeld = line.state === "held";
  const isReturningCaller = line.callerHasHistory || (line.callerTotalCalls ?? 0) > 1;
  const statusLabel = TONE_LABEL[tone];

  return (
    <article data-tone={tone} data-state={line.state} className={`cc-line tone-${tone}`} aria-label={`คู่สาย ${line.lineId}: ${statusLabel}`}>
      {isRingingIn && <span className="cc-sheen" aria-hidden="true" />}

      <div className="relative flex flex-wrap items-center justify-between gap-3">
        {/* Left: radar + status + caller */}
        <div className="flex min-w-0 flex-1 items-center gap-3.5">
          <SignalRadar tone={tone} mode={radarMode(tone)} className="h-12 w-12" coreClassName="h-8 w-8">
            <CoreIcon line={line} tone={tone} />
          </SignalRadar>

          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="inline-flex items-center gap-1 text-sm font-semibold leading-none cc-text">
                <span>คู่สาย</span>
                <span className="font-mono tabular-nums">{line.lineId}</span>
              </span>
              <span className="cc-chip">
                <span className={`cc-led ${isRingingIn ? "cc-led--blink" : ""}`} />
                <span>{statusLabel}</span>
              </span>

              {(isConnected || isHeld) && isActiveAudio && (
                <span className="cc-chip tone-live">
                  <Headphones className="h-3 w-3" />
                  <span>กำลังฟังสายนี้</span>
                </span>
              )}

              {!isIdle && isReturningCaller && (
                <button type="button" onClick={() => onViewCallerHistory?.(line.remoteNumber)} className="cc-chip tone-held">
                  <History className="h-3 w-3" />
                  <span>ลูกค้าเก่า • ครั้งที่</span>
                  <span className="tabular-nums">{line.callerTotalCalls}</span>
                </button>
              )}

              {!isIdle && line.crmTier === "vip" && (
                <span className="cc-chip tone-ai">VIP</span>
              )}
              {!isIdle && line.crmTier === "at_risk" && (
                <span className="cc-chip tone-danger">ต้องดูแลพิเศษ</span>
              )}
              {!isIdle && (line.crmOpenTickets ?? 0) > 0 && (
                <span className="cc-chip tone-danger">
                  <span>เคสค้าง</span>
                  <span className="tabular-nums">{line.crmOpenTickets}</span>
                </span>
              )}

              {!isIdle && (
                <span className={`cc-chip ${line.language ? "tone-live" : "tone-held"}`}>
                  <Globe className="h-3 w-3" />
                  <span>{languageShortLabel(line.language)}</span>
                </span>
              )}
            </div>

            <div className="mt-2 flex flex-wrap items-center gap-2.5">
              {isIdle ? (
                <span className="inline-flex items-center gap-1.5 text-xs leading-none cc-dim">
                  <span>สแตนด์บาย • เฝ้าระวังสายเข้า</span>
                </span>
              ) : (
                <>
                  <span className="font-mono text-lg font-semibold leading-none tracking-wide tabular-nums cc-text">
                    {formatPhone(line.remoteNumber)}
                  </span>
                  {(line.crmName || line.crmCompany) && (
                    <span className="text-xs font-medium cc-muted">
                      {[line.crmName, line.crmCompany].filter(Boolean).join(" • ")}
                    </span>
                  )}
                  <span className="cc-chip tone-idle font-mono tabular-nums">
                    <Timer className="h-3 w-3" />
                    <span>
                      {isRingingIn ? "รอ " : ""}
                      {formatDuration(line.connectedAt || line.startedAt, nowMs)}
                    </span>
                  </span>
                  {(isConnected || isRingingIn) && <AudioEqualizer bars={9} className="h-4" />}
                </>
              )}
            </div>

            {line.aiActive && line.aiTranscript && (
              <div className="cc-quote mt-2 flex items-center gap-2 px-2.5 py-1.5 text-xs leading-snug tone-ai" title={line.aiTranscript}>
                <Bot className="h-3.5 w-3.5 shrink-0" />
                <span className="truncate">{line.aiTranscript}</span>
              </div>
            )}
          </div>
        </div>

        {/* Right: inbound actions */}
        <div className="flex flex-wrap items-center gap-2">
          {isIdle && (
            <span className="cc-chip tone-idle h-9 rounded-xl px-3">
              <span className="cc-led" />
              <span>รอสายเข้า</span>
            </span>
          )}

          {isRingingIn && (
            <>
              <button type="button" onClick={() => onAnswer(line.lineId)} className="cc-btn cc-btn--solid cc-btn--lg tone-in">
                <Phone className="h-4 w-4" />
                <span>กดรับสาย</span>
              </button>
              <button type="button" onClick={() => onReject(line.lineId)} className="cc-btn cc-btn--tone cc-btn--lg tone-danger">
                <PhoneOff className="h-4 w-4" />
                <span>ปฏิเสธ</span>
              </button>
            </>
          )}

          {(isConnected || isHeld) && (
            <>
              {onChangeLanguage && (
                <select
                  aria-label="เปลี่ยนภาษากลางสาย"
                  value={line.language || ""}
                  onChange={(e) => onChangeLanguage(line.lineId, e.target.value || null)}
                  className="h-10 rounded-xl border border-[var(--cc-border)] bg-[var(--cc-inset)] px-2.5 text-xs font-medium cc-text outline-none cursor-pointer"
                  title="เปลี่ยนภาษากลางสาย (J.A.R.V.I.S + บันทึกลง CRM)"
                >
                  <option value="">🌐 อัตโนมัติ (ถามภาษา)</option>
                  {SUPPORTED_LANGUAGES.map((l) => (
                    <option key={l.code} value={l.code}>
                      🌐 {l.short}
                    </option>
                  ))}
                </select>
              )}

              {line.aiActive ? (
                <button type="button" onClick={() => onTakeOverAi(line.lineId)} className="cc-btn cc-btn--tone tone-in">
                  <UserCheck className="h-3.5 w-3.5" />
                  <span>รับสายแทน AI</span>
                </button>
              ) : (
                <button type="button" onClick={() => onAttachAi(line.lineId)} className="cc-btn cc-btn--tone tone-ai">
                  <Bot className="h-3.5 w-3.5" />
                  <span>ให้ AI ตอบ</span>
                </button>
              )}

              {!isActiveAudio && (
                <button type="button" onClick={() => onSelectAudio(line.lineId)} className="cc-btn cc-btn--tone tone-live">
                  <Headphones className="h-3.5 w-3.5" />
                  <span>ฟังเสียง</span>
                </button>
              )}

              {!line.aiActive && (
                <button
                  type="button"
                  onClick={() => onMute(line.lineId)}
                  disabled={isHeld}
                  className={`cc-btn ${line.muted ? "cc-btn--tone tone-held" : ""}`}
                >
                  {line.muted ? <MicOff className="h-3.5 w-3.5" /> : <Mic className="h-3.5 w-3.5" />}
                  <span>{line.muted ? "เปิดไมค์" : "ปิดไมค์"}</span>
                </button>
              )}

              <button type="button" onClick={() => onHold(line.lineId)} className={`cc-btn ${isHeld ? "cc-btn--tone tone-in" : ""}`}>
                {isHeld ? <Play className="h-3.5 w-3.5" /> : <Pause className="h-3.5 w-3.5" />}
                <span>{isHeld ? "คุยต่อ" : "พักสาย"}</span>
              </button>

              <button type="button" onClick={() => onHangup(line.lineId)} className="cc-btn cc-btn--solid tone-danger">
                <PhoneOff className="h-3.5 w-3.5" />
                <span>วางสาย</span>
              </button>
            </>
          )}
        </div>
      </div>
    </article>
  );
}
