"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  Activity,
  Bot,
  Briefcase,
  ChevronRight,
  Clock3,
  History,
  Moon,
  Phone,
  PhoneCall,
  PhoneIncoming,
  PhoneMissed,
  PhoneOff,
  Radio,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Sun,
  Timer,
  TriangleAlert,
  UserCheck,
  Users,
  Volume2,
  VolumeX,
} from "lucide-react";
import {
  AudioEqualizer,
  LineCard,
  SignalRadar,
  callStatusMeta,
  formatDateTime,
  formatDuration,
  formatPhone,
  formatSeconds,
  lineStatusLabel,
  lineTone,
} from "@/components/LineCard";
import { CallQaDrawer, formatScore, scoreTone, sentimentMeta } from "@/components/CallQaDrawer";
import { CrmWorkspace } from "@/components/CrmWorkspace";
import { AudioBridge } from "@/lib/audio-bridge";
import { languageShortLabel } from "@/types/sip";
import type { CallHistoryEntry, CallLogEntry, CallerRecord, ConsoleState, CrmState, SipLine } from "@/types/sip";

const INITIAL_STATE: ConsoleState = {
  trunk: {
    status: "connecting",
    host: "",
    ip: null,
    port: 5060,
    user: "",
    lastRegisterAt: null,
    expires: 120,
    error: null,
  },
  autoAnswerAi: true,
  jarvisUrl: "http://127.0.0.1:7861",
  activeAudioLine: null,
  lines: Array.from({ length: 5 }, (_, i) => ({
    lineId: i + 1,
    state: "idle",
    direction: null,
    remoteNumber: "",
    remoteName: "",
    startedAt: null,
    connectedAt: null,
    muted: false,
    held: false,
    aiActive: false,
    aiTranscript: "",
    callerTotalCalls: 0,
    callerHasHistory: false,
    sessionId: null,
  })),
  history: [],
  callers: [],
  callLogs: [],
  crm: {
    customers: [],
    tickets: [],
    stats: { totalCustomers: 0, vipOrRisk: 0, openTickets: 0, urgentTickets: 0, avgCsat: null },
  },
};

type RightTab = "qa" | "callers" | "history";
type MainView = "console" | "crm";

export default function ConsolePage() {
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [activeView, setActiveView] = useState<MainView>("console");
  const [crmFocusPhone, setCrmFocusPhone] = useState<string | null>(null);
  const [state, setState] = useState<ConsoleState>(INITIAL_STATE);
  const [wsConnected, setWsConnected] = useState(false);
  const [audioReady, setAudioReady] = useState(false);
  const [micWarning, setMicWarning] = useState<string | null>(null);
  const [selectedCallerPhone, setSelectedCallerPhone] = useState<string | null>(null);
  const [selectedLogId, setSelectedLogId] = useState<string | null>(null);
  const [rightTab, setRightTab] = useState<RightTab>("qa");
  const [toast, setToast] = useState<string | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());

  const wsRef = useRef<WebSocket | null>(null);
  const audioRef = useRef<AudioBridge | null>(null);
  const isDark = theme === "dark";

  useEffect(() => {
    try {
      const saved = localStorage.getItem("yalecom-theme");
      if (saved === "light" || saved === "dark") setTheme(saved);
    } catch {}
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme((prev) => {
      const next = prev === "dark" ? "light" : "dark";
      try {
        localStorage.setItem("yalecom-theme", next);
      } catch {}
      return next;
    });
  }, []);

  useEffect(() => {
    const timer = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    const bridge = new AudioBridge();
    audioRef.current = bridge;

    bridge.setSender((lineId, pcmu) => {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      const frame = new Uint8Array(1 + pcmu.length);
      frame[0] = lineId & 0xff;
      frame.set(pcmu, 1);
      ws.send(frame);
    });

    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let unmounted = false;

    const connect = () => {
      if (unmounted) return;
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(`${proto}//${window.location.host}/ws/sip`);
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;

      ws.onopen = () => setWsConnected(true);
      ws.onclose = () => {
        setWsConnected(false);
        if (!unmounted) reconnectTimer = setTimeout(connect, 2000);
      };

      ws.onmessage = (ev) => {
        if (ev.data instanceof ArrayBuffer) {
          const bytes = new Uint8Array(ev.data);
          if (bytes.length > 1) bridge.playIncomingPcmu(bytes.subarray(1));
          return;
        }
        try {
          const msg = JSON.parse(String(ev.data));
          if (msg.type === "state") setState(msg.data);
          else if (msg.type === "error") setToast(msg.message);
        } catch {}
      };
    };

    connect();

    return () => {
      unmounted = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      wsRef.current?.close();
      bridge.destroy();
    };
  }, []);

  useEffect(() => {
    const bridge = audioRef.current;
    if (!bridge) return;
    const activeLine = state.lines.find((l) => l.lineId === state.activeAudioLine);
    bridge.setActiveLine(activeLine && !activeLine.aiActive ? activeLine.lineId : null);

    const hasIncoming = !state.autoAnswerAi && state.lines.some((l) => l.state === "ringing_in");
    if (hasIncoming) bridge.startRingtone();
    else bridge.stopRingtone();
  }, [state]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 4000);
    return () => clearTimeout(t);
  }, [toast]);

  const activateAudio = useCallback(async () => {
    if (!audioRef.current) return;
    await audioRef.current.ensureAudio();
    setAudioReady(audioRef.current.isReady);
    setMicWarning(audioRef.current.micError);
  }, []);

  const sendCmd = useCallback((payload: Record<string, unknown>) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      setToast("ยังไม่ได้เชื่อมต่อเซิร์ฟเวอร์");
      return;
    }
    ws.send(JSON.stringify(payload));
  }, []);

  const handleAnswer = useCallback(
    async (lineId: number) => {
      await activateAudio();
      sendCmd({ type: "answer", lineId });
    },
    [activateAudio, sendCmd]
  );

  const handleTakeOverAi = useCallback(
    async (lineId: number) => {
      await activateAudio();
      sendCmd({ type: "takeOverFromAi", lineId });
    },
    [activateAudio, sendCmd]
  );

  const handleReject = useCallback((lineId: number) => sendCmd({ type: "reject", lineId }), [sendCmd]);
  const handleReanalyze = useCallback(
    (phone: string, sessionId: string) => sendCmd({ type: "reanalyze", phone, sessionId }),
    [sendCmd]
  );
  const closeDrawer = useCallback(() => setSelectedLogId(null), []);
  const viewCaller = useCallback((phone: string) => {
    setSelectedCallerPhone(phone);
    setRightTab("callers");
  }, []);

  const callers: CallerRecord[] = state.callers || [];
  const callLogs: CallLogEntry[] = state.callLogs || [];
  const activeCallerRecord = useMemo(() => {
    if (selectedCallerPhone) {
      return callers.find((c) => c.phone === selectedCallerPhone) || callers[0] || null;
    }
    return callers[0] || null;
  }, [callers, selectedCallerPhone]);
  // Looked up from live state so the drawer refreshes when DeepSeek analysis finishes
  const selectedLog = useMemo(
    () => (selectedLogId ? callLogs.find((l) => l.sessionId === selectedLogId) ?? null : null),
    [callLogs, selectedLogId]
  );

  const busyCount = useMemo(() => state.lines.filter((l) => l.state !== "idle").length, [state.lines]);
  const aiCount = useMemo(() => state.lines.filter((l) => l.aiActive).length, [state.lines]);
  const idleCount = state.lines.length - busyCount;
  const ringingLines = useMemo(() => state.lines.filter((l) => l.state === "ringing_in"), [state.lines]);
  const ringingLine = ringingLines[0];
  const activeAiLine = useMemo(() => state.lines.find((l) => l.aiActive && l.state === "connected"), [state.lines]);
  const trunkOnline = state.trunk.status === "registered" && wsConnected;
  const trunkTone = trunkOnline ? "tone-in" : state.trunk.status === "error" ? "tone-danger" : "tone-held";
  const clock = new Date(nowMs).toLocaleTimeString("th-TH", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

  return (
    <div data-theme={theme} data-alert={ringingLine ? "in" : activeAiLine ? "ai" : "none"} className="cc-root relative min-h-screen">
      {/* Command-center backdrop: ambient glow + drifting grid + telemetry scanline */}
      <div className="cc-ambient" aria-hidden="true" />
      <div className="cc-grid-bg" aria-hidden="true" />
      <div className="cc-scanline" aria-hidden="true" />

      <div className="relative mx-auto max-w-7xl px-4 py-5 sm:px-6 lg:px-8">
        {/* Header */}
        <header className="cc-panel mb-5 flex flex-wrap items-center justify-between gap-4 px-5 py-3.5">
          <div className="flex min-w-0 items-center gap-3.5">
            <SignalRadar
              tone={ringingLine ? "in" : activeAiLine ? "ai" : "live"}
              mode={ringingLine || activeAiLine ? "alert" : "scan"}
              className="h-12 w-12"
              coreClassName="h-8 w-8"
            >
              <PhoneIncoming className="h-4 w-4" />
            </SignalRadar>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2.5">
                <h1 className="text-lg font-bold leading-none tracking-tight cc-text">ระบบ BC Call Center</h1>
                <span className="cc-chip tone-in font-mono tabular-nums">{formatPhone(state.trunk.user)}</span>
              </div>
              <p className="mt-1.5 text-xs leading-none cc-muted">
                ศูนย์บัญชาการรับสายอัตโนมัติ 5 คู่สาย • J.A.R.V.I.S + จดจำประวัติการคุยรายเบอร์
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {/* Main Workspace Mode Switcher */}
            <div className="inline-flex items-center gap-1 rounded-xl border border-[var(--cc-border)] bg-[var(--cc-inset)] p-1">
              <button
                type="button"
                onClick={() => setActiveView("console")}
                className={`cc-btn h-7 px-3 text-xs ${activeView === "console" ? "cc-btn--tone tone-live" : "border-transparent bg-transparent"}`}
              >
                <Radio className="h-3.5 w-3.5" />
                <span>ศูนย์รับสาย</span>
              </button>
              <button
                type="button"
                onClick={() => setActiveView("crm")}
                className={`cc-btn h-7 px-3 text-xs ${activeView === "crm" ? "cc-btn--tone tone-ai" : "border-transparent bg-transparent"}`}
              >
                <Briefcase className="h-3.5 w-3.5" />
                <span>ระบบ CRM</span>
                {(state.crm?.stats.openTickets ?? 0) > 0 && (
                  <span className="cc-chip tone-danger py-0 text-[10px] tabular-nums">
                    {state.crm?.stats.openTickets}
                  </span>
                )}
              </button>
            </div>

            <span className="cc-chip tone-idle h-9 rounded-xl px-3 font-mono tabular-nums">
              <Clock3 className="h-3.5 w-3.5" />
              <span suppressHydrationWarning>{clock}</span>
            </span>

            <button
              type="button"
              role="switch"
              aria-checked={state.autoAnswerAi}
              onClick={() => sendCmd({ type: "setAutoAnswerAi", enabled: !state.autoAnswerAi })}
              className={`cc-btn h-9 gap-2 tone-ai ${state.autoAnswerAi ? "cc-btn--tone" : ""}`}
            >
              <Bot className="h-3.5 w-3.5" />
              <span>AI รับสายอัตโนมัติ</span>
              <span className={`cc-switch ${state.autoAnswerAi ? "is-on" : ""}`} aria-hidden="true">
                <span className="cc-switch__knob" />
              </span>
              <span>{state.autoAnswerAi ? "เปิด" : "ปิด"}</span>
            </button>

            <span className={`cc-chip ${trunkTone} h-9 rounded-xl px-3`}>
              <span className={`cc-led ${trunkOnline || state.trunk.status === "error" ? "" : "cc-led--blink"}`} />
              <span>
                {trunkOnline
                  ? `ออนไลน์ (${state.trunk.ip || state.trunk.host})`
                  : state.trunk.status === "error"
                    ? `ขัดข้อง: ${state.trunk.error}`
                    : "กำลังเชื่อมต่อ SIP..."}
              </span>
            </span>

            <button
              type="button"
              onClick={activateAudio}
              className={`cc-btn cc-btn--tone h-9 ${audioReady ? "tone-live" : "tone-held"}`}
            >
              {audioReady ? <Volume2 className="h-3.5 w-3.5" /> : <VolumeX className="h-3.5 w-3.5" />}
              <span>{audioReady ? "เปิดเสียงแล้ว" : "เปิดเสียง/ไมค์"}</span>
            </button>

            <button
              type="button"
              onClick={toggleTheme}
              className="cc-btn h-9"
              title={isDark ? "สลับเป็นโหมดสว่าง" : "สลับเป็นโหมดมืด"}
            >
              {isDark ? <Sun className="h-3.5 w-3.5 text-amber-300" /> : <Moon className="h-3.5 w-3.5 text-indigo-600" />}
              <span>{isDark ? "โหมดสว่าง" : "โหมดมืด"}</span>
            </button>

            <button type="button" onClick={() => sendCmd({ type: "reregister" })} className="cc-btn h-9 w-9 px-0" title="เชื่อมต่อ SIP ใหม่">
              <RefreshCw className="h-3.5 w-3.5" />
            </button>
          </div>
        </header>

        {/* Command Center Alert Hero */}
        {ringingLine || activeAiLine ? (
          <CommandAlertHero
            line={(ringingLine || activeAiLine) as SipLine}
            isRinging={Boolean(ringingLine)}
            queuedCount={Math.max(0, ringingLines.length - 1)}
            autoAnswerAi={state.autoAnswerAi}
            nowMs={nowMs}
            onAnswer={handleAnswer}
            onReject={handleReject}
            onTakeOver={handleTakeOverAi}
          />
        ) : (
          <StandbyStrip busyCount={busyCount} autoAnswerAi={state.autoAnswerAi} />
        )}

        {(toast || micWarning) && (
          <div role="status" className="cc-banner tone-held mb-4 px-4 py-2.5">
            <TriangleAlert className="h-4 w-4 shrink-0" />
            <span>{toast || `คำเตือนไมโครโฟน: ${micWarning}`}</span>
          </div>
        )}

        {activeView === "crm" ? (
          <CrmWorkspace
            crm={
              (state.crm as CrmState) || {
                customers: [],
                tickets: [],
                stats: { totalCustomers: 0, vipOrRisk: 0, openTickets: 0, urgentTickets: 0, avgCsat: null },
              }
            }
            callLogs={callLogs}
            initialPhone={crmFocusPhone}
            onUpsertCustomer={(customer) => {
              sendCmd({ type: "crmUpsertCustomer", customer });
              setToast("บันทึกข้อมูลลูกค้าลง CRM เรียบร้อยแล้ว");
            }}
            onCreateTicket={(ticket) => {
              sendCmd({ type: "crmCreateTicket", ticket });
              setToast("สร้างเคสติดตามงานใหม่เรียบร้อยแล้ว");
            }}
            onUpdateTicket={(id, patch) => sendCmd({ type: "crmUpdateTicket", id, patch })}
            onDeleteTicket={(id) => sendCmd({ type: "crmDeleteTicket", id })}
            onOpenQaLog={(_phone, sessionId) => setSelectedLogId(sessionId)}
          />
        ) : (
          <>
            {/* KPI telemetry */}
            <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
              <KpiTile tone="in" label="คู่สายว่าง" value={idleCount} suffix={`/ ${state.lines.length}`} meter={idleCount} icon={<Phone className="h-4 w-4" />} />
              <KpiTile tone="live" label="กำลังให้บริการ" value={busyCount} meter={busyCount} icon={<PhoneCall className="h-4 w-4" />} />
              <KpiTile tone="ai" label="J.A.R.V.I.S ดูแล" value={aiCount} meter={aiCount} icon={<Bot className="h-4 w-4" />} />
              <KpiTile tone="held" label="เคส CRM เปิดค้าง" value={state.crm?.stats.openTickets ?? 0} icon={<Briefcase className="h-4 w-4" />} />
            </div>

            <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
              {/* Left: 5-line switchboard */}
              <section className="space-y-3 lg:col-span-7">
                <div className="flex flex-wrap items-center justify-between gap-3 px-1">
                  <h2 className="inline-flex items-center gap-2 text-sm font-semibold leading-none cc-text">
                    <Radio className="h-4 w-4" />
                    <span>แผงรับสาย 5 คู่สาย</span>
                    <span className="cc-chip tone-idle">รับสายเข้าเท่านั้น</span>
                  </h2>
                  <div className="inline-flex flex-wrap items-center gap-1.5" aria-label="สถานะคู่สายทั้งหมด">
                    {state.lines.map((l) => (
                      <span key={l.lineId} className={`cc-rail tone-${lineTone(l)}`} title={`คู่สาย ${l.lineId}: ${lineStatusLabel(l)}`}>
                        <span className={`cc-led ${l.state === "ringing_in" ? "cc-led--blink" : ""}`} />
                        <span className="font-mono tabular-nums">L{l.lineId}</span>
                      </span>
                    ))}
                  </div>
                </div>

                <div className="space-y-3">
                  {state.lines.map((line) => (
                    <LineCard
                      key={line.lineId}
                      line={line}
                      isDark={isDark}
                      isActiveAudio={state.activeAudioLine === line.lineId}
                      nowMs={nowMs}
                      onAnswer={handleAnswer}
                      onReject={handleReject}
                      onHangup={(id) => sendCmd({ type: "hangup", lineId: id })}
                      onMute={(id) => sendCmd({ type: "mute", lineId: id })}
                      onHold={(id) => sendCmd({ type: "hold", lineId: id })}
                      onSelectAudio={(id) => sendCmd({ type: "setActiveAudioLine", lineId: id })}
                      onTakeOverAi={handleTakeOverAi}
                      onAttachAi={(id) => sendCmd({ type: "attachAi", lineId: id })}
                      onViewCallerHistory={(phone) => {
                        setCrmFocusPhone(phone);
                        viewCaller(phone);
                      }}
                      onChangeLanguage={(lineId, language) => {
                        sendCmd({ type: "setLineLanguage", lineId, language });
                        setToast(
                          language
                            ? `เปลี่ยนภาษาคู่สาย ${lineId} เป็น ${languageShortLabel(language)} และบันทึกลง CRM แล้ว`
                            : `ตั้งค่าภาษาคู่สาย ${lineId} เป็นอัตโนมัติแล้ว`
                        );
                      }}
                    />
                  ))}
                </div>

                <p className="inline-flex items-center gap-1.5 px-1 text-[11px] leading-none cc-dim">
                  <ShieldCheck className="h-3.5 w-3.5" />
                  <span>SIP UDP :</span>
                  <span className="font-mono tabular-nums">{state.trunk.port}</span>
                  <span>• WebRTC J.A.R.V.I.S Bridge • CRM Active</span>
                </p>
              </section>

              {/* Right: post-call QA, caller memory, recent calls */}
              <aside className="space-y-4 lg:col-span-5">
                <div className="cc-panel p-4">
                  <div className="cc-tabs mb-4" role="tablist">
                    <TabButton active={rightTab === "qa"} tone="ai" onClick={() => setRightTab("qa")} icon={<Sparkles className="h-3.5 w-3.5" />}>
                      <span>บันทึก QA</span>
                      <span className="tabular-nums">({callLogs.length})</span>
                    </TabButton>
                    <TabButton active={rightTab === "callers"} tone="live" onClick={() => setRightTab("callers")} icon={<Users className="h-3.5 w-3.5" />}>
                      <span>รายเบอร์</span>
                      <span className="tabular-nums">({callers.length})</span>
                    </TabButton>
                    <TabButton active={rightTab === "history"} tone="in" onClick={() => setRightTab("history")} icon={<History className="h-3.5 w-3.5" />}>
                      <span>สายเข้าล่าสุด</span>
                      <span className="tabular-nums">({state.history.length})</span>
                    </TabButton>
                  </div>

                  {rightTab === "qa" && <CallLogList logs={callLogs} onOpen={setSelectedLogId} />}
                  {rightTab === "callers" && (
                    <CallerMemory callers={callers} active={activeCallerRecord} onSelect={setSelectedCallerPhone} onOpenLog={setSelectedLogId} />
                  )}
                  {rightTab === "history" && <HistoryList history={state.history} onSelect={viewCaller} />}
                </div>
              </aside>
            </div>
          </>
        )}
      </div>

      {selectedLog && <CallQaDrawer log={selectedLog} onClose={closeDrawer} onReanalyze={handleReanalyze} />}
    </div>
  );
}

/* ===================================================================== */

function CommandAlertHero({
  line,
  isRinging,
  queuedCount,
  autoAnswerAi,
  nowMs,
  onAnswer,
  onReject,
  onTakeOver,
}: {
  line: SipLine;
  isRinging: boolean;
  queuedCount: number;
  autoAnswerAi: boolean;
  nowMs: number;
  onAnswer: (lineId: number) => void;
  onReject: (lineId: number) => void;
  onTakeOver: (lineId: number) => void;
}) {
  const tone = isRinging ? "in" : "ai";
  const returning = line.callerHasHistory || (line.callerTotalCalls ?? 0) > 1;
  return (
    <section
      key={`${tone}-${line.lineId}`}
      aria-live="assertive"
      className={`cc-hero tone-${tone} ${isRinging ? "cc-hero--alert" : ""} mb-5 px-5 py-5 sm:px-7`}
    >
      <span className="cc-hero__scan" aria-hidden="true" />
      <span className="cc-beam" aria-hidden="true" />
      <span className="cc-brackets" aria-hidden="true" />

      <div className="relative flex flex-wrap items-center gap-6">
        <SignalRadar tone={tone} mode="alert" className="h-28 w-28" coreClassName="h-14 w-14">
          {isRinging ? <PhoneIncoming className="h-6 w-6 cc-float" /> : <Bot className="h-6 w-6" />}
        </SignalRadar>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="cc-chip">
              <span className="cc-led cc-led--blink" />
              <span>{isRinging ? "แจ้งเตือนสายเรียกเข้า" : "J.A.R.V.I.S กำลังสนทนา"}</span>
            </span>
            <span className="cc-chip tone-idle">
              <span>คู่สาย</span>
              <span className="font-mono tabular-nums">{line.lineId}</span>
            </span>
            {queuedCount > 0 && (
              <span className="cc-chip tone-held">
                <span>รอคิวอีก</span>
                <span className="tabular-nums">{queuedCount}</span>
                <span>สาย</span>
              </span>
            )}
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-4">
            <span className="font-mono text-3xl font-bold leading-none tracking-wide tabular-nums cc-text sm:text-4xl">
              {formatPhone(line.remoteNumber)}
            </span>
            <AudioEqualizer bars={16} className="h-8" />
          </div>
          {(line.crmName || line.crmCompany || line.remoteName) && (
            <div className="mt-1.5 flex flex-wrap items-center gap-2 text-sm leading-none cc-muted">
              <span>{[line.crmName || line.remoteName, line.crmCompany].filter(Boolean).join(" • ")}</span>
              {line.crmTier === "vip" && <span className="cc-chip tone-ai">VIP</span>}
              {line.crmTier === "at_risk" && <span className="cc-chip tone-danger">ต้องดูแลพิเศษ</span>}
              {(line.crmOpenTickets ?? 0) > 0 && (
                <span className="cc-chip tone-danger">เคสค้าง {line.crmOpenTickets}</span>
              )}
            </div>
          )}

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <span className="cc-chip tone-idle font-mono tabular-nums">
              <Timer className="h-3 w-3" />
              <span>
                {isRinging ? "รอสาย " : "คุยแล้ว "}
                {formatDuration(line.connectedAt || line.startedAt, nowMs)}
              </span>
            </span>
            <span className={`cc-chip ${returning ? "tone-held" : "tone-idle"}`}>
              <History className="h-3 w-3" />
              {returning ? (
                <>
                  <span>ลูกค้าเก่า • ครั้งที่</span>
                  <span className="tabular-nums">{line.callerTotalCalls}</span>
                </>
              ) : (
                <span>สายใหม่ ยังไม่มีประวัติ</span>
              )}
            </span>
            <span className={`cc-chip ${line.language ? "tone-live" : "tone-held"}`}>
              <span>🌐 {languageShortLabel(line.language)}</span>
            </span>
            {isRinging && autoAnswerAi && (
              <span className="cc-chip tone-ai">
                <Bot className="h-3 w-3" />
                <span>J.A.R.V.I.S จะรับสายอัตโนมัติ</span>
              </span>
            )}
          </div>

          {!isRinging && line.aiTranscript && (
            <p className="cc-quote tone-ai mt-3 px-3 py-2 text-sm leading-relaxed">
              <span className="cc-caret">{line.aiTranscript}</span>
            </p>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          {isRinging ? (
            <>
              <button type="button" onClick={() => onAnswer(line.lineId)} className="cc-btn cc-btn--solid cc-btn--xl tone-in">
                <Phone className="h-4 w-4" />
                <span>กดรับสายทันที</span>
              </button>
              <button type="button" onClick={() => onReject(line.lineId)} className="cc-btn cc-btn--tone cc-btn--xl tone-danger">
                <PhoneOff className="h-4 w-4" />
                <span>ปฏิเสธสาย</span>
              </button>
            </>
          ) : (
            <button type="button" onClick={() => onTakeOver(line.lineId)} className="cc-btn cc-btn--solid cc-btn--xl tone-in">
              <UserCheck className="h-4 w-4" />
              <span>รับสายแทน AI</span>
            </button>
          )}
        </div>
      </div>
    </section>
  );
}

function StandbyStrip({ busyCount, autoAnswerAi }: { busyCount: number; autoAnswerAi: boolean }) {
  return (
    <section className="cc-hero tone-live mb-5 px-5 py-3.5">
      <span className="cc-brackets" aria-hidden="true" />
      <div className="relative flex flex-wrap items-center justify-between gap-4">
        <div className="flex min-w-0 items-center gap-4">
          <SignalRadar tone="live" mode="scan" className="h-14 w-14" coreClassName="h-7 w-7">
            <ShieldCheck className="h-3.5 w-3.5" />
          </SignalRadar>
          <div className="min-w-0">
            <div className="inline-flex flex-wrap items-center gap-2 text-sm font-semibold leading-none cc-text">
              <span className="cc-led" />
              {busyCount > 0 ? (
                <>
                  <span>กำลังให้บริการ</span>
                  <span className="font-mono tabular-nums">{busyCount}</span>
                  <span>สาย • เฝ้าระวังคู่สายว่าง</span>
                </>
              ) : (
                <span>สแตนด์บาย • เฝ้าระวังสายเข้าทั้ง 5 คู่สาย</span>
              )}
            </div>
            <p className="mt-1.5 text-xs leading-none cc-muted">
              {autoAnswerAi
                ? "เมื่อมีสายเข้า J.A.R.V.I.S จะรับสายอัตโนมัติ"
                : "เมื่อมีสายเข้า ระบบจะส่งเสียงเรียกและแจ้งเตือนให้กดรับสาย"}
            </p>
          </div>
        </div>
        <AudioEqualizer bars={24} active={false} className="hidden h-6 sm:inline-flex" />
      </div>
    </section>
  );
}

function KpiTile({
  tone,
  label,
  value,
  suffix,
  meter,
  icon,
}: {
  tone: "in" | "live" | "ai" | "held";
  label: string;
  value: number;
  suffix?: string;
  meter?: number;
  icon: ReactNode;
}) {
  return (
    <div className={`cc-panel tone-${tone} flex items-center justify-between gap-3 px-4 py-3`}>
      <div className="min-w-0">
        <div className="text-xs leading-none cc-muted">{label}</div>
        <div className="mt-2 inline-flex items-baseline gap-1 font-mono leading-none tabular-nums">
          <span className="text-2xl font-bold cc-ink">{value}</span>
          {suffix && <span className="text-sm cc-dim">{suffix}</span>}
        </div>
        {meter !== undefined && (
          <div className="cc-meter" aria-hidden="true">
            {Array.from({ length: 5 }, (_, i) => (
              <span key={i} className={`cc-meter__seg ${i < meter ? "is-on" : ""}`} />
            ))}
          </div>
        )}
      </div>
      <span className="cc-icon-tile">{icon}</span>
    </div>
  );
}

function TabButton({
  active,
  tone,
  onClick,
  icon,
  children,
}: {
  active: boolean;
  tone: "ai" | "live" | "in";
  onClick: () => void;
  icon: ReactNode;
  children: ReactNode;
}) {
  return (
    <button type="button" role="tab" aria-selected={active} onClick={onClick} className={`cc-tab tone-${tone} ${active ? "is-active" : ""}`}>
      {icon}
      {children}
    </button>
  );
}

function CallLogList({ logs, onOpen }: { logs: CallLogEntry[]; onOpen: (sessionId: string) => void }) {
  if (logs.length === 0) {
    return (
      <div className="cc-empty px-4 py-10 text-center text-xs">
        ยังไม่มีบันทึกหลังสาย — เมื่อวางสาย ระบบจะบันทึกบทสนทนาและให้ DeepSeek วิเคราะห์คุณภาพสายให้อัตโนมัติ
      </div>
    );
  }
  return (
    <div className="cc-scroll max-h-[34rem] space-y-2 overflow-y-auto pr-1">
      {logs.map((log) => {
        const status = callStatusMeta(log.status);
        const sentiment = sentimentMeta(log.analysis?.sentiment);
        const csat = log.analysis?.csatScore;
        return (
          <button key={log.sessionId} type="button" onClick={() => onOpen(log.sessionId)} className={`cc-log tone-${status.tone}`}>
            <span className="cc-icon-tile h-9 w-9">
              {log.status === "active" ? <PhoneCall className="h-3.5 w-3.5" /> : <PhoneIncoming className="h-3.5 w-3.5" />}
            </span>
            <span className="min-w-0 flex-1">
              <span className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-sm font-semibold leading-none tabular-nums cc-text">{formatPhone(log.phone)}</span>
                <span className="text-[11px] leading-none tabular-nums cc-dim">{formatDateTime(log.startedAt)}</span>
              </span>
              <span className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[11px] leading-none cc-muted">
                <span>{status.label}</span>
                <span>•</span>
                <span className="font-mono tabular-nums">{formatSeconds(log.durationSec)}</span>
                <span>•</span>
                <span className="tabular-nums">{log.messages.length} ข้อความ</span>
              </span>
              {log.analysis?.topic && <span className="mt-1.5 block truncate text-xs leading-snug cc-text">{log.analysis.topic}</span>}
            </span>
            <span className="inline-flex shrink-0 items-center gap-1.5">
              {log.analysisStatus === "analyzing" ? (
                <span className="cc-chip tone-ai">
                  <AudioEqualizer bars={4} className="h-3" />
                  <span>กำลังวิเคราะห์</span>
                </span>
              ) : log.analysis && !log.analysis.error ? (
                <>
                  {sentiment && <span className={`cc-led tone-${sentiment.tone}`} title={sentiment.label} />}
                  <span className={`cc-chip tone-${scoreTone(csat)} font-mono tabular-nums`} title="CSAT">
                    CSAT {formatScore(csat)}
                  </span>
                </>
              ) : (
                <span className={`cc-chip ${log.analysisStatus === "error" || log.analysis?.error ? "tone-danger" : "tone-idle"}`}>
                  {log.analysisStatus === "error" || log.analysis?.error ? "วิเคราะห์ไม่สำเร็จ" : "ยังไม่วิเคราะห์"}
                </span>
              )}
              <ChevronRight className="h-4 w-4 cc-dim" />
            </span>
          </button>
        );
      })}
    </div>
  );
}

function CallerMemory({
  callers,
  active,
  onSelect,
  onOpenLog,
}: {
  callers: CallerRecord[];
  active: CallerRecord | null;
  onSelect: (phone: string) => void;
  onOpenLog: (sessionId: string) => void;
}) {
  if (callers.length === 0 || !active) {
    return (
      <div className="cc-empty px-4 py-10 text-center text-xs">
        ยังไม่มีประวัติสนทนา — เมื่อมีสายเข้า ระบบจะบันทึกบทสนทนาของเบอร์นั้นให้อัตโนมัติเพื่อคุยต่อเนื่องในครั้งถัดไป
      </div>
    );
  }
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1.5">
        {callers.map((c) => (
          <button
            key={c.phone}
            type="button"
            onClick={() => onSelect(c.phone)}
            className={`cc-btn h-8 rounded-lg px-2.5 font-mono tabular-nums ${active.phone === c.phone ? "cc-btn--tone tone-live" : ""}`}
          >
            <span>{formatPhone(c.phone)}</span>
            <span className="opacity-70">({c.totalCalls})</span>
          </button>
        ))}
      </div>

      <div className="cc-inset p-3.5">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div className="min-w-0">
            <div className="font-mono text-sm font-bold leading-none tabular-nums cc-text">{formatPhone(active.phone)}</div>
            <div className="mt-1.5 inline-flex flex-wrap items-center gap-1 text-[11px] leading-none cc-dim">
              <span>โทรมาแล้ว</span>
              <span className="tabular-nums">{active.totalCalls}</span>
              <span>ครั้ง • ล่าสุด</span>
              <span className="tabular-nums">{formatDateTime(active.lastCallAt)}</span>
            </div>
          </div>
          <span className="cc-chip tone-ai">
            <Bot className="h-3 w-3" />
            <span>จำบริบทต่อเนื่อง</span>
          </span>
        </div>

        <div className="cc-scroll max-h-80 space-y-3 overflow-y-auto pr-1">
          {active.sessions.map((sess) => (
            <div key={sess.id} className="space-y-1.5">
              <div className="flex items-center justify-between gap-2">
                <span className="inline-flex items-center gap-1 text-[11px] font-medium leading-none cc-dim">
                  <span>รอบสนทนา</span>
                  <span className="tabular-nums">{formatDateTime(sess.startedAt)}</span>
                </span>
                <button type="button" onClick={() => onOpenLog(sess.id)} className="cc-chip tone-ai">
                  <Sparkles className="h-3 w-3" />
                  <span>ดูผล QA</span>
                </button>
              </div>
              {sess.messages.length === 0 ? (
                <div className="text-[11px] italic cc-dim">ไม่มีข้อความถอดเสียงในรอบนี้</div>
              ) : (
                sess.messages.map((m, idx) => (
                  <div
                    key={`${sess.id}-${idx}`}
                    className={`px-2.5 py-1.5 text-xs leading-relaxed ${
                      m.role === "caller" ? "cc-bubble" : `cc-quote ${m.role === "agent" ? "tone-live" : "tone-ai"}`
                    }`}
                  >
                    <span className="font-semibold">
                      {m.role === "caller" ? "ผู้โทร: " : m.role === "agent" ? "เจ้าหน้าที่: " : "J.A.R.V.I.S: "}
                    </span>
                    <span>{m.text}</span>
                  </div>
                ))
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function HistoryList({ history, onSelect }: { history: CallHistoryEntry[]; onSelect: (phone: string) => void }) {
  if (history.length === 0) {
    return <div className="cc-empty px-4 py-10 text-center text-xs">ยังไม่มีประวัติสายเข้าในรอบนี้</div>;
  }
  return (
    <div className="cc-scroll max-h-[34rem] space-y-2 overflow-y-auto pr-1">
      {history.map((item) => {
        const status = callStatusMeta(item.status);
        const isMissed = status.tone === "danger";
        return (
          <button key={item.id} type="button" onClick={() => onSelect(item.number)} className={`cc-log tone-${status.tone}`}>
            <span className="cc-icon-tile h-8 w-8">
              {isMissed ? <PhoneMissed className="h-3.5 w-3.5" /> : <PhoneIncoming className="h-3.5 w-3.5" />}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block font-mono text-xs font-semibold leading-none tabular-nums cc-text">{formatPhone(item.number)}</span>
              <span className="mt-1.5 flex flex-wrap items-center gap-1 text-[11px] leading-none cc-dim">
                <span className="tabular-nums">{formatDateTime(item.at)}</span>
                <span>•</span>
                {item.status === "answered" ? (
                  <>
                    <span>คุย</span>
                    <span className="font-mono tabular-nums">{formatSeconds(item.durationSec)}</span>
                  </>
                ) : (
                  <span>{status.label}</span>
                )}
              </span>
            </span>
            <Activity className="h-3.5 w-3.5 cc-dim" />
          </button>
        );
      })}
    </div>
  );
}
