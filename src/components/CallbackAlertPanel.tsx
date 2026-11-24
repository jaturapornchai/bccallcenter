"use client";

import {
  BellRing,
  CheckCircle2,
  ClipboardList,
  Lightbulb,
  PhoneCall,
  UserCheck,
  Volume2,
} from "lucide-react";
import { formatDateTime, formatPhone } from "@/components/LineCard";
import type { CrmTicket } from "@/types/sip";

function extractExtraDescriptionLines(t: CrmTicket): string[] {
  const lines = String(t.description || "").split(/\r?\n/);
  const extra: string[] = [];
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) continue;
    if (
      /^เรื่องที่ให้โทรกลับ\s*[:：]/.test(line) ||
      /^ประเด็นที่ลูกค้าแจ้ง/.test(line) ||
      /^\d+\.\s+/.test(line) ||
      /^เบอร์ให้โทรกลับ\s*[:：]/.test(line) ||
      /^โทรเข้าจากเบอร์\s*[:：]/.test(line) ||
      /^ชื่อลูกค้า\s*[:：]/.test(line) ||
      /^บริษัท\s*[:：]/.test(line)
    ) {
      continue;
    }
    extra.push(line);
  }
  return extra;
}

export function CallbackAdviceBox({ advice }: { advice?: string | null }) {
  if (!advice || !advice.trim()) return null;
  return (
    <div className="mt-2.5 rounded-xl border border-[var(--tone-line)] bg-[var(--tone-soft)] tone-ai p-3">
      <div className="flex items-center gap-1.5 text-[11.5px] font-bold cc-text">
        <Lightbulb className="h-3.5 w-3.5 shrink-0" />
        <span>คำแนะนำในการโทรกลับให้พนักงาน</span>
      </div>
      <p className="mt-1.5 whitespace-pre-line text-xs leading-relaxed cc-text">{advice}</p>
    </div>
  );
}

function TicketFullDetailsBox({ ticket }: { ticket: CrmTicket }) {
  const topics = Array.isArray(ticket.topics) ? ticket.topics.filter(Boolean) : [];
  const extraLines = extractExtraDescriptionLines(ticket);
  const merged = Math.max(1, Number(ticket.mergedCount || 1));

  return (
    <div className="mt-2 space-y-2">
      <p className="whitespace-pre-line text-xs font-semibold leading-relaxed cc-text">
        {ticket.subject}
      </p>

      {(topics.length > 0 || extraLines.length > 0 || ticket.description) && (
        <div className="rounded-xl border border-[var(--cc-border)] bg-[var(--cc-panel)] p-2.5 text-xs leading-relaxed cc-muted">
          {topics.length > 0 ? (
            <>
              <div className="mb-1 text-[11px] font-semibold cc-text">
                {topics.length > 1
                  ? `ประเด็นที่ลูกค้าแจ้งทั้งหมด (${topics.length} ประเด็น${merged > 1 ? ` • โทรซ้ำ ${merged} ครั้ง` : ""})`
                  : "รายละเอียดเรื่องที่ลูกค้าฝากไว้"}
              </div>
              {topics.length > 1 ? (
                <ol className="list-decimal space-y-1 pl-4">
                  {topics.map((tp, idx) => (
                    <li key={idx} className="whitespace-pre-line">
                      {tp}
                    </li>
                  ))}
                </ol>
              ) : (
                <p className="whitespace-pre-line">{topics[0]}</p>
              )}
              {extraLines.length > 0 && (
                <div className="mt-2 border-t border-[var(--cc-border)] pt-2 whitespace-pre-line">
                  {extraLines.join("\n")}
                </div>
              )}
            </>
          ) : (
            <p className="whitespace-pre-line">{ticket.description}</p>
          )}
        </div>
      )}

      <CallbackAdviceBox advice={ticket.callbackAdvice} />
    </div>
  );
}

interface CallbackAlertPanelProps {
  tickets: CrmTicket[];
  readOnly: boolean;
  voiceAlertEnabled: boolean;
  audioBlocked: boolean;
  onAcknowledgeTicket: (id: string) => void;
  onCloseTicket: (id: string) => void;
  onReplayVoice: (ticket: CrmTicket) => void;
  onUnlockAudio: () => void;
  onOpenCrmTickets: () => void;
}

export function CallbackAlertPanel({
  tickets,
  readOnly,
  voiceAlertEnabled,
  audioBlocked,
  onAcknowledgeTicket,
  onCloseTicket,
  onReplayVoice,
  onUnlockAudio,
  onOpenCrmTickets,
}: CallbackAlertPanelProps) {
  if (tickets.length === 0) return null;

  const unackCount = tickets.filter((t) => t.status === "open" && !t.acknowledgedAt).length;

  return (
    <section
      aria-live="assertive"
      className="cc-panel cc-callback-alert tone-danger mb-5 p-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2.5">
          <span className="cc-icon-tile tone-danger">
            <BellRing className="h-4 w-4 animate-pulse" />
          </span>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-bold leading-none cc-text">
              แจ้งเตือนด่วน: ลูกค้าขอให้โทรกลับ ({tickets.length} เคส)
            </h2>
            {unackCount > 0 ? (
              <span className="cc-chip tone-danger">
                <span className="cc-led cc-led--blink" />
                <span>รอพนักงานรับทราบ {unackCount}</span>
              </span>
            ) : (
              <span className="cc-chip tone-held">
                <span>รับทราบแล้ว • รอโทรกลับ</span>
              </span>
            )}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {audioBlocked && voiceAlertEnabled && (
            <button
              type="button"
              onClick={onUnlockAudio}
              className="cc-btn cc-btn--tone tone-held h-8 px-3"
            >
              <Volume2 className="h-3.5 w-3.5" />
              <span>แตะเพื่อเปิดเสียงพูด</span>
            </button>
          )}
          <button
            type="button"
            onClick={onOpenCrmTickets}
            className="cc-btn h-8 px-3"
          >
            <ClipboardList className="h-3.5 w-3.5" />
            <span>เปิดบอร์ด CRM</span>
          </button>
        </div>
      </div>

      <div className={`mt-3 grid grid-cols-1 gap-3 ${tickets.length > 1 ? "lg:grid-cols-2" : ""}`}>
        {tickets.slice(0, 6).map((t) => {
          const cbPhone = t.callbackPhone || t.phone;
          const hasDiffCaller = Boolean(t.callerPhone && cbPhone && t.callerPhone !== cbPhone);
          const isUnack = t.status === "open" && !t.acknowledgedAt;
          const merged = Math.max(1, Number(t.mergedCount || 1));

          return (
            <div
              key={t.id}
              className={`cc-inset p-3.5 ${isUnack ? "tone-danger border-[var(--tone-line)]" : "tone-held"}`}
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="font-mono text-xs font-bold cc-text">{t.id}</span>
                  {isUnack ? (
                    <span className="cc-chip tone-danger text-[10.5px]">
                      <span className="cc-led cc-led--blink" />
                      <span>รอรับทราบ</span>
                    </span>
                  ) : (
                    <span className="cc-chip tone-held text-[10.5px]">
                      <span>รับทราบแล้ว • กำลังโทรกลับ</span>
                    </span>
                  )}
                  {merged > 1 && (
                    <span className="cc-chip tone-ai text-[10.5px]">
                      รวม {merged} ครั้ง
                    </span>
                  )}
                  {typeof t.lineId === "number" && (
                    <span className="cc-chip tone-ai text-[10.5px] font-mono">L{t.lineId}</span>
                  )}
                </div>
                <span className="text-[11px] cc-dim">{formatDateTime(t.updatedAt || t.createdAt)}</span>
              </div>

              <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
                {cbPhone && (
                  <span className="inline-flex items-center gap-1 font-mono font-bold cc-text">
                    <PhoneCall className="h-3.5 w-3.5 text-emerald-400" />
                    <span>{formatPhone(cbPhone)}</span>
                  </span>
                )}
                {t.customerName && <span className="font-semibold cc-text">• {t.customerName}</span>}
                {t.company && <span className="cc-muted">({t.company})</span>}
                {hasDiffCaller && (
                  <span className="text-[11px] cc-muted">(โทรเข้าจาก {formatPhone(t.callerPhone!)})</span>
                )}
              </div>

              <TicketFullDetailsBox ticket={t} />

              {t.acknowledgedBy && t.acknowledgedAt && (
                <div className="mt-1.5 text-[11px] cc-dim">
                  รับทราบโดย {t.acknowledgedBy} • {formatDateTime(t.acknowledgedAt)}
                </div>
              )}

              <div className="mt-3 flex flex-wrap items-center gap-2">
                {!readOnly && isUnack && (
                  <button
                    type="button"
                    onClick={() => onAcknowledgeTicket(t.id)}
                    className="cc-btn cc-btn--tone tone-held h-8 px-3"
                  >
                    <UserCheck className="h-3.5 w-3.5" />
                    <span>รับทราบแล้ว</span>
                  </button>
                )}
                {!readOnly && (
                  <button
                    type="button"
                    onClick={() => onCloseTicket(t.id)}
                    className="cc-btn cc-btn--solid tone-in h-8 px-3"
                  >
                    <CheckCircle2 className="h-3.5 w-3.5" />
                    <span>ปิด Ticket (พนักงานรับรู้แล้ว)</span>
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => onReplayVoice(t)}
                  className="cc-btn h-8 px-2.5"
                  title="ฟังเสียงพูดแจ้งเตือนอีกครั้ง"
                >
                  <Volume2 className="h-3.5 w-3.5" />
                  <span>ฟังเสียงเตือน</span>
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

interface CallbackTicketListProps {
  tickets: CrmTicket[];
  readOnly: boolean;
  onAcknowledgeTicket: (id: string) => void;
  onCloseTicket: (id: string) => void;
  onReplayVoice: (ticket: CrmTicket) => void;
  onOpenCrmTickets: () => void;
}

export function CallbackTicketList({
  tickets,
  readOnly,
  onAcknowledgeTicket,
  onCloseTicket,
  onReplayVoice,
  onOpenCrmTickets,
}: CallbackTicketListProps) {
  const sorted = [...tickets].sort((a, b) => {
    const aClosed = a.status === "closed" || a.status === "resolved" ? 1 : 0;
    const bClosed = b.status === "closed" || b.status === "resolved" ? 1 : 0;
    if (aClosed !== bClosed) return aClosed - bClosed;
    return String(b.updatedAt || b.createdAt).localeCompare(String(a.updatedAt || a.createdAt));
  });

  if (sorted.length === 0) {
    return (
      <div className="cc-empty px-4 py-10 text-center text-xs">
        ยังไม่มีเคสขอให้โทรกลับ — เมื่อลูกค้าขอคุยกับพนักงานจริงระหว่างสาย ระบบจะเปิด Ticket และแจ้งเตือนที่นี่ทันที
      </div>
    );
  }

  return (
    <div className="space-y-2.5">
      <div className="max-h-[620px] space-y-2.5 overflow-y-auto pr-1 cc-scroll">
        {sorted.slice(0, 20).map((t) => {
          const isClosed = t.status === "closed" || t.status === "resolved";
          const isUnack = t.status === "open" && !t.acknowledgedAt;
          const cbPhone = t.callbackPhone || t.phone;
          const hasDiffCaller = Boolean(t.callerPhone && cbPhone && t.callerPhone !== cbPhone);
          const merged = Math.max(1, Number(t.mergedCount || 1));

          return (
            <div
              key={t.id}
              className={`cc-inset p-3 ${
                isClosed ? "tone-idle opacity-85" : isUnack ? "tone-danger border-[var(--tone-line)]" : "tone-held"
              }`}
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="font-mono text-[11px] font-bold cc-text">{t.id}</span>
                  {isClosed ? (
                    <span className="cc-chip tone-in text-[10px]">
                      <CheckCircle2 className="h-3 w-3" />
                      <span>ปิด Ticket แล้ว</span>
                    </span>
                  ) : isUnack ? (
                    <span className="cc-chip tone-danger text-[10px]">
                      <span className="cc-led cc-led--blink" />
                      <span>รอรับทราบ</span>
                    </span>
                  ) : (
                    <span className="cc-chip tone-held text-[10px]">
                      <span>รับทราบแล้ว</span>
                    </span>
                  )}
                  {merged > 1 && (
                    <span className="cc-chip tone-ai text-[10px]">
                      รวม {merged} ครั้ง
                    </span>
                  )}
                </div>
                <span className="text-[10.5px] cc-dim">{formatDateTime(t.updatedAt || t.createdAt)}</span>
              </div>

              <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs">
                {cbPhone && <span className="font-mono font-semibold cc-text">{formatPhone(cbPhone)}</span>}
                {t.customerName && <span className="cc-text">• {t.customerName}</span>}
                {t.company && <span className="cc-muted">({t.company})</span>}
                {hasDiffCaller && (
                  <span className="text-[11px] cc-muted">(โทรเข้าจาก {formatPhone(t.callerPhone!)})</span>
                )}
              </div>

              <TicketFullDetailsBox ticket={t} />

              {isClosed ? (
                <div className="mt-2 text-[10.5px] cc-dim">
                  พนักงานรับรู้/ปิดเคสแล้ว{t.closedBy ? ` โดย ${t.closedBy}` : ""}
                  {t.closedAt ? ` • ${formatDateTime(t.closedAt)}` : ""}
                </div>
              ) : (
                <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
                  {!readOnly && isUnack && (
                    <button
                      type="button"
                      onClick={() => onAcknowledgeTicket(t.id)}
                      className="cc-btn cc-btn--tone tone-held h-7 px-2.5 text-[11px]"
                    >
                      <UserCheck className="h-3 w-3" />
                      <span>รับทราบแล้ว</span>
                    </button>
                  )}
                  {!readOnly && (
                    <button
                      type="button"
                      onClick={() => onCloseTicket(t.id)}
                      className="cc-btn cc-btn--solid tone-in h-7 px-2.5 text-[11px]"
                    >
                      <CheckCircle2 className="h-3 w-3" />
                      <span>ปิด Ticket (รับรู้แล้ว)</span>
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => onReplayVoice(t)}
                    className="cc-btn h-7 px-2 text-[11px]"
                    title="ฟังเสียงเตือน"
                  >
                    <Volume2 className="h-3 w-3" />
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>

      <button
        type="button"
        onClick={onOpenCrmTickets}
        className="cc-btn h-8 w-full text-xs"
      >
        <ClipboardList className="h-3.5 w-3.5" />
        <span>จัดการ Ticket ทั้งหมดในระบบ CRM</span>
      </button>
    </div>
  );
}
