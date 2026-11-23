"use client";

import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Bot,
  Briefcase,
  Building2,
  CheckCircle2,
  ClipboardList,
  Crown,
  Phone,
  PhoneCall,
  Plus,
  Save,
  Search,
  Sparkles,
  Tag,
  Trash2,
  UserCheck,
  UserPlus,
  Users,
} from "lucide-react";
import { formatDateTime, formatPhone, formatSeconds } from "@/components/LineCard";
import { sentimentMeta } from "@/components/CallQaDrawer";
import { SUPPORTED_LANGUAGES, languageShortLabel } from "@/types/sip";
import type {
  CallLogEntry,
  CrmCustomer,
  CrmStage,
  CrmState,
  CrmTier,
  TicketPriority,
  TicketStatus,
} from "@/types/sip";

interface CrmWorkspaceProps {
  crm: CrmState;
  callLogs: CallLogEntry[];
  initialPhone?: string | null;
  initialSubTab?: "customers" | "tickets";
  onSubTabChange?: (tab: "customers" | "tickets") => void;
  onUpsertCustomer: (customer: Partial<CrmCustomer> & { phone: string }) => void;
  onCreateTicket: (ticket: {
    phone: string;
    customerName?: string;
    company?: string;
    subject: string;
    description: string;
    priority: TicketPriority;
    assignee: string;
  }) => void;
  onUpdateTicket: (id: string, patch: { status?: TicketStatus; priority?: TicketPriority; assignee?: string }) => void;
  onAcknowledgeTicket?: (id: string) => void;
  onCloseTicket?: (id: string) => void;
  onDeleteTicket: (id: string) => void;
  onOpenQaLog: (phone: string, sessionId: string) => void;
  readOnly?: boolean;
}

const TIER_META: Record<CrmTier, { label: string; tone: string }> = {
  new: { label: "ลูกค้าใหม่", tone: "tone-live" },
  regular: { label: "ลูกค้าประจำ", tone: "tone-in" },
  vip: { label: "VIP", tone: "tone-ai" },
  at_risk: { label: "ต้องดูแลพิเศษ", tone: "tone-danger" },
};

const STAGE_LABEL: Record<CrmStage, string> = {
  lead: "ผู้สนใจ (Lead)",
  prospect: "กำลังตัดสินใจ",
  customer: "ลูกค้าปัจจุบัน",
  churn_risk: "เสี่ยงหลุด",
};

const STATUS_META: Record<TicketStatus, { label: string; tone: string }> = {
  open: { label: "เปิดเคสใหม่", tone: "tone-danger" },
  in_progress: { label: "กำลังดำเนินการ", tone: "tone-live" },
  waiting_customer: { label: "รอลูกค้า", tone: "tone-held" },
  resolved: { label: "แก้ไขแล้ว", tone: "tone-in" },
  closed: { label: "ปิดเคส", tone: "tone-idle" },
};

const PRIORITY_META: Record<TicketPriority, { label: string; tone: string }> = {
  urgent: { label: "ด่วนมาก", tone: "tone-danger" },
  high: { label: "สูง", tone: "tone-held" },
  medium: { label: "ปกติ", tone: "tone-live" },
  low: { label: "ต่ำ", tone: "tone-idle" },
};

export function CrmWorkspace({
  crm,
  callLogs,
  initialPhone,
  initialSubTab,
  onSubTabChange,
  onUpsertCustomer,
  onCreateTicket,
  onUpdateTicket,
  onAcknowledgeTicket,
  onCloseTicket,
  onDeleteTicket,
  onOpenQaLog,
  readOnly = false,
}: CrmWorkspaceProps) {
  const [subTab, setSubTab] = useState<"customers" | "tickets">(initialSubTab || "customers");
  useEffect(() => {
    if (initialSubTab) setSubTab(initialSubTab);
  }, [initialSubTab]);
  const [search, setSearch] = useState("");
  const [tierFilter, setTierFilter] = useState<CrmTier | "all">("all");
  const [statusFilter, setStatusFilter] = useState<TicketStatus | "all">("all");
  const [selectedPhone, setSelectedPhone] = useState<string | null>(initialPhone || crm.customers[0]?.phone || null);
  useEffect(() => {
    if (initialPhone) {
      setSelectedPhone(initialPhone);
      return;
    }
    if (!selectedPhone && crm.customers[0]?.phone) {
      setSelectedPhone(crm.customers[0].phone);
    } else if (selectedPhone && crm.customers.length > 0 && !crm.customers.some((c) => c.phone === selectedPhone)) {
      setSelectedPhone(crm.customers[0].phone);
    }
  }, [initialPhone, crm.customers, selectedPhone]);
  const [showNewCustomer, setShowNewCustomer] = useState(false);
  const [showNewTicket, setShowNewTicket] = useState(false);
  const [showAllCustomers, setShowAllCustomers] = useState(false);
  const [showAllCustTickets, setShowAllCustTickets] = useState(false);
  const [showAllCustLogs, setShowAllCustLogs] = useState(false);

  // New / Edit Customer Form state
  const [editDraft, setEditDraft] = useState<{
    phone: string;
    name: string;
    company: string;
    tier: CrmTier;
    stage: CrmStage;
    preferredLanguage: string;
    tagsText: string;
    notes: string;
  } | null>(null);

  // New Ticket Form state
  const [ticketForm, setTicketForm] = useState({
    phone: "",
    subject: "",
    description: "",
    priority: "medium" as TicketPriority,
    assignee: "ทีมดูแลลูกค้า",
  });

  const filteredCustomers = useMemo(() => {
    const q = search.trim().toLowerCase();
    return crm.customers.filter((c) => {
      if (tierFilter !== "all" && c.tier !== tierFilter) return false;
      if (!q) return true;
      return (
        c.phone.toLowerCase().includes(q) ||
        c.name.toLowerCase().includes(q) ||
        c.company.toLowerCase().includes(q) ||
        c.tags.some((t) => t.toLowerCase().includes(q))
      );
    });
  }, [crm.customers, search, tierFilter]);

  const activeCustomer = useMemo(() => {
    const target = selectedPhone || initialPhone;
    if (target) {
      const found = crm.customers.find((c) => c.phone === target);
      if (found) return found;
    }
    return filteredCustomers[0] || crm.customers[0] || null;
  }, [crm.customers, filteredCustomers, initialPhone, selectedPhone]);

  const activeDraft = useMemo(() => {
    if (editDraft && (!activeCustomer || editDraft.phone === activeCustomer.phone || showNewCustomer)) {
      return editDraft;
    }
    if (!activeCustomer) return null;
    return {
      phone: activeCustomer.phone,
      name: activeCustomer.name,
      company: activeCustomer.company,
      tier: activeCustomer.tier,
      stage: activeCustomer.stage,
      preferredLanguage: activeCustomer.preferredLanguage || "",
      tagsText: activeCustomer.tags.join(", "),
      notes: activeCustomer.notes,
    };
  }, [activeCustomer, editDraft, showNewCustomer]);

  const customerTickets = useMemo(() => {
    if (!activeCustomer) return [];
    return crm.tickets.filter((t) => t.phone === activeCustomer.phone);
  }, [activeCustomer, crm.tickets]);

  const customerLogs = useMemo(() => {
    if (!activeCustomer) return [];
    return callLogs.filter((l) => l.phone === activeCustomer.phone);
  }, [activeCustomer, callLogs]);

  const filteredTickets = useMemo(() => {
    const q = search.trim().toLowerCase();
    return crm.tickets.filter((t) => {
      if (statusFilter !== "all" && t.status !== statusFilter) return false;
      if (!q) return true;
      return (
        t.id.toLowerCase().includes(q) ||
        t.subject.toLowerCase().includes(q) ||
        t.phone.toLowerCase().includes(q) ||
        t.customerName.toLowerCase().includes(q) ||
        t.company.toLowerCase().includes(q)
      );
    });
  }, [crm.tickets, search, statusFilter]);

  const handleSaveCustomer = () => {
    if (!activeDraft || !activeDraft.phone.trim()) return;
    onUpsertCustomer({
      phone: activeDraft.phone.trim(),
      name: activeDraft.name.trim(),
      company: activeDraft.company.trim(),
      tier: activeDraft.tier,
      stage: activeDraft.stage,
      preferredLanguage: activeDraft.preferredLanguage || null,
      tags: activeDraft.tagsText
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
      notes: activeDraft.notes.trim(),
    });
    setSelectedPhone(activeDraft.phone.replace(/[^\d+]/g, ""));
    setShowNewCustomer(false);
    setEditDraft(null);
  };

  const handleSubmitTicket = () => {
    if (!ticketForm.subject.trim()) return;
    const cust = crm.customers.find((c) => c.phone === ticketForm.phone.replace(/[^\d+]/g, ""));
    onCreateTicket({
      phone: ticketForm.phone.trim(),
      customerName: cust?.name || "",
      company: cust?.company || "",
      subject: ticketForm.subject.trim(),
      description: ticketForm.description.trim(),
      priority: ticketForm.priority,
      assignee: ticketForm.assignee.trim() || "ทีมดูแลลูกค้า",
    });
    setTicketForm({ phone: "", subject: "", description: "", priority: "medium", assignee: "ทีมดูแลลูกค้า" });
    setShowNewTicket(false);
    setSubTab("tickets");
  };

  const inputCls =
    "w-full rounded-xl border border-[var(--cc-border)] bg-[var(--cc-inset)] px-3 py-2 text-xs cc-text outline-none focus:border-[var(--cc-border-strong)]";

  return (
    <div className="space-y-5">
      {/* CRM KPI Strip */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div className="cc-panel tone-live flex items-center justify-between px-4 py-3.5">
          <div>
            <div className="text-xs cc-muted">ลูกค้าในระบบ CRM</div>
            <div className="mt-1 font-mono text-2xl font-bold tabular-nums cc-text">{crm.stats.totalCustomers}</div>
          </div>
          <span className="cc-chip tone-live h-9 w-9 justify-center rounded-xl p-0">
            <Users className="h-4 w-4" />
          </span>
        </div>

        <div className="cc-panel tone-ai flex items-center justify-between px-4 py-3.5">
          <div>
            <div className="text-xs cc-muted">VIP / ต้องดูแลพิเศษ</div>
            <div className="mt-1 font-mono text-2xl font-bold tabular-nums cc-ink">{crm.stats.vipOrRisk}</div>
          </div>
          <span className="cc-chip tone-ai h-9 w-9 justify-center rounded-xl p-0">
            <Crown className="h-4 w-4" />
          </span>
        </div>

        <div className="cc-panel tone-danger flex items-center justify-between px-4 py-3.5">
          <div>
            <div className="text-xs cc-muted">เคสเปิดค้าง</div>
            <div className="mt-1 flex items-baseline gap-2">
              <span className="font-mono text-2xl font-bold tabular-nums cc-ink">{crm.stats.openTickets}</span>
              {crm.stats.urgentTickets > 0 && (
                <span className="text-[11px] font-medium cc-ink">({crm.stats.urgentTickets} ด่วน)</span>
              )}
            </div>
          </div>
          <span className="cc-chip tone-danger h-9 w-9 justify-center rounded-xl p-0">
            <AlertTriangle className="h-4 w-4" />
          </span>
        </div>

        <div className="cc-panel tone-in flex items-center justify-between px-4 py-3.5">
          <div>
            <div className="text-xs cc-muted">CSAT เฉลี่ยทั้งศูนย์</div>
            <div className="mt-1 font-mono text-2xl font-bold tabular-nums cc-ink">
              {crm.stats.avgCsat !== null ? `${crm.stats.avgCsat}/10` : "—"}
            </div>
          </div>
          <span className="cc-chip tone-in h-9 w-9 justify-center rounded-xl p-0">
            <Sparkles className="h-4 w-4" />
          </span>
        </div>
      </div>

      {/* Toolbar: Sub-tabs + Search + Action Buttons */}
      <div className="cc-panel flex flex-wrap items-center justify-between gap-3 px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => {
              setSubTab("customers");
              onSubTabChange?.("customers");
            }}
            className={`cc-btn h-9 ${subTab === "customers" ? "cc-btn--tone tone-ai" : ""}`}
          >
            <Users className="h-3.5 w-3.5" />
            <span>ทำเนียบลูกค้า 360° ({crm.customers.length})</span>
          </button>
          <button
            type="button"
            onClick={() => {
              setSubTab("tickets");
              onSubTabChange?.("tickets");
            }}
            className={`cc-btn h-9 ${subTab === "tickets" ? "cc-btn--tone tone-live" : ""}`}
          >
            <ClipboardList className="h-3.5 w-3.5" />
            <span>บอร์ดติดตามเคส ({crm.tickets.length})</span>
          </button>
        </div>

        <div className="flex flex-1 flex-wrap items-center justify-end gap-2">
          <div className="relative min-w-[210px] max-w-xs flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 cc-dim" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="ค้นหาชื่อ, เบอร์โทร, บริษัท, รหัสเคส..."
              className="h-9 w-full rounded-xl border border-[var(--cc-border)] bg-[var(--cc-inset)] pl-8 pr-3 text-xs cc-text outline-none focus:border-[var(--cc-border-strong)]"
            />
          </div>

          {!readOnly && (
            <>
              <button
                type="button"
                onClick={() => {
                  setSubTab("customers");
                  setShowNewCustomer(true);
                  setEditDraft({
                    phone: "",
                    name: "",
                    company: "",
                    tier: "new",
                    stage: "lead",
                    preferredLanguage: "",
                    tagsText: "",
                    notes: "",
                  });
                }}
                className="cc-btn cc-btn--tone tone-in h-9"
              >
                <UserPlus className="h-3.5 w-3.5" />
                <span>เพิ่มลูกค้า</span>
              </button>

              <button
                type="button"
                onClick={() => {
                  setTicketForm((prev) => ({ ...prev, phone: activeCustomer?.phone || prev.phone }));
                  setShowNewTicket((v) => !v);
                }}
                className="cc-btn cc-btn--solid tone-live h-9"
              >
                <Plus className="h-3.5 w-3.5" />
                <span>สร้างเคสใหม่</span>
              </button>
            </>
          )}
        </div>
      </div>

      {/* Inline Create Ticket Box */}
      {showNewTicket && (
        <div className="cc-panel space-y-3 p-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold cc-text">สร้างเคสติดตามงานใหม่ (CRM Ticket)</h3>
            <button type="button" onClick={() => setShowNewTicket(false)} className="cc-btn h-7 px-2.5 text-xs">
              ปิด
            </button>
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <input
              className={inputCls}
              placeholder="เบอร์โทรลูกค้า เช่น 0812345678"
              value={ticketForm.phone}
              onChange={(e) => setTicketForm({ ...ticketForm, phone: e.target.value })}
            />
            <select
              className={inputCls}
              value={ticketForm.priority}
              onChange={(e) => setTicketForm({ ...ticketForm, priority: e.target.value as TicketPriority })}
            >
              <option value="urgent">ความเร่งด่วน: ด่วนมาก</option>
              <option value="high">ความเร่งด่วน: สูง</option>
              <option value="medium">ความเร่งด่วน: ปกติ</option>
              <option value="low">ความเร่งด่วน: ต่ำ</option>
            </select>
            <input
              className={inputCls}
              placeholder="ผู้รับผิดชอบ (เช่น ฝ่ายขาย / ฝ่ายบัญชี)"
              value={ticketForm.assignee}
              onChange={(e) => setTicketForm({ ...ticketForm, assignee: e.target.value })}
            />
          </div>
          <input
            className={inputCls}
            placeholder="หัวข้อเคส (จำเป็น) เช่น ขอใบเสนอราคาแพ็กเกจ Standard"
            value={ticketForm.subject}
            onChange={(e) => setTicketForm({ ...ticketForm, subject: e.target.value })}
          />
          <textarea
            rows={2}
            className={inputCls}
            placeholder="รายละเอียด / สิ่งที่ต้องดำเนินการต่อ..."
            value={ticketForm.description}
            onChange={(e) => setTicketForm({ ...ticketForm, description: e.target.value })}
          />
          <div className="flex justify-end">
            <button type="button" onClick={handleSubmitTicket} className="cc-btn cc-btn--solid tone-in h-9 px-4">
              <CheckCircle2 className="h-3.5 w-3.5" />
              <span>บันทึกเคส</span>
            </button>
          </div>
        </div>
      )}

      {/* TAB 1: Customer 360° Directory */}
      {subTab === "customers" && (
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-12">
          {/* Left: Customer List */}
          <div className="cc-panel space-y-3 p-4 lg:col-span-5">
            <div className="flex flex-wrap items-center gap-1.5">
              {(["all", "vip", "at_risk", "regular", "new"] as const).map((tier) => (
                <button
                  key={tier}
                  type="button"
                  onClick={() => setTierFilter(tier)}
                  className={`cc-btn h-7 px-2.5 text-[11px] ${
                    tierFilter === tier
                      ? `cc-btn--tone ${tier === "all" ? "tone-live" : TIER_META[tier].tone}`
                      : ""
                  }`}
                >
                  {tier === "all" ? "ทั้งหมด" : TIER_META[tier].label}
                </button>
              ))}
            </div>

            {filteredCustomers.length === 0 ? (
              <div className="rounded-xl border border-dashed border-[var(--cc-border)] py-10 text-center text-xs cc-dim">
                ยังไม่มีข้อมูลลูกค้าในระบบ — เมื่อมีสายเข้าหรือกด &quot;เพิ่มลูกค้า&quot; ข้อมูลจะปรากฏที่นี่อัตโนมัติ
              </div>
            ) : (
              <div className="space-y-2">
                {(showAllCustomers ? filteredCustomers : filteredCustomers.slice(0, 6)).map((c) => {
                  const isSelected = !showNewCustomer && activeCustomer?.phone === c.phone;
                  const meta = TIER_META[c.tier] || TIER_META.new;
                  const openCnt = crm.tickets.filter(
                    (t) => t.phone === c.phone && t.status !== "resolved" && t.status !== "closed"
                  ).length;
                  return (
                    <button
                      key={c.phone}
                      type="button"
                      onClick={() => {
                        setShowNewCustomer(false);
                        setSelectedPhone(c.phone);
                        setEditDraft(null);
                      }}
                      className={`w-full rounded-xl border p-3 text-left transition cursor-pointer ${
                        isSelected
                          ? "border-[var(--tone-line)] bg-[var(--tone-soft)] tone-ai"
                          : "border-[var(--cc-border)] bg-[var(--cc-inset)] hover:border-[var(--cc-border-strong)]"
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="min-w-0">
                          <div className="truncate text-sm font-semibold cc-text">
                            {c.name || formatPhone(c.phone)}
                          </div>
                          <div className="mt-0.5 flex flex-wrap items-center gap-2 text-[11px] cc-dim">
                            <span className="font-mono tabular-nums">{formatPhone(c.phone)}</span>
                            {c.company && <span>• {c.company}</span>}
                          </div>
                        </div>
                        <span className={`cc-chip ${meta.tone} shrink-0 text-[10.5px]`}>{meta.label}</span>
                      </div>

                      <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] cc-muted">
                        <span>โทร {c.totalCalls} ครั้ง</span>
                        <span className={`cc-chip ${c.preferredLanguage ? "tone-live" : "tone-idle"} py-0.5 text-[10px]`}>
                          🌐 {languageShortLabel(c.preferredLanguage)}
                        </span>
                        {c.avgCsat !== null && (
                          <span className="cc-chip tone-in py-0.5 text-[10px]">CSAT {c.avgCsat}/10</span>
                        )}
                        {openCnt > 0 && (
                          <span className="cc-chip tone-danger py-0.5 text-[10px]">เคสค้าง {openCnt}</span>
                        )}
                      </div>
                    </button>
                  );
                })}
                {filteredCustomers.length > 6 && (
                  <button
                    type="button"
                    onClick={() => setShowAllCustomers((v) => !v)}
                    className="cc-btn h-8 w-full text-xs"
                  >
                    {showAllCustomers
                      ? "ย่อรายการลูกค้า"
                      : `ดูลูกค้าทั้งหมด (+${filteredCustomers.length - 6} ราย)`}
                  </button>
                )}
              </div>
            )}
          </div>

          {/* Right: Customer 360° Editor + History */}
          <div className="cc-panel space-y-4 p-5 lg:col-span-7">
            {!activeDraft ? (
              <div className="py-16 text-center text-xs cc-dim">
                เลือกลูกค้าจากรายการฝั่งซ้าย หรือกดปุ่ม &quot;เพิ่มลูกค้า&quot; เพื่อสร้างข้อมูล CRM
              </div>
            ) : (
              <>
                <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--cc-border)] pb-3">
                  <div>
                    <h3 className="text-base font-bold cc-text">
                      {showNewCustomer ? "เพิ่มลูกค้าใหม่ใน CRM" : activeDraft.name || formatPhone(activeDraft.phone)}
                    </h3>
                    {!showNewCustomer && activeCustomer && (
                      <p className="mt-0.5 text-xs cc-muted">
                        โทรมาแล้ว {activeCustomer.totalCalls} ครั้ง • ล่าสุด {formatDateTime(activeCustomer.lastCallAt)}
                        {activeCustomer.lastSentiment && (
                          <> • อารมณ์ล่าสุด: {sentimentMeta(activeCustomer.lastSentiment)?.label ?? activeCustomer.lastSentiment}</>
                        )}
                      </p>
                    )}
                  </div>
                  {!readOnly && (
                    <div className="flex items-center gap-2">
                      {!showNewCustomer && (
                        <button
                          type="button"
                          onClick={() => {
                            setTicketForm((p) => ({ ...p, phone: activeDraft.phone }));
                            setShowNewTicket(true);
                          }}
                          className="cc-btn cc-btn--tone tone-live h-9"
                        >
                          <Plus className="h-3.5 w-3.5" />
                          <span>เปิดเคส</span>
                        </button>
                      )}
                      <button type="button" onClick={handleSaveCustomer} className="cc-btn cc-btn--solid tone-in h-9 px-4">
                        <Save className="h-3.5 w-3.5" />
                        <span>บันทึกข้อมูล CRM</span>
                      </button>
                    </div>
                  )}
                </div>

                {/* Form Grid */}
                <fieldset disabled={readOnly} className="grid grid-cols-1 gap-3 sm:grid-cols-2 disabled:opacity-80">
                  <div>
                    <label className="mb-1 block text-[11px] font-medium cc-muted">เบอร์โทรศัพท์</label>
                    <input
                      className={`${inputCls} font-mono`}
                      disabled={!showNewCustomer}
                      value={activeDraft.phone}
                      onChange={(e) => setEditDraft({ ...activeDraft, phone: e.target.value })}
                      placeholder="เช่น 0812345678"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-[11px] font-medium cc-muted">ชื่อผู้ติดต่อ</label>
                    <input
                      className={inputCls}
                      value={activeDraft.name}
                      onChange={(e) => setEditDraft({ ...activeDraft, name: e.target.value })}
                      placeholder="เช่น คุณสมชาย"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-[11px] font-medium cc-muted">ชื่อร้าน / บริษัท</label>
                    <div className="relative">
                      <Building2 className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 cc-dim" />
                      <input
                        className={`${inputCls} pl-8`}
                        value={activeDraft.company}
                        onChange={(e) => setEditDraft({ ...activeDraft, company: e.target.value })}
                        placeholder="เช่น บจก. บ้านเชียงซอฟต์"
                      />
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="mb-1 block text-[11px] font-medium cc-muted">ระดับลูกค้า</label>
                      <select
                        className={inputCls}
                        value={activeDraft.tier}
                        onChange={(e) => setEditDraft({ ...activeDraft, tier: e.target.value as CrmTier })}
                      >
                        <option value="new">ลูกค้าใหม่</option>
                        <option value="regular">ลูกค้าประจำ</option>
                        <option value="vip">VIP</option>
                        <option value="at_risk">ต้องดูแลพิเศษ</option>
                      </select>
                    </div>
                    <div>
                      <label className="mb-1 block text-[11px] font-medium cc-muted">สถานะการขาย</label>
                      <select
                        className={inputCls}
                        value={activeDraft.stage}
                        onChange={(e) => setEditDraft({ ...activeDraft, stage: e.target.value as CrmStage })}
                      >
                        {(Object.keys(STAGE_LABEL) as CrmStage[]).map((st) => (
                          <option key={st} value={st}>
                            {STAGE_LABEL[st]}
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>
                  <div>
                    <label className="mb-1 block text-[11px] font-medium cc-muted">ภาษาประจำของลูกค้า (ใช้ฟัง-แปล-ตอบอัตโนมัติ)</label>
                    <select
                      className={inputCls}
                      value={activeDraft.preferredLanguage}
                      onChange={(e) => setEditDraft({ ...activeDraft, preferredLanguage: e.target.value })}
                    >
                      <option value="">ยังไม่ระบุ (ถามลูกค้าเมื่อโทรเข้า)</option>
                      {SUPPORTED_LANGUAGES.map((l) => (
                        <option key={l.code} value={l.code}>
                          {l.label}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="mb-1 block text-[11px] font-medium cc-muted">แท็ก (คั่นด้วยเครื่องหมายจุลภาค)</label>
                    <div className="relative">
                      <Tag className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 cc-dim" />
                      <input
                        className={`${inputCls} pl-8`}
                        value={activeDraft.tagsText}
                        onChange={(e) => setEditDraft({ ...activeDraft, tagsText: e.target.value })}
                        placeholder="เช่น BC Ai Account, ขอใบเสนอราคา, VIP"
                      />
                    </div>
                  </div>
                  <div className="sm:col-span-2">
                    <label className="mb-1 block text-[11px] font-medium cc-muted">
                      บันทึกย่อ (J.A.R.V.I.S จะอ่านโน้ตนี้เมื่อลูกค้าโทรเข้ามาใหม่)
                    </label>
                    <textarea
                      rows={2}
                      className={inputCls}
                      value={activeDraft.notes}
                      onChange={(e) => setEditDraft({ ...activeDraft, notes: e.target.value })}
                      placeholder="เช่น สนใจแพ็กเกจ Standard 5 เครื่อง, นัดส่งใบเสนอราคาวันจันทร์"
                    />
                  </div>
                </fieldset>

                {/* Linked Tickets & Call Logs */}
                {!showNewCustomer && activeCustomer && (
                  <div className="grid grid-cols-1 gap-4 border-t border-[var(--cc-border)] pt-4 md:grid-cols-2">
                    <div>
                      <h4 className="mb-2 inline-flex items-center gap-1.5 text-xs font-semibold cc-text">
                        <Briefcase className="h-3.5 w-3.5" />
                        <span>เคสติดตามของลูกค้า ({customerTickets.length})</span>
                      </h4>
                      {customerTickets.length === 0 ? (
                        <div className="rounded-xl border border-dashed border-[var(--cc-border)] p-4 text-center text-[11px] cc-dim">
                          ยังไม่มีเคสของลูกค้ารายนี้
                        </div>
                      ) : (
                        <div className="space-y-2">
                          {(showAllCustTickets ? customerTickets : customerTickets.slice(0, 4)).map((t) => (
                            <div key={t.id} className="rounded-xl border border-[var(--cc-border)] bg-[var(--cc-inset)] p-2.5 text-xs">
                              <div className="flex items-center justify-between gap-2">
                                <span className="font-mono text-[10.5px] cc-dim">{t.id}</span>
                                <span className={`cc-chip ${STATUS_META[t.status].tone} py-0.5 text-[10px]`}>
                                  {STATUS_META[t.status].label}
                                </span>
                              </div>
                              <div className="mt-1 font-semibold cc-text">{t.subject}</div>
                            </div>
                          ))}
                          {customerTickets.length > 4 && (
                            <button
                              type="button"
                              onClick={() => setShowAllCustTickets((v) => !v)}
                              className="cc-btn h-8 w-full text-xs"
                            >
                              {showAllCustTickets ? "ย่อเคส" : `ดูเคสทั้งหมด (+${customerTickets.length - 4})`}
                            </button>
                          )}
                        </div>
                      )}
                    </div>

                    <div>
                      <h4 className="mb-2 inline-flex items-center gap-1.5 text-xs font-semibold cc-text">
                        <Phone className="h-3.5 w-3.5" />
                        <span>ประวัติการโทร & ผล QA ({customerLogs.length})</span>
                      </h4>
                      {customerLogs.length === 0 ? (
                        <div className="rounded-xl border border-dashed border-[var(--cc-border)] p-4 text-center text-[11px] cc-dim">
                          ยังไม่มีบันทึกสายสนทนา
                        </div>
                      ) : (
                        <div className="space-y-2">
                          {(showAllCustLogs ? customerLogs : customerLogs.slice(0, 4)).map((l) => (
                            <button
                              key={l.sessionId}
                              type="button"
                              onClick={() => onOpenQaLog(l.phone, l.sessionId)}
                              className="w-full rounded-xl border border-[var(--cc-border)] bg-[var(--cc-inset)] p-2.5 text-left text-xs transition hover:border-[var(--cc-border-strong)] cursor-pointer"
                            >
                              <div className="flex items-center justify-between gap-2">
                                <span className="text-[11px] cc-dim">{formatDateTime(l.startedAt)}</span>
                                <span className="font-mono text-[11px] cc-muted">{formatSeconds(l.durationSec)}</span>
                              </div>
                              <div className="mt-1 truncate font-medium cc-text">
                                {l.analysis?.topic || `บทสนทนา ${l.messages.length} ข้อความ`}
                              </div>
                            </button>
                          ))}
                          {customerLogs.length > 4 && (
                            <button
                              type="button"
                              onClick={() => setShowAllCustLogs((v) => !v)}
                              className="cc-btn h-8 w-full text-xs"
                            >
                              {showAllCustLogs ? "ย่อประวัติ" : `ดูประวัติทั้งหมด (+${customerLogs.length - 4} สาย)`}
                            </button>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}

      {/* TAB 2: Ticket Pipeline Board */}
      {subTab === "tickets" && (
        <div className="cc-panel space-y-4 p-4">
          <div className="flex flex-wrap items-center gap-1.5">
            {(["all", "open", "in_progress", "waiting_customer", "resolved", "closed"] as const).map((st) => (
              <button
                key={st}
                type="button"
                onClick={() => setStatusFilter(st)}
                className={`cc-btn h-8 px-3 text-xs ${
                  statusFilter === st
                    ? `cc-btn--tone ${st === "all" ? "tone-live" : STATUS_META[st].tone}`
                    : ""
                }`}
              >
                {st === "all" ? "ทุกสถานะ" : STATUS_META[st].label}
              </button>
            ))}
          </div>

          {filteredTickets.length === 0 ? (
            <div className="rounded-xl border border-dashed border-[var(--cc-border)] py-12 text-center text-xs cc-dim">
              ยังไม่มีเคสติดตามงาน — เมื่อ DeepSeek QA พบว่าสายไหนยังแก้ไม่จบหรือต้องให้พนักงานติดต่อกลับ ระบบจะเปิดเคสให้อัตโนมัติที่นี่
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              {filteredTickets.map((t) => (
                <div key={t.id} className="rounded-2xl border border-[var(--cc-border)] bg-[var(--cc-inset)] p-4">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="font-mono text-xs font-bold cc-text">{t.id}</span>
                      <span className={`cc-chip ${PRIORITY_META[t.priority].tone} text-[10.5px]`}>
                        {PRIORITY_META[t.priority].label}
                      </span>
                      {t.source === "ai_callback" && (
                        <span className="cc-chip tone-danger text-[10.5px]">
                          <PhoneCall className="h-3 w-3" />
                          <span>ลูกค้าขอให้โทรกลับ</span>
                        </span>
                      )}
                      {t.source === "ai_auto" && (
                        <span className="cc-chip tone-ai text-[10.5px]">
                          <Bot className="h-3 w-3" />
                          <span>AI สร้างอัตโนมัติ</span>
                        </span>
                      )}
                    </div>
                    {!readOnly && (
                      <button
                        type="button"
                        onClick={() => onDeleteTicket(t.id)}
                        className="cc-btn h-7 w-7 px-0 tone-danger"
                        title="ลบเคส"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>

                  <h4 className="mt-2 text-sm font-semibold cc-text">{t.subject}</h4>
                  <div className="mt-1 flex flex-wrap items-center gap-2 text-xs cc-muted">
                    {(t.callbackPhone || t.phone) && <span className="font-mono">{formatPhone(t.callbackPhone || t.phone)}</span>}
                    {t.callerPhone && (t.callbackPhone || t.phone) && t.callerPhone !== (t.callbackPhone || t.phone) && (
                      <span>(โทรเข้าจาก {formatPhone(t.callerPhone)})</span>
                    )}
                    {t.customerName && <span>• {t.customerName}</span>}
                    {t.company && <span>({t.company})</span>}
                  </div>

                  {t.description && (
                    <p className="mt-2 whitespace-pre-line rounded-xl border border-[var(--cc-border)] bg-[var(--cc-panel)] p-2.5 text-xs leading-relaxed cc-muted">
                      {t.description}
                    </p>
                  )}

                  {(t.acknowledgedBy || t.closedBy) && (
                    <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] cc-dim">
                      {t.acknowledgedBy && t.acknowledgedAt && (
                        <span>รับทราบโดย {t.acknowledgedBy} • {formatDateTime(t.acknowledgedAt)}</span>
                      )}
                      {t.closedBy && t.closedAt && (
                        <span>ปิดเคสโดย {t.closedBy} • {formatDateTime(t.closedAt)}</span>
                      )}
                    </div>
                  )}

                  {!readOnly && t.status !== "closed" && (onAcknowledgeTicket || onCloseTicket) && (
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      {t.status === "open" && !t.acknowledgedAt && onAcknowledgeTicket && (
                        <button
                          type="button"
                          onClick={() => onAcknowledgeTicket(t.id)}
                          className="cc-btn cc-btn--tone tone-held h-8 px-3"
                        >
                          <UserCheck className="h-3.5 w-3.5" />
                          <span>รับทราบแล้ว</span>
                        </button>
                      )}
                      {onCloseTicket && (
                        <button
                          type="button"
                          onClick={() => onCloseTicket(t.id)}
                          className="cc-btn cc-btn--solid tone-in h-8 px-3"
                        >
                          <CheckCircle2 className="h-3.5 w-3.5" />
                          <span>ปิด Ticket (รับรู้แล้ว)</span>
                        </button>
                      )}
                    </div>
                  )}

                  <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-[var(--cc-border)] pt-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <select
                        disabled={readOnly}
                        value={t.status}
                        onChange={(e) => onUpdateTicket(t.id, { status: e.target.value as TicketStatus })}
                        className="h-8 rounded-lg border border-[var(--cc-border)] bg-[var(--cc-panel)] px-2 text-xs cc-text outline-none disabled:opacity-70"
                      >
                        {(Object.keys(STATUS_META) as TicketStatus[]).map((st) => (
                          <option key={st} value={st}>
                            {STATUS_META[st].label}
                          </option>
                        ))}
                      </select>

                      <input
                        disabled={readOnly}
                        defaultValue={t.assignee}
                        onBlur={(e) => {
                          if (e.target.value.trim() !== t.assignee) {
                            onUpdateTicket(t.id, { assignee: e.target.value.trim() });
                          }
                        }}
                        className="h-8 w-36 rounded-lg border border-[var(--cc-border)] bg-[var(--cc-panel)] px-2.5 text-xs cc-text outline-none disabled:opacity-70"
                        placeholder="ผู้รับผิดชอบ"
                      />
                    </div>

                    <div className="flex items-center gap-2">
                      {t.sessionId && t.phone && (
                        <button
                          type="button"
                          onClick={() => onOpenQaLog(t.phone, t.sessionId!)}
                          className="cc-btn cc-btn--tone tone-ai h-8 px-2.5 text-[11px]"
                        >
                          ดูบันทึกสาย
                        </button>
                      )}
                      <span className="text-[11px] cc-dim">{formatDateTime(t.createdAt)}</span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
