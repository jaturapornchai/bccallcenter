export type LineState =
  | "idle"
  | "ringing_in"
  | "dialing"
  | "ringing_out"
  | "connected"
  | "held";

export interface LiveCallSignal {
  sentiment: "positive" | "neutral" | "frustrated";
  buyingIntent: "high" | "medium" | "none";
  label: string;
  tone: "in" | "held" | "danger" | "idle";
  updatedAt: string;
}

export interface SipLine {
  lineId: number;
  state: LineState;
  direction: "inbound" | "outbound" | null;
  remoteNumber: string;
  remoteName: string;
  startedAt: number | null;
  connectedAt: number | null;
  muted: boolean;
  held: boolean;
  aiActive: boolean;
  aiTranscript: string;
  aiPartial?: string;
  aiStatus?: string;
  liveMessages?: CallerMessage[];
  liveSignal?: LiveCallSignal | null;
  callerTotalCalls?: number;
  callerHasHistory?: boolean;
  sessionId?: string | null;
  crmName?: string;
  crmCompany?: string;
  crmTier?: CrmTier | null;
  crmOpenTickets?: number;
  language?: string | null;
  isNewCaller?: boolean;
}

export const SUPPORTED_LANGUAGES: { code: string; label: string; short: string }[] = [
  { code: "th", label: "ภาษาไทย (Thai)", short: "ไทย" },
  { code: "en", label: "English", short: "EN" },
  { code: "zh", label: "中文 (จีน)", short: "中文" },
  { code: "ja", label: "日本語 (ญี่ปุ่น)", short: "日本語" },
  { code: "ko", label: "한국어 (เกาหลี)", short: "한국어" },
  { code: "lo", label: "ລາວ (ลาว)", short: "ลาว" },
  { code: "my", label: "မြန်မာ (เมียนมา)", short: "เมียนมา" },
  { code: "vi", label: "Tiếng Việt (เวียดนาม)", short: "VN" },
  { code: "km", label: "ខ្មែរ (กัมพูชา)", short: "KH" },
];

export function languageShortLabel(code?: string | null): string {
  if (!code) return "รอเลือกภาษา";
  return SUPPORTED_LANGUAGES.find((l) => l.code === code)?.short || code.toUpperCase();
}

export type CrmTier = "new" | "regular" | "vip" | "at_risk";
export type CrmStage = "lead" | "prospect" | "customer" | "churn_risk";
export type TicketStatus = "open" | "in_progress" | "waiting_customer" | "resolved" | "closed";
export type TicketPriority = "low" | "medium" | "high" | "urgent";

export interface CrmCustomer {
  phone: string;
  name: string;
  company: string;
  tier: CrmTier;
  stage: CrmStage;
  preferredLanguage?: string | null;
  tags: string[];
  notes: string;
  avgCsat: number | null;
  csatCount: number;
  lastSentiment: "positive" | "neutral" | "negative" | "urgent" | null;
  totalCalls: number;
  createdAt: string;
  updatedAt: string;
  lastCallAt: string;
}

export interface CrmTicket {
  id: string;
  phone: string;
  customerName: string;
  company: string;
  subject: string;
  description: string;
  status: TicketStatus;
  priority: TicketPriority;
  assignee: string;
  source: "ai_auto" | "manual";
  sessionId: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface CrmStats {
  totalCustomers: number;
  vipOrRisk: number;
  openTickets: number;
  urgentTickets: number;
  avgCsat: number | null;
}

export interface CrmState {
  customers: CrmCustomer[];
  tickets: CrmTicket[];
  stats: CrmStats;
}

export interface CallerMessage {
  at: string;
  role: "caller" | "ai" | "agent";
  text: string;
}

export interface CallAnalysis {
  analyzedAt: string;
  model: string;
  csatScore?: number;
  aiQualityScore?: number;
  sentiment?: "positive" | "neutral" | "negative" | "urgent";
  emotionLabel?: string;
  emotionTrend?: string;
  topic?: string;
  summary?: string;
  resolved?: boolean;
  resolutionSteps?: string;
  needsHumanFollowUp?: boolean;
  urgency?: "low" | "medium" | "high";
  buyingIntent?: "high" | "medium" | "low";
  interestedModules?: string[];
  followUpAction?: string;
  followUpMessage?: string;
  aiCoachingTip?: string;
  error?: string;
}

export interface CallerSession {
  id: string;
  startedAt: string;
  endedAt: string | null;
  durationSec?: number;
  status?: string;
  direction: "inbound" | "outbound";
  messages: CallerMessage[];
  hasRecording?: boolean;
  recordingDurationSec?: number;
  analysisStatus?: "idle" | "analyzing" | "done" | "error";
  analysis?: CallAnalysis | null;
}

export interface CallerRecord {
  phone: string;
  totalCalls: number;
  lastCallAt: string;
  lastDirection: "inbound" | "outbound";
  sessions: CallerSession[];
}

export interface CallLogEntry {
  phone: string;
  callerTotalCalls: number;
  sessionId: string;
  startedAt: string;
  endedAt: string | null;
  durationSec: number;
  status: string;
  direction: "inbound" | "outbound";
  messages: CallerMessage[];
  hasRecording?: boolean;
  recordingDurationSec?: number;
  analysisStatus: "idle" | "analyzing" | "done" | "error";
  analysis: CallAnalysis | null;
}

export interface TrunkStatus {
  status: "connecting" | "registered" | "error";
  host: string;
  ip: string | null;
  port: number;
  user: string;
  lastRegisterAt: string | null;
  expires: number;
  error: string | null;
}

export interface CallHistoryEntry {
  id: string;
  at: string;
  direction: "inbound" | "outbound";
  number: string;
  status: "answered" | "missed" | "rejected" | "cancelled" | "failed" | "busy";
  durationSec: number;
}

export interface ConsoleState {
  trunk: TrunkStatus;
  autoAnswerAi: boolean;
  jarvisUrl: string;
  activeAudioLine: number | null;
  lines: SipLine[];
  history: CallHistoryEntry[];
  callers?: CallerRecord[];
  callLogs?: CallLogEntry[];
  crm?: CrmState;
}
