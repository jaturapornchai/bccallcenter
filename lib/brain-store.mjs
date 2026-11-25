import fs from "node:fs";
import path from "node:path";
import { BrainPgVectorNodeClient, normalizeThaiBrainQuery } from "./brain-pgvector.mjs";

const DEV_FOLDERS = new Set(["api", "architecture", "dev-playbook", "queries", "schema"]);
const VALID_SLUG_RE = /^[a-z0-9][a-z0-9._-]{0,120}$/i;
const MENU_ROW_RE = /^\|\s*\*\*(\d{4})\*\*\s*\|\s*\*\*([^|*]+)\*\*\s*\|\s*\*\*([^|*]+)\*\*(?:<br>([^|]+))?\s*\|\s*([^|]+)\s*\|/;
const WIKILINK_RE = /\[\[(?:[a-z_]+::)?([^\]|#]+?)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]/g;
const MD_IMAGE_RE = /!\[([^\]]*)\]\((https:\/\/[^)\s]+)\)/g;
const MD_LINK_RE = /(?<!!)\[([^\]]+)\]\((https:\/\/[^)\s]+)\)/g;
const YT_URL_RE = /(?:youtube\.com\/(?:watch\?[^\s)]*v=|embed\/|shorts\/|playlist\?)|youtu\.be\/)/i;
const SOURCE_LINE_RE = /^(?:sources|slug):\s*.*\r?\n?/gm;
const MEDIA_SECTION_RE = /^##\s+(?:สื่อภาพและวิดีโอ|วิดีโอสอนใช้งาน|ภาพประกอบ)(?:\s|$)/;
const YT_MD_LINK_RE = /\[[^\]]*\]\(https?:\/\/(?:www\.)?(?:youtube\.com|youtu\.be)\/[^)]*\)/gi;
const PLAIN_YT_URL_RE = /https?:\/\/(?:www\.)?(?:youtube\.com|youtu\.be)\/\S+/gi;
const MOBILE_RE = /\b0[689]\d[- ]?\d{3}[- ]?\d{4}\b/g;

function stripVoiceMedia(body) {
  const parts = String(body || "").replace(/\r\n/g, "\n").split("\n## ");
  const kept = [parts[0]];
  for (let i = 1; i < parts.length; i++) {
    const sec = `## ${parts[i]}`;
    if (MEDIA_SECTION_RE.test(sec.trimStart())) continue;
    kept.push(sec);
  }
  return kept
    .join("\n")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[🎬[^\]]*\]\([^)]*\)/g, "")
    .replace(YT_MD_LINK_RE, "")
    .replace(PLAIN_YT_URL_RE, "")
    .replace(/^(?:youtube_videos|images):.*\r?\n?/gm, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export const FOLDER_LABELS_TH = {
  "": "หน้าสารบัญหลัก",
  start: "เริ่มต้น & สารบัญเมนู",
  howto: "คู่มือใช้งานทีละขั้น",
  troubleshoot: "แก้ไขปัญหา & Error",
  sales: "งานขาย & แพ็กเกจ",
  systems: "ภาพรวมโมดูลระบบ",
  reports: "รายงาน & งบการเงิน",
  faq: "คำถามที่พบบ่อย",
  glossary: "คำศัพท์บัญชี/ระบบ",
  doctypes: "ประเภทเอกสาร",
  playbook: "แนวทาง Call Center",
  schema: "โครงสร้างฐานข้อมูล (Dev)",
  queries: "คำสั่ง SQL มาตรฐาน (Dev)",
  api: "API & Endpoints (Dev)",
  architecture: "สถาปัตยกรรมระบบ (Dev)",
  "dev-playbook": "แนวทางพัฒนา (Dev)",
};

export const MENU_GROUPS_TH = {
  "1": "1xxx ตั้งค่า & ระบบ",
  "2": "2xxx สินค้า & คลัง",
  "3": "3xxx จัดซื้อ",
  "4": "4xxx ขาย & บิล",
  "5": "5xxx เจ้าหนี้",
  "6": "6xxx ลูกหนี้",
  "7": "7xxx เงินสด & ธนาคาร",
  "8": "8xxx สินทรัพย์ถาวร",
  "9": "9xxx บัญชีแยกประเภท",
};

function parseFrontmatter(raw) {
  const text = String(raw || "").replace(/\r\n/g, "\n");
  if (!text.startsWith("---\n")) return { meta: {}, body: text };
  const end = text.indexOf("\n---\n", 4);
  if (end < 0) return { meta: {}, body: text };
  const fmBlock = text.slice(4, end);
  const body = text.slice(end + 5).replace(/^\n+/, "");
  const meta = {};
  let curListKey = null;
  for (const line of fmBlock.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    if (trimmed.startsWith("- ") && curListKey) {
      const val = trimmed.slice(2).trim().replace(/^["']|["']$/g, "");
      if (val) {
        if (!Array.isArray(meta[curListKey])) meta[curListKey] = [];
        meta[curListKey].push(val);
      }
      continue;
    }
    curListKey = null;
    if (line.startsWith(" ") || line.startsWith("\t") || !line.includes(":")) continue;
    const idx = line.indexOf(":");
    const key = line.slice(0, idx).trim();
    const val = line.slice(idx + 1).trim();
    if (!val) {
      curListKey = key;
      meta[key] = [];
    } else if (val.startsWith("[") && val.endsWith("]")) {
      meta[key] = val
        .slice(1, -1)
        .split(",")
        .map((x) => x.trim().replace(/^["']|["']$/g, ""))
        .filter(Boolean);
    } else {
      meta[key] = val.replace(/^["']|["']$/g, "");
    }
  }
  return { meta, body };
}

function extractHeading(body) {
  for (const ln of String(body || "").split("\n")) {
    const t = ln.trim();
    if (t.startsWith("#")) return t.replace(/^#+\s*/, "").trim();
  }
  return "";
}

function makeSnippet(body, maxLen = 190) {
  const cleaned = String(body || "")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[\[(?:[a-z_]+::)?([^\]|#]+?)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]/g, (_, s, lbl) => lbl || s)
    .replace(/^#+\s+.*$/gm, "")
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/\|[-:\s|]+\|/g, " ")
    .replace(/[*_`>#]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return cleaned.length > maxLen ? `${cleaned.slice(0, maxLen).trimEnd()}…` : cleaned;
}

function extractMedia(body) {
  const images = [];
  const videos = [];
  const seenImg = new Set();
  const seenVid = new Set();
  for (const m of String(body || "").matchAll(MD_IMAGE_RE)) {
    const url = m[2].trim();
    if (!seenImg.has(url)) {
      seenImg.add(url);
      images.push({ alt: m[1].trim() || "ภาพประกอบ", url });
    }
  }
  for (const m of String(body || "").matchAll(MD_LINK_RE)) {
    const url = m[2].trim();
    if (YT_URL_RE.test(url) && !seenVid.has(url)) {
      seenVid.add(url);
      videos.push({ title: m[1].replace(/^🎬\s*/, "").trim() || "วิดีโอสอนใช้งาน", url });
    }
  }
  return { images, videos };
}

function maskMobileForViewer(text) {
  return String(text || "").replace(MOBILE_RE, "•••-•••-••••");
}

function parseWishlistFile(wishlistPath) {
  if (!fs.existsSync(wishlistPath)) {
    return { pendingCount: 0, doneCount: 0, items: [] };
  }
  try {
    const raw = fs.readFileSync(wishlistPath, "utf8").replace(/\r\n/g, "\n");
    const lines = raw.split("\n");
    const items = [];
    let current = null;
    for (const line of lines) {
      const m = line.match(/^- \[([ xX])\]\s+\*\*(.+?)\*\*(.*)$/);
      if (m) {
        if (current) items.push(current);
        const done = m[1].toLowerCase() === "x";
        const slugMatch = m[3].match(/`([a-z0-9-]+)`/i);
        current = {
          done,
          title: m[2].trim(),
          slug: slugMatch ? slugMatch[1] : "",
          details: [],
        };
      } else if (current && /^\s{2,}-\s+/.test(line)) {
        const clean = line.replace(/^\s+-\s+/, "").replace(/\*\*/g, "").trim();
        if (clean) current.details.push(clean);
      } else if (current && /^##\s+/.test(line)) {
        items.push(current);
        current = null;
      }
    }
    if (current) items.push(current);
    const pendingCount = items.filter((i) => !i.done).length;
    const doneCount = items.filter((i) => i.done).length;
    return { pendingCount, doneCount, items };
  } catch {
    return { pendingCount: 0, doneCount: 0, items: [] };
  }
}

export class LocalBrainStore {
  constructor({
    vaultDir = path.resolve(process.cwd(), "bcbrain", "vault"),
    wishlistPath = path.resolve(process.cwd(), "docs", "brain-wishlist.md"),
    watchIntervalMs = 3000,
  } = {}) {
    this.vaultDir = path.resolve(vaultDir);
    this.wishlistPath = path.resolve(wishlistPath);
    this.watchIntervalMs = Math.max(1000, Number(watchIntervalMs) || 3000);
    this.notesBySlug = new Map();
    this.notesList = [];
    this.menusList = [];
    this.wishlist = { pendingCount: 0, doneCount: 0, items: [] };
    this.lastWatchAt = 0;
    this.lastSig = "";
    this.loadedAt = null;
    this.pgVector = new BrainPgVectorNodeClient();
    this.reload(true);
  }

  _computeDirSig() {
    let count = 0;
    let maxMtime = 0;
    const walk = (dir) => {
      let entries;
      try {
        entries = fs.readdirSync(dir, { withFileTypes: true });
      } catch {
        return;
      }
      for (const e of entries) {
        const p = path.join(dir, e.name);
        if (e.isDirectory()) walk(p);
        else if (e.name.endsWith(".md")) {
          count++;
          try {
            const mt = fs.statSync(p).mtimeMs;
            if (mt > maxMtime) maxMtime = mt;
          } catch {}
        }
      }
    };
    walk(this.vaultDir);
    let wlMtime = 0;
    try {
      wlMtime = fs.statSync(this.wishlistPath).mtimeMs;
    } catch {}
    return `${count}:${Math.round(maxMtime)}:${Math.round(wlMtime)}`;
  }

  maybeReload() {
    const now = Date.now();
    if (now - this.lastWatchAt < this.watchIntervalMs && this.notesList.length > 0) return false;
    this.lastWatchAt = now;
    const sig = this._computeDirSig();
    if (sig === this.lastSig && this.notesList.length > 0) return false;
    return this.reload(true, sig);
  }

  reload(force = false, precomputedSig = null) {
    if (!fs.existsSync(this.vaultDir)) {
      this.notesBySlug = new Map();
      this.notesList = [];
      this.menusList = [];
      this.wishlist = parseWishlistFile(this.wishlistPath);
      this.loadedAt = new Date().toISOString();
      return false;
    }
    const sig = precomputedSig || this._computeDirSig();
    if (!force && sig === this.lastSig && this.notesList.length > 0) return false;

    const bySlug = new Map();
    const menus = [];
    const walk = (dir) => {
      const entries = fs.readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name));
      for (const e of entries) {
        const full = path.join(dir, e.name);
        if (e.isDirectory()) {
          walk(full);
          continue;
        }
        if (!e.name.endsWith(".md")) continue;
        const rel = path.relative(this.vaultDir, full).replace(/\\/g, "/");
        const folder = rel.includes("/") ? rel.split("/", 1)[0] : "";
        const slug = e.name.slice(0, -3);
        let raw = "";
        let mtimeIso = "";
        try {
          raw = fs.readFileSync(full, "utf8");
          mtimeIso = fs.statSync(full).mtime.toISOString();
        } catch {
          continue;
        }
        const { meta, body } = parseFrontmatter(raw);
        const fmAud = String(meta.audience || "").trim().toLowerCase();
        const isDev = DEV_FOLDERS.has(folder) || fmAud === "dev" || slug === "playbook-dev";
        const title = String(meta.title || "").trim() || extractHeading(body) || slug;
        const tags = Array.isArray(meta.tags) ? meta.tags.map((x) => String(x).trim()).filter(Boolean) : [];
        const aliases = Array.isArray(meta.aliases) ? meta.aliases.map((x) => String(x).trim()).filter(Boolean) : [];
        const cleanBody = stripVoiceMedia(body.replace(SOURCE_LINE_RE, "").trim());
        const images = [];
        const videos = [];
        const outLinks = [];
        const seenLink = new Set();
        for (const m of body.matchAll(WIKILINK_RE)) {
          const target = m[1].trim();
          if (target && target !== slug && !seenLink.has(target)) {
            seenLink.add(target);
            outLinks.push(target);
          }
        }
        const rec = {
          slug,
          folder,
          folderLabel: FOLDER_LABELS_TH[folder] || folder || "ทั่วไป",
          audience: isDev ? "dev" : "user",
          isDev,
          title,
          tags,
          aliases,
          body: cleanBody,
          rawBody: body,
          snippet: makeSnippet(cleanBody),
          images,
          videos,
          outLinks,
          backlinks: [],
          updatedAt: mtimeIso,
          hayTitle: `${title} ${slug} ${aliases.join(" ")} ${tags.join(" ")}`.toLowerCase(),
          hayBody: cleanBody.toLowerCase(),
        };
        bySlug.set(slug, rec);

        if (slug.startsWith("menu-cat-")) {
          for (const ln of body.split("\n")) {
            const mm = ln.trim().match(MENU_ROW_RE);
            if (!mm) continue;
            menus.push({
              code: (mm[1] || "").trim(),
              legacy: (mm[2] || "").trim(),
              title: (mm[3] || "").trim(),
              path: (mm[4] || "").trim(),
              edition: (mm[5] || "").trim(),
              group: (mm[1] || "").trim().slice(0, 1),
              catalogSlug: slug,
            });
          }
        }
      }
    };
    walk(this.vaultDir);

    for (const [slug, note] of bySlug.entries()) {
      for (const target of note.outLinks) {
        const t = bySlug.get(target);
        if (t && !t.backlinks.includes(slug)) {
          t.backlinks.push(slug);
        }
      }
    }

    menus.sort((a, b) => a.code.localeCompare(b.code));
    this.notesBySlug = bySlug;
    this.notesList = Array.from(bySlug.values()).sort((a, b) => {
      if (a.folder !== b.folder) return a.folder.localeCompare(b.folder);
      return a.title.localeCompare(b.title, "th");
    });
    this.menusList = menus;
    this.wishlist = parseWishlistFile(this.wishlistPath);
    this.lastSig = sig;
    this.lastWatchAt = Date.now();
    this.loadedAt = new Date().toISOString();
    return true;
  }

  getOverview({ isAdmin = false, audience = "user", folder = "", q = "", limit = 120 } = {}) {
    this.maybeReload();
    const effAudience = isAdmin && ["user", "dev", "all"].includes(audience) ? audience : "user";
    const fFilter = String(folder || "").trim().toLowerCase();
    const query = normalizeThaiBrainQuery(q);
    const tokens = query
      ? query
          .split(/\s+/)
          .map((t) => t.trim())
          .filter((t) => t.length >= 2 || /^\d{1,4}$/.test(t))
      : [];

    const allowedNotes = this.notesList.filter((n) => {
      if (!isAdmin && n.isDev) return false;
      if (effAudience === "user") return !n.isDev;
      if (effAudience === "dev") return n.isDev;
      return true;
    });

    const folderMap = new Map();
    for (const n of allowedNotes) {
      const k = n.folder;
      folderMap.set(k, (folderMap.get(k) || 0) + 1);
    }
    const folders = Array.from(folderMap.entries())
      .map(([name, count]) => ({
        name,
        label: FOLDER_LABELS_TH[name] || name || "ทั่วไป",
        count,
        isDev: DEV_FOLDERS.has(name),
      }))
      .sort((a, b) => {
        if (a.isDev !== b.isDev) return a.isDev ? 1 : -1;
        return b.count - a.count;
      });

    let matched = allowedNotes.filter((n) => !fFilter || n.folder.toLowerCase() === fFilter);
    if (tokens.length > 0) {
      const scored = [];
      for (const n of matched) {
        let score = 0;
        if (n.hayTitle.includes(query)) score += 30;
        for (const t of tokens) {
          if (n.hayTitle.includes(t)) score += 12;
          else if (n.hayBody.includes(t)) score += 4;
        }
        if (score > 0) scored.push({ n, score });
      }
      scored.sort((a, b) => b.score - a.score || a.n.slug.localeCompare(b.n.slug));
      matched = scored.map((x) => x.n);
    }

    const cap = Math.max(20, Math.min(Number(limit) || 120, 400));
    const notes = matched.slice(0, cap).map((n) => ({
      slug: n.slug,
      title: n.title,
      folder: n.folder,
      folderLabel: n.folderLabel,
      audience: n.audience,
      tags: n.tags.slice(0, 6),
      aliases: n.aliases.slice(0, 4),
      snippet: isAdmin ? n.snippet : maskMobileForViewer(n.snippet),
      updatedAt: n.updatedAt,
      mediaCount: n.images.length + n.videos.length,
      linkCount: n.outLinks.length,
    }));

    const userCount = this.notesList.filter((n) => !n.isDev).length;
    const devCount = this.notesList.length - userCount;

    return {
      ok: true,
      mode: "embedded-local",
      isAdmin: Boolean(isAdmin),
      audience: effAudience,
      loadedAt: this.loadedAt,
      stats: {
        totalNotes: isAdmin ? this.notesList.length : userCount,
        userNotes: userCount,
        devNotes: isAdmin ? devCount : 0,
        totalMenus: this.menusList.length,
        matchedNotes: matched.length,
        pendingWishlist: this.wishlist.pendingCount,
      },
      folders,
      notes,
      wishlist: this.wishlist,
    };
  }

  async getOverviewHybrid({ isAdmin = false, audience = "user", folder = "", q = "", limit = 120 } = {}) {
    const base = this.getOverview({ isAdmin, audience, folder, q, limit });
    const query = String(q || "").trim();
    const effAudience = base.audience;
    const fFilter = String(folder || "").trim().toLowerCase();

    let vectorStats = {
      enabled: Boolean(this.pgVector?.enabled),
      ready: false,
      chunks: 0,
      notes: 0,
      model: this.pgVector?.embedModel || "bge-m3:latest",
      tokenizer: "pythainlp+bc-dict",
    };

    try {
      const [statsRes, pgHits] = await Promise.all([
        this.pgVector ? this.pgVector.getStats() : Promise.resolve(vectorStats),
        query && this.pgVector
          ? this.pgVector.hybridSearch({ query, audience: effAudience, folder: fFilter, limit: 50 })
          : Promise.resolve(null),
      ]);
      if (statsRes) vectorStats = statsRes;

      if (query && Array.isArray(pgHits) && pgHits.length > 0) {
        const cap = Math.max(20, Math.min(Number(limit) || 120, 400));
        const rrfMap = new Map();
        const bestChunkBySlug = new Map();

        base.notes.forEach((n, idx) => {
          rrfMap.set(n.slug, (rrfMap.get(n.slug) || 0) + 0.45 / (30 + idx + 1));
        });
        pgHits.forEach((h, idx) => {
          const note = this.notesBySlug.get(h.slug);
          if (!note) return;
          if (!isAdmin && note.isDev) return;
          if (effAudience === "user" && note.isDev) return;
          if (effAudience === "dev" && !note.isDev) return;
          if (fFilter && note.folder.toLowerCase() !== fFilter) return;
          rrfMap.set(
            h.slug,
            (rrfMap.get(h.slug) || 0) + 0.55 / (30 + idx + 1) + Math.max(0, h.cosine) * 0.015
          );
          if (!bestChunkBySlug.has(h.slug) && h.content) {
            bestChunkBySlug.set(h.slug, h);
          }
        });

        const mergedSlugs = Array.from(rrfMap.entries())
          .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
          .map(([slug]) => slug);

        const mergedNotes = mergedSlugs
          .slice(0, cap)
          .map((slug) => {
            const n = this.notesBySlug.get(slug);
            if (!n) return null;
            const ch = bestChunkBySlug.get(slug);
            const rawSnippet = ch?.content ? makeSnippet(ch.content, 190) : n.snippet;
            return {
              slug: n.slug,
              title: n.title,
              folder: n.folder,
              folderLabel: n.folderLabel,
              audience: n.audience,
              tags: n.tags.slice(0, 6),
              aliases: n.aliases.slice(0, 4),
              snippet: isAdmin ? rawSnippet : maskMobileForViewer(rawSnippet),
              updatedAt: n.updatedAt,
              mediaCount: n.images.length + n.videos.length,
              linkCount: n.outLinks.length,
            };
          })
          .filter(Boolean);

        base.notes = mergedNotes;
        base.stats.matchedNotes = mergedSlugs.length;
        base.mode = "embedded-hybrid-pgvector";
      }
    } catch {
      // Fail-soft to in-memory results
    }

    base.stats.vector = vectorStats;
    return base;
  }

  getNote(slug, { isAdmin = false } = {}) {
    this.maybeReload();
    const clean = String(slug || "").trim();
    if (!clean || !VALID_SLUG_RE.test(clean)) {
      return { ok: false, status: 400, error: "รหัสโน้ตไม่ถูกต้อง" };
    }
    const note = this.notesBySlug.get(clean);
    if (!note) {
      return { ok: false, status: 404, error: "ไม่พบโน้ตที่ระบุในคลังสมอง" };
    }
    if (!isAdmin && note.isDev) {
      return { ok: false, status: 403, error: "โน้ตหมวดเทคนิคสงวนสิทธิ์เฉพาะผู้ดูแลระบบ" };
    }
    const filterLinks = (slugs) =>
      slugs
        .map((s) => this.notesBySlug.get(s))
        .filter((t) => t && (isAdmin || !t.isDev))
        .map((t) => ({ slug: t.slug, title: t.title, folder: t.folder, audience: t.audience }));

    return {
      ok: true,
      status: 200,
      note: {
        slug: note.slug,
        title: note.title,
        folder: note.folder,
        folderLabel: note.folderLabel,
        audience: note.audience,
        tags: note.tags,
        aliases: note.aliases,
        body: isAdmin ? note.body : maskMobileForViewer(note.body),
        images: note.images,
        videos: note.videos,
        outLinks: filterLinks(note.outLinks),
        backlinks: filterLinks(note.backlinks).slice(0, 20),
        updatedAt: note.updatedAt,
      },
    };
  }

  searchMenus({ q = "", group = "", limit = 100 } = {}) {
    this.maybeReload();
    const query = String(q || "").trim().toLowerCase();
    const grp = String(group || "").trim();
    const tokens = query ? query.split(/\s+/).filter(Boolean) : [];
    let list = this.menusList;
    if (grp && MENU_GROUPS_TH[grp]) {
      list = list.filter((m) => m.group === grp);
    }
    if (tokens.length > 0) {
      const scored = [];
      for (const m of list) {
        let score = 0;
        if (m.code === query || m.legacy === query) score += 100;
        else if (m.code.includes(query)) score += 50;
        const hay = `${m.code} ${m.legacy} ${m.title} ${m.path} ${m.edition}`.toLowerCase();
        if (hay.includes(query)) score += 25;
        for (const t of tokens) {
          if (m.title.toLowerCase().includes(t)) score += 10;
          else if (hay.includes(t)) score += 5;
        }
        if (score > 0) scored.push({ m, score });
      }
      scored.sort((a, b) => b.score - a.score || a.m.code.localeCompare(b.m.code));
      list = scored.map((x) => x.m);
    }
    const cap = Math.max(20, Math.min(Number(limit) || 100, 300));
    const groupCounts = {};
    for (const m of this.menusList) {
      groupCounts[m.group] = (groupCounts[m.group] || 0) + 1;
    }
    return {
      ok: true,
      total: this.menusList.length,
      matched: list.length,
      groups: Object.entries(MENU_GROUPS_TH).map(([code, label]) => ({
        code,
        label,
        count: groupCounts[code] || 0,
      })),
      menus: list.slice(0, cap),
    };
  }
}
