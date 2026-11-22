export type LineState =
  | "idle"
  | "ringing_in"
  | "dialing"
  | "ringing_out"
  | "connected"
  | "held";

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
  callerTotalCalls?: number;
  callerHasHistory?: boolean;
  sessionId?: string | null;
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
  followUpAction?: string;
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
}
