"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  ArrowLeft,
  BookOpen,
  Brain,
  CheckCircle2,
  Clock3,
  Copy,
  Database,
  ExternalLink,
  FileText,
  FolderOpen,
  Layers,
  Link2,
  ListChecks,
  Lock,
  Pencil,
  Plus,
  RefreshCw,
  Save,
  Search,
  Sparkles,
  Table2,
  Tag,
  Trash2,
  Video,
  X,
} from "lucide-react";

interface BrainFolder {
  name: string;
  label: string;
  count: number;
  isDev: boolean;
}

interface BrainNoteSummary {
  slug: string;
  title: string;
  folder: string;
  folderLabel: string;
  audience: "user" | "dev";
  tags: string[];
  aliases: string[];
  snippet: string;
  updatedAt: string;
  mediaCount: number;
  linkCount: number;
}

interface BrainLinkRef {
  slug: string;
  title: string;
  folder: string;
  audience: "user" | "dev";
}

interface BrainNoteDetail {
  slug: string;
  title: string;
  folder: string;
  folderLabel: string;
  audience: "user" | "dev";
  tags: string[];
  aliases: string[];
  body: string;
  images: { alt: string; url: string }[];
  videos: { title: string; url: string }[];
  outLinks: BrainLinkRef[];
  backlinks: BrainLinkRef[];
  updatedAt: string;
}

interface BrainWishlistItem {
  done: boolean;
  title: string;
  slug: string;
  details: string[];
}

interface BrainMissItem {
  query: string;
  rawKey: string;
  count: number;
  lastAt: string;
  ctxTopic: string;
  topSlug: string;
  topCosine: number;
  reason: string;
}

interface BrainOverviewResponse {
  ok: boolean;
  mode: string;
  isAdmin: boolean;
  audience: "user" | "dev" | "all";
  loadedAt: string | null;
  stats: {
    totalNotes: number;
    userNotes: number;
    devNotes: number;
    totalMenus: number;
    matchedNotes: number;
    pendingWishlist: number;
    missCount?: number;
    vector?: {
      enabled: boolean;
      ready: boolean;
      chunks: number;
      notes: number;
      model: string;
      tokenizer: string;
    };
  };
  folders: BrainFolder[];
  notes: BrainNoteSummary[];
  wishlist: {
    pendingCount: number;
    doneCount: number;
    items: BrainWishlistItem[];
  };
  misses?: {
    totalEvents: number;
    uniqueCount: number;
    items: BrainMissItem[];
  };
}

interface BrainMenuEntry {
  code: string;
  legacy: string;
  title: string;
  path: string;
  edition: string;
  group: string;
  catalogSlug: string;
}

interface BrainMenusResponse {
  ok: boolean;
  total: number;
  matched: number;
  groups: { code: string; label: string; count: number }[];
  menus: BrainMenuEntry[];
}

type SubTab = "notes" | "menus" | "wishlist" | "misses";

const USER_WRITE_FOLDERS: { value: string; label: string }[] = [
  { value: "faq", label: "คำถามที่พบบ่อย (faq)" },
  { value: "howto", label: "คู่มือใช้งานทีละขั้น (howto)" },
  { value: "reports", label: "รายงาน & งบการเงิน (reports)" },
  { value: "sales", label: "งานขาย & แพ็กเกจ (sales)" },
  { value: "systems", label: "ภาพรวมโมดูลระบบ (systems)" },
  { value: "glossary", label: "คำศัพท์บัญชี/ระบบ (glossary)" },
  { value: "troubleshoot", label: "แก้ไขปัญหา & Error (troubleshoot)" },
];

function formatShortTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("th-TH", {
      day: "numeric",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function renderInlineMarkdown(
  text: string,
  onSelectSlug: (slug: string) => void
): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  const tokenRe =
    /(\[\[(?:[a-z_]+::)?[^\]|#]+?(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]|\*\*[^*\n]+\*\*|`[^`\n]+`|\[[^\]]+\]\(https:\/\/[^)\s]+\)|https:\/\/[^\s)<]+)/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let keyIdx = 0;
  while ((match = tokenRe.exec(text)) !== null) {
    if (match.index > last) {
      nodes.push(text.slice(last, match.index));
    }
    const tok = match[0];
    if (tok.startsWith("[[") && tok.endsWith("]]")) {
      const inner = tok.slice(2, -2);
      const withoutRel = inner.replace(/^[a-z_]+::/, "");
      const [targetPart, labelPart] = withoutRel.split("|");
      const slug = (targetPart || "").split("#")[0].trim();
      const label = (labelPart || slug).trim();
      nodes.push(
        <button
          key={`wl-${keyIdx++}`}
          type="button"
          onClick={() => onSelectSlug(slug)}
          className="mx-0.5 inline-flex items-center gap-1 rounded-md border border-[var(--tone-line)] bg-[var(--tone-soft)] px-1.5 py-0.5 align-baseline font-mono text-[11px] font-medium text-[var(--tone-ink)] transition hover:opacity-85 tone-ai"
          title={`เปิดโน้ต ${slug}`}
        >
          <Link2 className="h-3 w-3 shrink-0" />
          <span>{label}</span>
        </button>
      );
    } else if (tok.startsWith("**") && tok.endsWith("**")) {
      nodes.push(
        <strong key={`b-${keyIdx++}`} className="font-semibold cc-text">
          {tok.slice(2, -2)}
        </strong>
      );
    } else if (tok.startsWith("`") && tok.endsWith("`")) {
      nodes.push(
        <code
          key={`c-${keyIdx++}`}
          className="rounded bg-[var(--cc-chip)] px-1.5 py-0.5 font-mono text-[12px] cc-text"
        >
          {tok.slice(1, -1)}
        </code>
      );
    } else if (tok.startsWith("[")) {
      const lm = tok.match(/^\[([^\]]+)\]\((https:\/\/[^)\s]+)\)$/);
      if (lm) {
        nodes.push(
          <a
            key={`a-${keyIdx++}`}
            href={lm[2]}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 underline decoration-dotted underline-offset-2 tone-live cc-ink hover:opacity-80"
          >
            <span>{lm[1]}</span>
            <ExternalLink className="h-3 w-3 shrink-0" />
          </a>
        );
      } else {
        nodes.push(tok);
      }
    } else if (tok.startsWith("https://")) {
      nodes.push(
        <a
          key={`u-${keyIdx++}`}
          href={tok}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 break-all underline decoration-dotted underline-offset-2 tone-live cc-ink hover:opacity-80"
        >
          <span>{tok}</span>
          <ExternalLink className="h-3 w-3 shrink-0" />
        </a>
      );
    }
    last = match.index + tok.length;
  }
  if (last < text.length) {
    nodes.push(text.slice(last));
  }
  return nodes;
}

function SafeMarkdownBody({
  markdown,
  onSelectSlug,
}: {
  markdown: string;
  onSelectSlug: (slug: string) => void;
}) {
  const blocks = useMemo(() => {
    const lines = String(markdown || "").replace(/\r\n/g, "\n").split("\n");
    const out: React.ReactNode[] = [];
    let i = 0;
    let key = 0;

    while (i < lines.length) {
      const line = lines[i];
      const trimmed = line.trim();
      if (!trimmed) {
        i++;
        continue;
      }

      // Fenced code block
      if (trimmed.startsWith("```")) {
        const lang = trimmed.slice(3).trim();
        i++;
        const codeLines: string[] = [];
        while (i < lines.length && !lines[i].trim().startsWith("```")) {
          codeLines.push(lines[i]);
          i++;
        }
        if (i < lines.length) i++;
        out.push(
          <div key={`code-${key++}`} className="my-3 overflow-hidden rounded-xl border border-[var(--cc-border)] bg-[var(--cc-inset)]">
            {lang && (
              <div className="border-b border-[var(--cc-border)] px-3 py-1 font-mono text-[10px] uppercase tracking-wider cc-dim">
                {lang}
              </div>
            )}
            <pre className="overflow-x-auto p-3 font-mono text-xs leading-relaxed cc-text">
              <code>{codeLines.join("\n")}</code>
            </pre>
          </div>
        );
        continue;
      }

      // Horizontal rule
      if (/^(?:---+|\*\*\*+|___+)$/.test(trimmed)) {
        out.push(<hr key={`hr-${key++}`} className="my-4 border-[var(--cc-border)]" />);
        i++;
        continue;
      }

      // Headings
      const hm = trimmed.match(/^(#{1,4})\s+(.+)$/);
      if (hm) {
        const lvl = hm[1].length;
        const content = renderInlineMarkdown(hm[2], onSelectSlug);
        if (lvl === 1) {
          out.push(
            <h1 key={`h-${key++}`} className="mt-2 mb-3 text-lg font-bold tracking-tight cc-text">
              {content}
            </h1>
          );
        } else if (lvl === 2) {
          out.push(
            <h2 key={`h-${key++}`} className="mt-5 mb-2 border-b border-[var(--cc-border)] pb-1.5 text-[15px] font-semibold cc-text">
              {content}
            </h2>
          );
        } else {
          out.push(
            <h3 key={`h-${key++}`} className="mt-4 mb-1.5 text-sm font-semibold cc-text">
              {content}
            </h3>
          );
        }
        i++;
        continue;
      }

      // Markdown table
      if (
        trimmed.startsWith("|") &&
        i + 1 < lines.length &&
        /^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)+\|?\s*$/.test(lines[i + 1])
      ) {
        const parseRow = (row: string) =>
          row
            .trim()
            .replace(/^\|/, "")
            .replace(/\|$/, "")
            .split("|")
            .map((c) => c.trim());
        const headers = parseRow(line);
        i += 2;
        const rows: string[][] = [];
        while (i < lines.length && lines[i].trim().startsWith("|")) {
          rows.push(parseRow(lines[i]));
          i++;
        }
        out.push(
          <div key={`tbl-${key++}`} className="my-3 overflow-x-auto rounded-xl border border-[var(--cc-border)]">
            <table className="w-full border-collapse text-left text-xs">
              <thead className="bg-[var(--cc-inset)]">
                <tr>
                  {headers.map((h, ci) => (
                    <th key={ci} className="border-b border-[var(--cc-border)] px-3 py-2 font-semibold cc-text">
                      {renderInlineMarkdown(h.replace(/<br\s*\/?>/gi, " · "), onSelectSlug)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r, ri) => (
                  <tr key={ri} className="border-b border-[var(--cc-border)] last:border-b-0 hover:bg-[var(--cc-chip)]">
                    {headers.map((_, ci) => (
                      <td key={ci} className="px-3 py-2 align-top leading-relaxed cc-muted">
                        {renderInlineMarkdown((r[ci] || "").replace(/<br\s*\/?>/gi, " · "), onSelectSlug)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
        continue;
      }

      // Blockquote
      if (trimmed.startsWith(">")) {
        const qLines: string[] = [];
        while (i < lines.length && lines[i].trim().startsWith(">")) {
          qLines.push(lines[i].trim().replace(/^>\s?/, ""));
          i++;
        }
        out.push(
          <blockquote
            key={`bq-${key++}`}
            className="my-3 rounded-r-xl border-l-3 border-[var(--tone)] bg-[var(--tone-soft)] px-3.5 py-2.5 text-xs leading-relaxed cc-text tone-ai"
          >
            {renderInlineMarkdown(qLines.join(" "), onSelectSlug)}
          </blockquote>
        );
        continue;
      }

      // Bullet or numbered list
      if (/^(?:[-*+]\s+|\d+[.)]\s+)/.test(trimmed)) {
        const isOrdered = /^\d+[.)]\s+/.test(trimmed);
        const items: string[] = [];
        while (i < lines.length && /^(?:\s*(?:[-*+]\s+|\d+[.)]\s+))/.test(lines[i])) {
          items.push(lines[i].trim().replace(/^(?:[-*+]\s+|\d+[.)]\s+)/, ""));
          i++;
        }
        const ListTag = isOrdered ? "ol" : "ul";
        out.push(
          <ListTag
            key={`list-${key++}`}
            className={`my-2.5 space-y-1.5 pl-5 text-[13px] leading-relaxed cc-muted ${isOrdered ? "list-decimal" : "list-disc"}`}
          >
            {items.map((it, idx) => (
              <li key={idx}>{renderInlineMarkdown(it, onSelectSlug)}</li>
            ))}
          </ListTag>
        );
        continue;
      }

      // Image standalone line
      const imgM = trimmed.match(/^!\[([^\]]*)\]\((https:\/\/[^)\s]+)\)$/);
      if (imgM) {
        out.push(
          <figure key={`img-${key++}`} className="my-3 overflow-hidden rounded-xl border border-[var(--cc-border)] bg-[var(--cc-inset)] p-2">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={imgM[2]} alt={imgM[1] || "ภาพประกอบ"} className="max-h-72 w-auto rounded-lg object-contain" loading="lazy" />
            {imgM[1] && <figcaption className="mt-1.5 text-[11px] cc-dim">{imgM[1]}</figcaption>}
          </figure>
        );
        i++;
        continue;
      }

      // Regular paragraph
      const pLines: string[] = [];
      while (
        i < lines.length &&
        lines[i].trim() &&
        !/^(?:#{1,4}\s+|```|[-*+]\s+|\d+[.)]\s+|>|\|)/.test(lines[i].trim())
      ) {
        pLines.push(lines[i].trim());
        i++;
      }
      out.push(
        <p key={`p-${key++}`} className="my-2 text-[13.5px] leading-relaxed cc-muted">
          {renderInlineMarkdown(pLines.join(" "), onSelectSlug)}
        </p>
      );
    }

    return out;
  }, [markdown, onSelectSlug]);

  return <div>{blocks}</div>;
}

export function BrainWorkspace({
  readOnly = true,
  onNotify,
}: {
  readOnly?: boolean;
  onNotify?: (msg: string) => void;
}) {
  const [subTab, setSubTab] = useState<SubTab>("notes");
  const [audience, setAudience] = useState<"user" | "dev" | "all">("user");
  const [folder, setFolder] = useState<string>("");
  const [query, setQuery] = useState<string>("");
  const [debouncedQuery, setDebouncedQuery] = useState<string>("");
  const [overview, setOverview] = useState<BrainOverviewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const [selectedSlug, setSelectedSlug] = useState<string>("");
  const [noteDetail, setNoteDetail] = useState<BrainNoteDetail | null>(null);
  const [noteLoading, setNoteLoading] = useState(false);
  const [noteError, setNoteError] = useState<string | null>(null);
  const [historyStack, setHistoryStack] = useState<string[]>([]);

  const [menuQuery, setMenuQuery] = useState<string>("");
  const [debouncedMenuQuery, setDebouncedMenuQuery] = useState<string>("");
  const [menuGroup, setMenuGroup] = useState<string>("");
  const [menusData, setMenusData] = useState<BrainMenusResponse | null>(null);
  const [menusLoading, setMenusLoading] = useState(false);

  // Admin-only Note Editor / Fill-Wishlist / Fill-Miss Modal
  const [editorOpen, setEditorOpen] = useState(false);
  const [edSlug, setEdSlug] = useState("");
  const [edFolder, setEdFolder] = useState("faq");
  const [edTitle, setEdTitle] = useState("");
  const [edAliases, setEdAliases] = useState("");
  const [edTags, setEdTags] = useState("");
  const [edBody, setEdBody] = useState("");
  const [edResolveWishlist, setEdResolveWishlist] = useState("");
  const [edClearMiss, setEdClearMiss] = useState("");
  const [edSaving, setEdSaving] = useState(false);
  const [edError, setEdError] = useState<string | null>(null);

  const readerTopRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const t = window.setTimeout(() => setDebouncedQuery(query.trim()), 200);
    return () => window.clearTimeout(t);
  }, [query]);

  useEffect(() => {
    const t = window.setTimeout(() => setDebouncedMenuQuery(menuQuery.trim()), 200);
    return () => window.clearTimeout(t);
  }, [menuQuery]);

  // Reset audience to user if switching back to read-only mode
  useEffect(() => {
    if (readOnly && audience !== "user") {
      setAudience("user");
      setFolder("");
    }
  }, [readOnly, audience]);

  const loadOverview = useCallback(
    async (manual = false) => {
      if (manual) setRefreshing(true);
      try {
        const params = new URLSearchParams();
        params.set("audience", readOnly ? "user" : audience);
        if (folder) params.set("folder", folder);
        if (debouncedQuery) params.set("q", debouncedQuery);
        params.set("limit", "200");
        if (manual) params.set("refresh", "1");
        const res = await fetch(`/api/brain/overview?${params.toString()}`, { cache: "no-store" });
        if (!res.ok) throw new Error("โหลดคลังสมองไม่สำเร็จ");
        const data = (await res.json()) as BrainOverviewResponse;
        setOverview(data);
        if (manual) {
          onNotify?.(`รีเฟรชสมอง BC ล่าสุดแล้ว (${data.stats.totalNotes} โน้ต · ${data.stats.totalMenus} เมนู)`);
        }
      } catch {
        if (manual) onNotify?.("ไม่สามารถโหลดข้อมูลสมอง BC ได้");
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [audience, folder, debouncedQuery, readOnly, onNotify]
  );

  useEffect(() => {
    void loadOverview(false);
  }, [loadOverview]);

  const loadMenus = useCallback(
    async (manual = false) => {
      setMenusLoading(true);
      try {
        const params = new URLSearchParams();
        if (debouncedMenuQuery) params.set("q", debouncedMenuQuery);
        if (menuGroup) params.set("group", menuGroup);
        params.set("limit", "200");
        if (manual) params.set("refresh", "1");
        const res = await fetch(`/api/brain/menus?${params.toString()}`, { cache: "no-store" });
        if (res.ok) {
          setMenusData((await res.json()) as BrainMenusResponse);
        }
      } finally {
        setMenusLoading(false);
      }
    },
    [debouncedMenuQuery, menuGroup]
  );

  useEffect(() => {
    if (subTab === "menus") {
      void loadMenus(false);
    }
  }, [subTab, loadMenus]);

  const openNote = useCallback(
    async (slug: string, pushHistory = true) => {
      const clean = slug.trim();
      if (!clean) return;
      if (pushHistory && selectedSlug && selectedSlug !== clean) {
        setHistoryStack((prev) => [...prev.slice(-15), selectedSlug]);
      }
      setSelectedSlug(clean);
      setSubTab("notes");
      setNoteLoading(true);
      setNoteError(null);
      try {
        const res = await fetch(`/api/brain/note/${encodeURIComponent(clean)}`, { cache: "no-store" });
        const data = (await res.json()) as { ok?: boolean; note?: BrainNoteDetail; error?: string };
        if (!res.ok || !data.ok || !data.note) {
          setNoteDetail(null);
          setNoteError(data.error || "ไม่พบโน้ตที่ระบุ");
          return;
        }
        setNoteDetail(data.note);
        readerTopRef.current?.scrollTo({ top: 0, behavior: "smooth" });
      } catch {
        setNoteDetail(null);
        setNoteError("เชื่อมต่อเซิร์ฟเวอร์เพื่อเปิดโน้ตไม่สำเร็จ");
      } finally {
        setNoteLoading(false);
      }
    },
    [selectedSlug]
  );

  // Auto-select first note when overview loads and nothing is selected
  useEffect(() => {
    if (!selectedSlug && overview?.notes?.length) {
      const preferred =
        overview.notes.find((n) => n.slug === "start-first-day") ||
        overview.notes.find((n) => n.slug === "sales-overview") ||
        overview.notes[0];
      if (preferred) {
        void openNote(preferred.slug, false);
      }
    }
  }, [overview, selectedSlug, openNote]);

  const handleBackNote = useCallback(() => {
    setHistoryStack((prev) => {
      if (prev.length === 0) return prev;
      const last = prev[prev.length - 1];
      void openNote(last, false);
      return prev.slice(0, -1);
    });
  }, [openNote]);

  const handleCopySlug = useCallback(
    async (slug: string) => {
      try {
        await navigator.clipboard.writeText(slug);
        onNotify?.(`คัดลอกรหัสโน้ต ${slug} แล้ว`);
      } catch {
        onNotify?.("ไม่สามารถคัดลอกได้");
      }
    },
    [onNotify]
  );

  const openEditorNew = useCallback(() => {
    setEdSlug("");
    setEdFolder("faq");
    setEdTitle("");
    setEdAliases("");
    setEdTags("");
    setEdBody("");
    setEdResolveWishlist("");
    setEdClearMiss("");
    setEdError(null);
    setEditorOpen(true);
  }, []);

  const openEditorForNote = useCallback((note: BrainNoteDetail) => {
    setEdSlug(note.slug);
    setEdFolder(USER_WRITE_FOLDERS.some((f) => f.value === note.folder) ? note.folder : "faq");
    setEdTitle(note.title);
    setEdAliases(note.aliases.join("\n"));
    setEdTags(note.tags.join(", "));
    setEdBody(note.body);
    setEdResolveWishlist("");
    setEdClearMiss("");
    setEdError(null);
    setEditorOpen(true);
  }, []);

  const openEditorForWishlist = useCallback((item: BrainWishlistItem) => {
    const slugGuess = item.slug || `faq-${Date.now().toString(36)}`;
    const folderGuess = slugGuess.startsWith("rpt-")
      ? "reports"
      : slugGuess.startsWith("howto-")
      ? "howto"
      : slugGuess.startsWith("sales-")
      ? "sales"
      : slugGuess.startsWith("sys-")
      ? "systems"
      : slugGuess.startsWith("term-")
      ? "glossary"
      : slugGuess.startsWith("ts-")
      ? "troubleshoot"
      : "faq";
    setEdSlug(slugGuess);
    setEdFolder(folderGuess);
    setEdTitle(item.title);
    setEdAliases(item.title);
    setEdTags(folderGuess);
    setEdBody(
      `## คำตอบยืนยันสำหรับลูกค้า\n\n` +
        (item.details.length ? `> โจทย์จากสายจริง:\n> ${item.details.slice(0, 2).join("\n> ")}\n\n` : "") +
        `พิมพ์ข้อเท็จจริง ขั้นตอน และรหัสเมนู 4 หลักที่ถูกต้องที่นี่...`
    );
    setEdResolveWishlist(item.slug || item.title);
    setEdClearMiss("");
    setEdError(null);
    setEditorOpen(true);
  }, []);

  const openEditorForMiss = useCallback((item: BrainMissItem) => {
    setEdSlug(`faq-miss-${Date.now().toString(36).slice(-5)}`);
    setEdFolder("faq");
    setEdTitle(item.query.slice(0, 90));
    setEdAliases(item.query);
    setEdTags("faq, call-center");
    setEdBody(`## คำถามจากสายลูกค้า\n${item.query}\n\n## คำตอบยืนยันสำหรับลูกค้า\n`);
    setEdResolveWishlist("");
    setEdClearMiss(item.query);
    setEdError(null);
    setEditorOpen(true);
  }, []);

  const handleSaveNote = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (readOnly) return;
      setEdError(null);
      setEdSaving(true);
      try {
        const res = await fetch("/api/brain/notes", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            slug: edSlug.trim(),
            folder: edFolder,
            title: edTitle.trim(),
            aliases: edAliases
              .split(/\r?\n|,/)
              .map((x) => x.trim())
              .filter(Boolean),
            tags: edTags
              .split(",")
              .map((x) => x.trim())
              .filter(Boolean),
            body: edBody.trim(),
            resolveWishlistSlug: edResolveWishlist,
            clearMissQuery: edClearMiss,
          }),
        });
        const j = (await res.json()) as {
          ok?: boolean;
          slug?: string;
          wishlistResolved?: boolean;
          pgSyncTriggered?: boolean;
          error?: string;
        };
        if (!res.ok || !j.ok) {
          setEdError(j.error || "บันทึกโน้ตไม่สำเร็จ");
          return;
        }
        setEditorOpen(false);
        onNotify?.(
          `บันทึกโน้ต ${j.slug} เข้าสมอง BC แล้ว${j.wishlistResolved ? " + ปิดคิวรอเติมแล้ว" : ""}${
            j.pgSyncTriggered ? " (กำลังซิงก์ pgvector)" : ""
          }`
        );
        await loadOverview(false);
        if (j.slug) {
          void openNote(j.slug, false);
        }
      } catch {
        setEdError("ไม่สามารถเชื่อมต่อเซิร์ฟเวอร์เพื่อบันทึกโน้ตได้");
      } finally {
        setEdSaving(false);
      }
    },
    [
      readOnly,
      edSlug,
      edFolder,
      edTitle,
      edAliases,
      edTags,
      edBody,
      edResolveWishlist,
      edClearMiss,
      onNotify,
      loadOverview,
      openNote,
    ]
  );

  const handleClearMiss = useCallback(
    async (qText: string) => {
      if (readOnly) return;
      try {
        const res = await fetch("/api/brain/misses/clear", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query: qText }),
        });
        if (res.ok) {
          onNotify?.("ล้างรายการคำถามนี้แล้ว");
          void loadOverview(false);
        }
      } catch {
        onNotify?.("ไม่สามารถล้างรายการได้");
      }
    },
    [readOnly, onNotify, loadOverview]
  );

  const stats = overview?.stats;
  const vectorStats = stats?.vector;

  return (
    <div className="space-y-4">
      {/* KPI Header Bar */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <div className="cc-panel p-3.5">
          <div className="flex items-center justify-between gap-2">
            <span className="cc-label">คลังความรู้ในเครื่อง</span>
            <span className="cc-chip tone-ai py-0.5 text-[10px]">
              <Brain className="h-3 w-3" />
              <span>Local RAM</span>
            </span>
          </div>
          <div className="mt-2 flex items-baseline gap-2">
            <span className="font-mono text-2xl font-bold tabular-nums cc-text">
              {stats ? stats.totalNotes : "—"}
            </span>
            <span className="text-xs cc-muted">
              โน้ต{!readOnly && stats ? ` (ลูกค้า ${stats.userNotes} · เทคนิค ${stats.devNotes})` : " โซนลูกค้า"}
            </span>
          </div>
        </div>

        <div className="cc-panel p-3.5">
          <div className="flex items-center justify-between gap-2">
            <span className="cc-label">สารบัญเมนู 4 หลัก</span>
            <span className="cc-chip tone-live py-0.5 text-[10px]">
              <Table2 className="h-3 w-3" />
              <span>1001–9070</span>
            </span>
          </div>
          <div className="mt-2 flex items-baseline gap-2">
            <span className="font-mono text-2xl font-bold tabular-nums cc-text">
              {stats ? stats.totalMenus : "—"}
            </span>
            <span className="text-xs cc-muted">หน้าจอค้นหา &lt;1ms</span>
          </div>
        </div>

        <div className="cc-panel p-3.5">
          <div className="flex items-center justify-between gap-2">
            <span className="cc-label">รอเติมเข้าสมอง</span>
            <span className="cc-chip tone-held py-0.5 text-[10px]">
              <ListChecks className="h-3 w-3" />
              <span>Wishlist</span>
            </span>
          </div>
          <div className="mt-2 flex items-baseline gap-2">
            <span className="font-mono text-2xl font-bold tabular-nums cc-text">
              {stats ? stats.pendingWishlist : "—"}
            </span>
            <span className="text-xs cc-muted">หัวข้อจากสายจริง</span>
          </div>
        </div>

        <div className="cc-panel p-3.5">
          <div className="flex items-center justify-between gap-2">
            <span className="cc-label">เครื่องค้นหาสมองเสียง</span>
            <span className={`cc-chip py-0.5 text-[10px] ${vectorStats?.ready ? "tone-in" : "tone-ai"}`}>
              <Database className="h-3 w-3" />
              <span>{vectorStats?.ready ? "pgvector + ตัดคำไทย" : "Local RAM"}</span>
            </span>
          </div>
          <div className="mt-2 flex items-baseline gap-2">
            <span className="font-mono text-lg font-bold tabular-nums cc-text">
              {vectorStats?.ready ? vectorStats.chunks.toLocaleString("th-TH") : "0ms"}
            </span>
            <span className="text-xs cc-muted">
              {vectorStats?.ready ? `chunks · ${vectorStats.model.replace(":latest", "")}` : "ฝังในเครื่อง • Hot-Reload"}
            </span>
          </div>
          <div className="mt-0.5 flex items-center gap-1 text-[11px] cc-dim">
            <Clock3 className="h-3 w-3" />
            <span>ซิงก์ล่าสุด: {formatShortTime(overview?.loadedAt)}</span>
          </div>
        </div>
      </div>

      {/* Baseline-aligned Control Toolbar */}
      <div className="cc-panel p-3">
        <div className="flex flex-wrap items-center justify-between gap-2.5">
          <div className="flex flex-wrap items-center gap-2">
            <div className="inline-flex flex-wrap items-center gap-1 rounded-xl border border-[var(--cc-border)] bg-[var(--cc-inset)] p-1">
              <button
                type="button"
                onClick={() => setSubTab("notes")}
                className={`cc-btn h-7 px-3 text-xs ${subTab === "notes" ? "cc-btn--tone tone-ai" : "border-transparent bg-transparent"}`}
              >
                <BookOpen className="h-3.5 w-3.5" />
                <span>คลังโน้ต</span>
                {stats && (
                  <span className="font-mono text-[10px] opacity-80 tabular-nums">({stats.matchedNotes})</span>
                )}
              </button>
              <button
                type="button"
                onClick={() => setSubTab("menus")}
                className={`cc-btn h-7 px-3 text-xs ${subTab === "menus" ? "cc-btn--tone tone-live" : "border-transparent bg-transparent"}`}
              >
                <Table2 className="h-3.5 w-3.5" />
                <span>สารบัญเมนู 4 หลัก</span>
              </button>
              <button
                type="button"
                onClick={() => setSubTab("wishlist")}
                className={`cc-btn h-7 px-3 text-xs ${subTab === "wishlist" ? "cc-btn--tone tone-held" : "border-transparent bg-transparent"}`}
              >
                <ListChecks className="h-3.5 w-3.5" />
                <span>รอเติมเข้าสมอง</span>
                {(stats?.pendingWishlist ?? 0) > 0 && (
                  <span className="cc-chip tone-held py-0 text-[10px] tabular-nums">{stats?.pendingWishlist}</span>
                )}
              </button>
              <button
                type="button"
                onClick={() => setSubTab("misses")}
                className={`cc-btn h-7 px-3 text-xs ${subTab === "misses" ? "cc-btn--tone tone-danger" : "border-transparent bg-transparent"}`}
              >
                <AlertCircle className="h-3.5 w-3.5" />
                <span>คำถามที่ตอบไม่ได้</span>
                {(stats?.missCount ?? 0) > 0 && (
                  <span className="cc-chip tone-danger py-0 text-[10px] tabular-nums">{stats?.missCount}</span>
                )}
              </button>
            </div>

            {subTab === "notes" && (
              readOnly ? (
                <span className="cc-chip tone-idle h-8 px-2.5 text-[11px]" title="ผู้ชมดูได้เฉพาะโซนผู้ใช้งาน (ไม่มีตาราง SQL/โค้ดภายใน)">
                  <Lock className="h-3 w-3" />
                  <span>โซนลูกค้า (User)</span>
                </span>
              ) : (
                <div className="inline-flex items-center gap-1 rounded-xl border border-[var(--cc-border)] bg-[var(--cc-inset)] p-1">
                  {(
                    [
                      { key: "user", label: "โซนลูกค้า" },
                      { key: "dev", label: "โซนเทคนิค" },
                      { key: "all", label: "ทั้งหมด" },
                    ] as const
                  ).map((opt) => (
                    <button
                      key={opt.key}
                      type="button"
                      onClick={() => {
                        setAudience(opt.key);
                        setFolder("");
                      }}
                      className={`cc-btn h-6 px-2.5 text-[11px] ${
                        audience === opt.key ? "cc-btn--tone tone-in" : "border-transparent bg-transparent"
                      }`}
                    >
                      <span>{opt.label}</span>
                    </button>
                  ))}
                </div>
              )
            )}
          </div>

          <div className="flex items-center gap-2">
            {!readOnly && (
              <button
                type="button"
                onClick={openEditorNew}
                className="cc-btn cc-btn--tone tone-in h-8 px-3 text-xs"
              >
                <Plus className="h-3.5 w-3.5" />
                <span>เพิ่มโน้ตใหม่</span>
              </button>
            )}
            <button
              type="button"
              disabled={refreshing}
              onClick={() => {
                void loadOverview(true);
                if (subTab === "menus") void loadMenus(true);
                if (selectedSlug) void openNote(selectedSlug, false);
              }}
              className="cc-btn h-8 px-3 text-xs"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
              <span>รีเฟรชสมอง</span>
            </button>
          </div>
        </div>
      </div>

      {/* TAB 1: NOTES EXPLORER */}
      {subTab === "notes" && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
          {/* Left Pane: Search + Folder Chips + Notes List */}
          <div className="cc-panel flex flex-col p-3 lg:col-span-5 xl:col-span-4">
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 cc-dim" />
              <input
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="ค้นหาโน้ต ชื่อเมนู อาการ หรือรหัส 4 หลัก..."
                className="cc-input h-9 w-full pl-9 pr-8 text-xs"
              />
              {query && (
                <button
                  type="button"
                  onClick={() => setQuery("")}
                  aria-label="ล้างคำค้น"
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 cc-dim hover:cc-text"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </div>

            {/* Folder filter chips */}
            <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
              <button
                type="button"
                onClick={() => setFolder("")}
                className={`cc-btn h-6 px-2 text-[11px] ${folder === "" ? "cc-btn--tone tone-ai" : ""}`}
              >
                <span>ทุกหมวด</span>
              </button>
              {(overview?.folders || []).map((f) => (
                <button
                  key={f.name || "root"}
                  type="button"
                  onClick={() => setFolder(f.name)}
                  className={`cc-btn h-6 px-2 text-[11px] ${
                    folder === f.name ? "cc-btn--tone tone-live" : ""
                  }`}
                >
                  <span>{f.label}</span>
                  <span className="font-mono text-[10px] opacity-75 tabular-nums">{f.count}</span>
                </button>
              ))}
            </div>

            {/* Notes List */}
            <div className="mt-3 max-h-[62vh] space-y-1.5 overflow-y-auto pr-1">
              {loading ? (
                <div className="py-10 text-center text-xs cc-dim">กำลังโหลดคลังสมอง BC...</div>
              ) : !overview?.notes.length ? (
                <div className="py-10 text-center text-xs cc-dim">ไม่พบโน้ตที่ตรงกับเงื่อนไข</div>
              ) : (
                overview.notes.map((n) => {
                  const active = n.slug === selectedSlug;
                  return (
                    <button
                      key={n.slug}
                      type="button"
                      onClick={() => void openNote(n.slug)}
                      className={`w-full rounded-xl border p-2.5 text-left transition ${
                        active
                          ? "border-[var(--tone-line)] bg-[var(--tone-soft)] tone-ai"
                          : "border-[var(--cc-border)] bg-[var(--cc-inset)] hover:border-[var(--cc-border-strong)]"
                      }`}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <span className="line-clamp-1 text-xs font-semibold cc-text">{n.title}</span>
                        <span
                          className={`cc-chip shrink-0 py-0 text-[10px] ${
                            n.audience === "dev" ? "tone-held" : "tone-idle"
                          }`}
                        >
                          {n.folder || "index"}
                        </span>
                      </div>
                      <p className="mt-1 line-clamp-2 text-[11px] leading-relaxed cc-muted">{n.snippet}</p>
                      <div className="mt-1.5 flex flex-wrap items-center gap-2 font-mono text-[10px] cc-dim">
                        <span>{n.slug}</span>
                        {n.linkCount > 0 && <span>· {n.linkCount} ลิงก์</span>}
                        {n.mediaCount > 0 && <span>· {n.mediaCount} สื่อ</span>}
                      </div>
                    </button>
                  );
                })
              )}
            </div>
          </div>

          {/* Right Pane: Markdown Note Reader */}
          <div
            ref={readerTopRef}
            className="cc-panel max-h-[76vh] overflow-y-auto p-5 lg:col-span-7 xl:col-span-8"
          >
            {noteLoading ? (
              <div className="py-16 text-center text-xs cc-dim">กำลังเปิดโน้ต...</div>
            ) : noteError ? (
              <div className="rounded-xl border border-[var(--tone-line)] bg-[var(--tone-soft)] p-4 text-xs cc-ink tone-danger">
                {noteError}
              </div>
            ) : !noteDetail ? (
              <div className="py-16 text-center text-xs cc-dim">เลือกโน้ตจากรายการด้านซ้ายเพื่ออ่านรายละเอียด</div>
            ) : (
              <div>
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--cc-border)] pb-3">
                  <div className="flex flex-wrap items-center gap-2">
                    {historyStack.length > 0 && (
                      <button
                        type="button"
                        onClick={handleBackNote}
                        className="cc-btn h-7 px-2.5 text-xs"
                        title="กลับไปโน้ตก่อนหน้า"
                      >
                        <ArrowLeft className="h-3.5 w-3.5" />
                        <span>ย้อนกลับ</span>
                      </button>
                    )}
                    <span className="cc-chip tone-ai py-0.5 text-[11px]">
                      <FolderOpen className="h-3 w-3" />
                      <span>{noteDetail.folderLabel}</span>
                    </span>
                    <span
                      className={`cc-chip py-0.5 text-[11px] ${
                        noteDetail.audience === "dev" ? "tone-held" : "tone-in"
                      }`}
                    >
                      {noteDetail.audience === "dev" ? "โซนเทคนิค (Dev)" : "โซนลูกค้า (User)"}
                    </span>
                  </div>

                  <div className="flex items-center gap-2">
                    {!readOnly && noteDetail.audience === "user" && (
                      <button
                        type="button"
                        onClick={() => openEditorForNote(noteDetail)}
                        className="cc-btn cc-btn--tone tone-live h-7 px-2.5 text-[11px]"
                        title="แก้ไขโน้ตนี้ในคลังสมอง"
                      >
                        <Pencil className="h-3 w-3" />
                        <span>แก้ไขโน้ต</span>
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => void handleCopySlug(noteDetail.slug)}
                      className="cc-btn h-7 px-2.5 font-mono text-[11px]"
                      title="คัดลอก slug ของโน้ต"
                    >
                      <Copy className="h-3 w-3" />
                      <span>{noteDetail.slug}</span>
                    </button>
                    <span className="text-[11px] cc-dim">{formatShortTime(noteDetail.updatedAt)}</span>
                  </div>
                </div>

                {/* Tags & Aliases */}
                {(noteDetail.tags.length > 0 || noteDetail.aliases.length > 0) && (
                  <div className="mt-3 flex flex-wrap items-center gap-1.5">
                    {noteDetail.tags.map((t) => (
                      <span key={`t-${t}`} className="cc-chip tone-idle py-0 text-[10.5px]">
                        <Tag className="h-2.5 w-2.5" />
                        <span>{t}</span>
                      </span>
                    ))}
                    {noteDetail.aliases.slice(0, 6).map((a) => (
                      <span key={`al-${a}`} className="cc-chip tone-live py-0 text-[10.5px]">
                        {a}
                      </span>
                    ))}
                  </div>
                )}

                {/* Main Markdown Content */}
                <div className="mt-4">
                  <SafeMarkdownBody markdown={noteDetail.body} onSelectSlug={(s) => void openNote(s)} />
                </div>

                {/* Videos */}
                {noteDetail.videos.length > 0 && (
                  <div className="mt-5 rounded-xl border border-[var(--cc-border)] bg-[var(--cc-inset)] p-3">
                    <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold cc-text">
                      <Video className="h-3.5 w-3.5 text-[var(--tone)] tone-danger" />
                      <span>วิดีโอสอนใช้งานที่เกี่ยวข้อง ({noteDetail.videos.length})</span>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {noteDetail.videos.map((v) => (
                        <a
                          key={v.url}
                          href={v.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="cc-btn h-7 px-2.5 text-xs tone-live"
                        >
                          <ExternalLink className="h-3 w-3" />
                          <span>{v.title}</span>
                        </a>
                      ))}
                    </div>
                  </div>
                )}

                {/* Connected Notes (Graph Links & Backlinks) */}
                {(noteDetail.outLinks.length > 0 || noteDetail.backlinks.length > 0) && (
                  <div className="mt-5 grid grid-cols-1 gap-3 border-t border-[var(--cc-border)] pt-4 md:grid-cols-2">
                    {noteDetail.outLinks.length > 0 && (
                      <div>
                        <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold cc-text">
                          <Link2 className="h-3.5 w-3.5" />
                          <span>โน้ตที่อ้างถึง ({noteDetail.outLinks.length})</span>
                        </div>
                        <div className="flex flex-wrap gap-1.5">
                          {noteDetail.outLinks.map((lk) => (
                            <button
                              key={`out-${lk.slug}`}
                              type="button"
                              onClick={() => void openNote(lk.slug)}
                              className="cc-btn h-7 px-2.5 text-[11px]"
                            >
                              <FileText className="h-3 w-3 shrink-0" />
                              <span className="max-w-[220px] truncate">{lk.title}</span>
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                    {noteDetail.backlinks.length > 0 && (
                      <div>
                        <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold cc-text">
                          <Layers className="h-3.5 w-3.5" />
                          <span>โน้ตที่ลิงก์มาหน้านี้ ({noteDetail.backlinks.length})</span>
                        </div>
                        <div className="flex flex-wrap gap-1.5">
                          {noteDetail.backlinks.map((lk) => (
                            <button
                              key={`in-${lk.slug}`}
                              type="button"
                              onClick={() => void openNote(lk.slug)}
                              className="cc-btn h-7 px-2.5 text-[11px]"
                            >
                              <FileText className="h-3 w-3 shrink-0" />
                              <span className="max-w-[220px] truncate">{lk.title}</span>
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* TAB 2: 4-DIGIT MENU CATALOG */}
      {subTab === "menus" && (
        <div className="cc-panel p-4">
          <div className="flex flex-wrap items-center gap-2.5">
            <div className="relative min-w-[240px] flex-1">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 cc-dim" />
              <input
                type="text"
                value={menuQuery}
                onChange={(e) => setMenuQuery(e.target.value)}
                placeholder="ค้นหารหัสเมนู 4 หลัก (เช่น 4013), รหัสเก่า (เช่น 4.13) หรือชื่อหน้าจอ..."
                className="cc-input h-9 w-full pl-9 pr-8 text-xs"
              />
              {menuQuery && (
                <button
                  type="button"
                  onClick={() => setMenuQuery("")}
                  aria-label="ล้างคำค้นเมนู"
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 cc-dim hover:cc-text"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
            <span className="cc-chip tone-live h-8 px-2.5 font-mono text-xs tabular-nums">
              พบ {menusData?.matched ?? 0} / {menusData?.total ?? stats?.totalMenus ?? 0} เมนู
            </span>
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-1.5">
            <button
              type="button"
              onClick={() => setMenuGroup("")}
              className={`cc-btn h-7 px-2.5 text-xs ${menuGroup === "" ? "cc-btn--tone tone-live" : ""}`}
            >
              <span>ทุกหมวด (1–9)</span>
            </button>
            {(menusData?.groups || []).map((g) => (
              <button
                key={g.code}
                type="button"
                onClick={() => setMenuGroup(g.code)}
                className={`cc-btn h-7 px-2.5 text-xs ${menuGroup === g.code ? "cc-btn--tone tone-ai" : ""}`}
              >
                <span>{g.label}</span>
                <span className="font-mono text-[10px] opacity-75 tabular-nums">{g.count}</span>
              </button>
            ))}
          </div>

          <div className="mt-3 max-h-[60vh] overflow-auto rounded-xl border border-[var(--cc-border)]">
            <table className="w-full border-collapse text-left text-xs">
              <thead className="sticky top-0 bg-[var(--cc-inset)]">
                <tr>
                  <th className="border-b border-[var(--cc-border)] px-3 py-2.5 font-semibold cc-text">รหัส 4 หลัก</th>
                  <th className="border-b border-[var(--cc-border)] px-3 py-2.5 font-semibold cc-text">รหัสเดิม</th>
                  <th className="border-b border-[var(--cc-border)] px-3 py-2.5 font-semibold cc-text">ชื่อหน้าจอ &amp; เส้นทางเมนู</th>
                  <th className="border-b border-[var(--cc-border)] px-3 py-2.5 font-semibold cc-text">รุ่นที่รองรับ</th>
                  <th className="border-b border-[var(--cc-border)] px-3 py-2.5 text-right font-semibold cc-text">โน้ตหมวด</th>
                </tr>
              </thead>
              <tbody>
                {menusLoading ? (
                  <tr>
                    <td colSpan={5} className="px-3 py-8 text-center cc-dim">
                      กำลังค้นหาสารบัญเมนู...
                    </td>
                  </tr>
                ) : !(menusData?.menus || []).length ? (
                  <tr>
                    <td colSpan={5} className="px-3 py-8 text-center cc-dim">
                      ไม่พบเมนูที่ตรงคำค้น
                    </td>
                  </tr>
                ) : (
                  (menusData?.menus || []).map((m) => (
                    <tr
                      key={`${m.code}-${m.legacy}`}
                      className="border-b border-[var(--cc-border)] last:border-b-0 hover:bg-[var(--cc-chip)]"
                    >
                      <td className="px-3 py-2 font-mono font-bold tabular-nums tone-ai cc-ink">{m.code}</td>
                      <td className="px-3 py-2 font-mono tabular-nums cc-dim">{m.legacy || "—"}</td>
                      <td className="px-3 py-2">
                        <div className="font-semibold cc-text">{m.title}</div>
                        {m.path && <div className="text-[11px] cc-dim">{m.path}</div>}
                      </td>
                      <td className="px-3 py-2 cc-muted">{m.edition || "—"}</td>
                      <td className="px-3 py-2 text-right">
                        <button
                          type="button"
                          onClick={() => void openNote(m.catalogSlug)}
                          className="cc-btn h-6 px-2 text-[11px]"
                        >
                          <span>เปิดดู</span>
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* TAB 3: BRAIN WISHLIST */}
      {subTab === "wishlist" && (
        <div className="cc-panel p-4">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--cc-border)] pb-3">
            <div>
              <h2 className="text-sm font-semibold cc-text">
                หัวข้อที่รอเติมข้อมูลจริงเข้าสมอง (`bcbrain/vault/`)
              </h2>
              <p className="mt-0.5 text-xs cc-muted">
                รวบรวมจากคำถามจริงในสายโทรศัพท์ของลูกค้าที่ยังไม่มีคำตอบตรงเป๊ะในคลังความรู้
              </p>
            </div>
            <div className="flex items-center gap-2">
              <span className="cc-chip tone-held h-7 px-2.5 text-xs tabular-nums">
                รอเติม {overview?.wishlist.pendingCount ?? 0}
              </span>
              <span className="cc-chip tone-in h-7 px-2.5 text-xs tabular-nums">
                เสร็จแล้ว {overview?.wishlist.doneCount ?? 0}
              </span>
            </div>
          </div>

          <div className="mt-3 space-y-2.5">
            {(overview?.wishlist.items || []).length === 0 ? (
              <div className="py-10 text-center text-xs cc-dim">ไม่มีรายการรอเติมในขณะนี้</div>
            ) : (
              (overview?.wishlist.items || []).map((item, idx) => (
                <div
                  key={`${item.slug || idx}`}
                  className="rounded-xl border border-[var(--cc-border)] bg-[var(--cc-inset)] p-3.5"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      {item.done ? (
                        <CheckCircle2 className="h-4 w-4 shrink-0 text-[var(--tone)] tone-in" />
                      ) : (
                        <Sparkles className="h-4 w-4 shrink-0 text-[var(--tone)] tone-held" />
                      )}
                      <span className="text-xs font-semibold cc-text">{item.title}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      {item.slug && (
                        <span className="cc-chip tone-idle py-0 font-mono text-[10.5px]">{item.slug}</span>
                      )}
                      {!readOnly && !item.done && (
                        <button
                          type="button"
                          onClick={() => openEditorForWishlist(item)}
                          className="cc-btn cc-btn--tone tone-in h-7 px-2.5 text-[11px]"
                        >
                          <Plus className="h-3 w-3" />
                          <span>เติมลงสมอง</span>
                        </button>
                      )}
                    </div>
                  </div>
                  {item.details.length > 0 && (
                    <ul className="mt-2 space-y-1 pl-6 text-xs leading-relaxed cc-muted list-disc">
                      {item.details.map((d, di) => (
                        <li key={di}>{d}</li>
                      ))}
                    </ul>
                  )}
                </div>
              ))
            )}
          </div>
        </div>
      )}

      {/* TAB 4: BRAIN MISS LOG (ABSTAINED / LOW-COSINE CALL QUERIES) */}
      {subTab === "misses" && (
        <div className="cc-panel p-4">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--cc-border)] pb-3">
            <div>
              <h2 className="text-sm font-semibold cc-text">
                คำถามจากสายจริงที่สมองยังตอบไม่ตรง (Brain Miss Log)
              </h2>
              <p className="mt-0.5 text-xs cc-muted">
                จัดอันดับตามจำนวนครั้งที่ลูกค้าถามซ้ำแล้วติด Abstain เพื่อเติมข้อมูลเข้าสมองและซิงก์ pgvector ต่อเนื่อง
              </p>
            </div>
            <div className="flex items-center gap-2">
              <span className="cc-chip tone-danger h-7 px-2.5 text-xs tabular-nums">
                {overview?.misses?.uniqueCount ?? 0} หัวข้อ ({overview?.misses?.totalEvents ?? 0} ครั้ง)
              </span>
            </div>
          </div>

          <div className="mt-3 space-y-2.5">
            {(overview?.misses?.items || []).length === 0 ? (
              <div className="py-10 text-center text-xs cc-dim">
                ยังไม่มีคำถามที่ติด Abstain สะสมใหม่ — ระบบจะบันทึกอัตโนมัติเมื่อมีสายโทรเข้าถามเรื่องที่ยังไม่มีในสมอง
              </div>
            ) : (
              (overview?.misses?.items || []).map((m) => (
                <div
                  key={m.rawKey}
                  className="rounded-xl border border-[var(--cc-border)] bg-[var(--cc-inset)] p-3.5"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex min-w-0 flex-wrap items-center gap-2">
                      <span className="cc-chip tone-danger py-0 font-mono text-[10.5px] tabular-nums">
                        ×{m.count}
                      </span>
                      <span className="text-xs font-semibold cc-text">{m.query}</span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <span className="text-[11px] cc-dim">{formatShortTime(m.lastAt)}</span>
                      {!readOnly && (
                        <>
                          <button
                            type="button"
                            onClick={() => openEditorForMiss(m)}
                            className="cc-btn cc-btn--tone tone-in h-7 px-2.5 text-[11px]"
                          >
                            <Plus className="h-3 w-3" />
                            <span>เติมลงสมอง</span>
                          </button>
                          <button
                            type="button"
                            onClick={() => void handleClearMiss(m.query)}
                            className="cc-btn h-7 w-7 px-0 tone-danger"
                            title="ล้างรายการนี้"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                  <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[11px] cc-dim">
                    {m.ctxTopic && <span>บริบทเดิม: {m.ctxTopic}</span>}
                    {m.topSlug && <span className="font-mono">ใกล้สุด: {m.topSlug}</span>}
                    {m.topCosine > 0 && <span className="font-mono">cosine={m.topCosine.toFixed(2)}</span>}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      )}

      {/* ADMIN-ONLY NOTE EDITOR MODAL */}
      {editorOpen && !readOnly && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-label="บันทึกข้อมูลเข้าสมอง BC">
          <button type="button" aria-label="ปิดหน้าต่าง" onClick={() => setEditorOpen(false)} className="cc-overlay absolute inset-0 cursor-default" />
          <form
            onSubmit={(e) => void handleSaveNote(e)}
            className="cc-panel relative z-10 flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden p-5"
          >
            <div className="flex items-center justify-between gap-2 border-b border-[var(--cc-border)] pb-3">
              <div className="flex items-center gap-2">
                <Brain className="h-4 w-4 text-[var(--tone)] tone-ai" />
                <h3 className="text-sm font-bold cc-text">
                  {edResolveWishlist ? "เติมข้อเท็จจริงปิดคิวรอเติมเข้าสมอง BC" : "เพิ่ม / แก้ไขโน้ตสมอง BC (โซนลูกค้า)"}
                </h3>
              </div>
              <button type="button" onClick={() => setEditorOpen(false)} className="cc-btn h-7 w-7 px-0">
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="cc-scroll mt-3 flex-1 space-y-3 overflow-y-auto pr-1 text-xs">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div>
                  <label className="mb-1 block font-medium cc-muted">รหัสโน้ต (slug ภาษาอังกฤษตัวเล็ก)</label>
                  <input
                    type="text"
                    required
                    value={edSlug}
                    onChange={(e) => setEdSlug(e.target.value)}
                    placeholder="เช่น faq-promotion-10-types"
                    className="cc-input h-9 w-full px-3 font-mono text-xs"
                  />
                </div>
                <div>
                  <label className="mb-1 block font-medium cc-muted">หมวดโซนลูกค้า (ปลอดศัพท์โค้ด/SQL)</label>
                  <select
                    value={edFolder}
                    onChange={(e) => setEdFolder(e.target.value)}
                    className="cc-input h-9 w-full px-3 text-xs"
                  >
                    {USER_WRITE_FOLDERS.map((f) => (
                      <option key={f.value} value={f.value}>
                        {f.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div>
                <label className="mb-1 block font-medium cc-muted">หัวข้อโน้ต (ภาษาไทยชัดเจน)</label>
                <input
                  type="text"
                  required
                  value={edTitle}
                  onChange={(e) => setEdTitle(e.target.value)}
                  placeholder="เช่น รายละเอียดโปรโมชันอัตโนมัติครบทั้ง 10 รูปแบบ"
                  className="cc-input h-9 w-full px-3 text-xs"
                />
              </div>

              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div>
                  <label className="mb-1 block font-medium cc-muted">คำพูด/คำถามที่ลูกค้ามักถาม (บรรทัดละ 1 ประโยค)</label>
                  <textarea
                    rows={2}
                    value={edAliases}
                    onChange={(e) => setEdAliases(e.target.value)}
                    placeholder="โปรโมชั่นมีกี่แบบ&#10;ซื้อสองแถมหนึ่งตั้งค่ายังไง"
                    className="cc-input w-full px-3 py-2 text-xs"
                  />
                </div>
                <div>
                  <label className="mb-1 block font-medium cc-muted">แท็กค้นหา (คั่นด้วยจุลภาค)</label>
                  <textarea
                    rows={2}
                    value={edTags}
                    onChange={(e) => setEdTags(e.target.value)}
                    placeholder="faq, promotion, pos"
                    className="cc-input w-full px-3 py-2 text-xs"
                  />
                </div>
              </div>

              <div>
                <label className="mb-1 block font-medium cc-muted">
                  เนื้อหาข้อเท็จจริงสำหรับตอบลูกค้าทางเสียง (ระบุขั้นตอนและรหัสเมนู 4 หลัก ไม่มีภาพ/YouTube)
                </label>
                <textarea
                  rows={8}
                  required
                  value={edBody}
                  onChange={(e) => setEdBody(e.target.value)}
                  placeholder="พิมพ์ข้อเท็จจริงและขั้นตอนที่ต้องการให้ J.A.R.V.I.S ใช้ตอบลูกค้า..."
                  className="cc-input w-full px-3 py-2 font-sans text-xs leading-relaxed"
                />
              </div>

              {edError && (
                <div className="rounded-lg border border-[var(--tone-line)] bg-[var(--tone-soft)] px-3 py-2 text-xs cc-ink tone-danger">
                  {edError}
                </div>
              )}
            </div>

            <div className="mt-4 flex flex-wrap items-center justify-between gap-2 border-t border-[var(--cc-border)] pt-3">
              <span className="text-[11px] cc-dim">
                บันทึกแล้ว Hot-Reload เข้า RAM ใน 3 วิ + ซิงก์ PostgreSQL pgvector อัตโนมัติ
              </span>
              <div className="flex items-center gap-2">
                <button type="button" onClick={() => setEditorOpen(false)} className="cc-btn h-8 px-3 text-xs">
                  <span>ยกเลิก</span>
                </button>
                <button type="submit" disabled={edSaving} className="cc-btn cc-btn--tone tone-in h-8 px-3.5 text-xs">
                  <Save className="h-3.5 w-3.5" />
                  <span>{edSaving ? "กำลังบันทึก..." : "บันทึกเข้าสมอง + ซิงก์ pgvector"}</span>
                </button>
              </div>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
