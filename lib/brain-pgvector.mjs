/**
 * Fail-soft PostgreSQL 18 pgvector + Thai FTS Hybrid RRF client for Node.js (Web "สมอง BC" Explorer).
 * Shares the exact same `bccallcenter-pg` (`brain_chunks`) table indexed by `jarvis/brain_pgvector.py`.
 * Enforces strict `audience = 'user'` isolation for non-admin viewers using parameterized SQL only.
 */
import pg from "pg";

const { Pool } = pg;

const EMBED_CACHE_MAX = 256;

const THAI_QUERY_NORM_RULES = [
  [/โปรโมชั[น่]น?|promotions?/gi, " โปรโมชัน "],
  [/ซื้อ\s*\d+\s*แถม\s*\d+|ซื้อ(?:หนึ่ง|สอง|สาม|ครบ)แถม(?:หนึ่ง|สอง|ฟรี)?|ซื้อแถม|แถมฟรี/g, " โปรโมชัน ของแถม ซื้อครบแถม สูตรโปรโมชัน "],
  [/พักบิน|พรรคบิน|พับบิล/g, " พักบิล "],
  [/เข้า(?:ที|ที่)หลัง(?:ออก|ของ)ก่อน|ไลโฟ/gi, " lifo เข้าทีหลังออกก่อน ประเภทต้นทุน "],
  [/ไฟโฟ|ฟีโฟ/gi, " fifo เข้าก่อนออกก่อน ประเภทต้นทุน "],
  [/สต(?:๊อก|็อค|อค)/g, " สต็อก "],
  [/ภ\.?พ\.?\s*30/g, " ภพ30 ภาษีมูลค่าเพิ่ม "],
  [/ภ\.?ง\.?ด\.?\s*(3|53)/g, " ภงด$1 หักณที่จ่าย "],
];

export function normalizeThaiBrainQuery(text) {
  let s = String(text || "").trim().toLowerCase();
  for (const [re, repl] of THAI_QUERY_NORM_RULES) {
    s = s.replace(re, repl);
  }
  return s.replace(/\s+/g, " ").trim();
}

export class BrainPgVectorNodeClient {
  constructor() {
    const enabledEnv = String(process.env.BRAIN_PG_ENABLED ?? "true").trim().toLowerCase();
    this.host = String(process.env.BRAIN_PG_HOST || "127.0.0.1").trim();
    this.port = Number(process.env.BRAIN_PG_PORT || 5446);
    this.database = String(process.env.BRAIN_PG_DB || "bccallcenter_brain").trim();
    this.user = String(process.env.BRAIN_PG_USER || "bcbrain").trim();
    this.password = String(process.env.BRAIN_PG_PASSWORD || "");
    this.embedUrl = String(process.env.BRAIN_EMBED_URL || "http://127.0.0.1:11434/api/embed").trim();
    this.embedModel = String(process.env.BRAIN_EMBED_MODEL || "bge-m3:latest").trim();
    this.enabled =
      !["0", "false", "no", "off"].includes(enabledEnv) && Boolean(this.password);

    this.pool = null;
    this.embedCache = new Map();
    this.failUntil = 0;
    this.statsCache = null;
    this.statsCachedAt = 0;
  }

  _getPool() {
    if (!this.enabled) return null;
    if (!this.pool) {
      this.pool = new Pool({
        host: this.host,
        port: this.port,
        database: this.database,
        user: this.user,
        password: this.password,
        max: 4,
        idleTimeoutMillis: 15000,
        connectionTimeoutMillis: 1200,
        allowExitOnIdle: true,
      });
      this.pool.on("error", () => {
        // Fail-soft: never crash web server if local pg restarts
      });
    }
    return this.pool;
  }

  async close() {
    if (this.pool) {
      try {
        await this.pool.end();
      } catch {}
      this.pool = null;
    }
  }

  async embedQuery(query, timeoutMs = 1500) {
    if (!this.enabled) return null;
    const key = String(query || "").trim().toLowerCase().replace(/\s+/g, " ");
    if (!key) return null;
    if (this.embedCache.has(key)) {
      const vec = this.embedCache.get(key);
      this.embedCache.delete(key);
      this.embedCache.set(key, vec);
      return vec;
    }
    if (Date.now() < this.failUntil) return null;

    const ac = new AbortController();
    const timer = setTimeout(() => ac.abort(), timeoutMs);
    try {
      const res = await fetch(this.embedUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: this.embedModel, input: [key], keep_alive: -1 }),
        signal: ac.signal,
      });
      if (!res.ok) {
        this.failUntil = Date.now() + 15000;
        return null;
      }
      const data = await res.json();
      const vec = Array.isArray(data?.embeddings?.[0]) ? data.embeddings[0] : null;
      if (!vec || vec.length !== 1024) return null;
      this.embedCache.set(key, vec);
      if (this.embedCache.size > EMBED_CACHE_MAX) {
        const oldest = this.embedCache.keys().next().value;
        this.embedCache.delete(oldest);
      }
      return vec;
    } catch {
      this.failUntil = Date.now() + 15000;
      return null;
    } finally {
      clearTimeout(timer);
    }
  }

  async getStats() {
    const base = {
      enabled: this.enabled,
      ready: false,
      chunks: 0,
      notes: 0,
      model: this.embedModel,
      tokenizer: "pythainlp+bc-dict",
    };
    if (!this.enabled) return base;
    if (this.statsCache && Date.now() - this.statsCachedAt < 30000) {
      return this.statsCache;
    }
    const pool = this._getPool();
    if (!pool) return base;
    try {
      const { rows } = await pool.query(
        "SELECT COUNT(*)::int AS chunks, COUNT(DISTINCT slug)::int AS notes FROM brain_chunks"
      );
      const chunks = Number(rows?.[0]?.chunks || 0);
      const notes = Number(rows?.[0]?.notes || 0);
      this.statsCache = { ...base, ready: chunks > 0, chunks, notes };
      this.statsCachedAt = Date.now();
      return this.statsCache;
    } catch {
      return base;
    }
  }

  async hybridSearch({ query, audience = "user", folder = "", limit = 40 } = {}) {
    if (!this.enabled || Date.now() < this.failUntil) return null;
    const pool = this._getPool();
    if (!pool) return null;
    const rawQ = String(query || "").trim();
    if (!rawQ) return null;
    const q = normalizeThaiBrainQuery(rawQ) || rawQ;

    const vec = await this.embedQuery(q, 1500);
    if (!vec) return null;
    const vecStr = `[${vec.map((x) => Number(x).toFixed(6)).join(",")}]`;

    const aud = ["user", "dev", "all"].includes(audience) ? audience : "user";
    const fFilter = String(folder || "").trim().toLowerCase();
    const lim = Math.max(5, Math.min(Number(limit) || 40, 80));

    // Use Intl.Segmenter('th') built into Node 20/22 for zero-dependency Thai tokenization on web queries
    const segTokens = [];
    const seen = new Set();
    try {
      const seg = new Intl.Segmenter("th", { granularity: "word" });
      for (const part of seg.segment(q.toLowerCase())) {
        if (!part.isWordLike) continue;
        const clean = part.segment.replace(/[^a-z0-9\u0E00-\u0E7F]/gi, "");
        if (clean.length >= 2 && !seen.has(clean)) {
          seen.add(clean);
          segTokens.push(clean);
        }
      }
    } catch {
      for (const raw of q.toLowerCase().split(/\s+/)) {
        const clean = raw.replace(/[^a-z0-9\u0E00-\u0E7F]/gi, "");
        if (clean.length >= 2 && !seen.has(clean)) {
          seen.add(clean);
          segTokens.push(clean);
        }
      }
    }
    const tsqExpr = segTokens.slice(0, 12).join(" | ");

    const conds = [];
    const params = [vecStr];
    let pIdx = 2;
    if (aud !== "all") {
      conds.push(`audience = $${pIdx++}`);
      params.push(aud);
    }
    if (fFilter) {
      conds.push(`LOWER(folder) = $${pIdx++}`);
      params.push(fFilter);
    }
    const whereBase = conds.length ? `WHERE ${conds.join(" AND ")}` : "";

    try {
      let sql = "";
      if (tsqExpr) {
        const tsqParam = pIdx++;
        params.push(tsqExpr);
        const limParam = pIdx++;
        params.push(lim);
        const whereFts = conds.length
          ? `WHERE ${conds.join(" AND ")} AND tsv @@ to_tsquery('simple', $${tsqParam})`
          : `WHERE tsv @@ to_tsquery('simple', $${tsqParam})`;

        sql = `
          WITH q AS (
            SELECT $1::vector AS qvec, to_tsquery('simple', $${tsqParam}) AS tsq
          ),
          vec_hits AS (
            SELECT
              id, slug, heading, content,
              (1.0 - (embedding <=> (SELECT qvec FROM q)))::float8 AS cosine,
              ROW_NUMBER() OVER (ORDER BY embedding <=> (SELECT qvec FROM q))::int AS v_rank
            FROM brain_chunks
            ${whereBase}
            ORDER BY embedding <=> (SELECT qvec FROM q)
            LIMIT 40
          ),
          fts_hits AS (
            SELECT
              id, slug, heading, content,
              ts_rank_cd(tsv, (SELECT tsq FROM q))::float8 AS fts_score,
              ROW_NUMBER() OVER (ORDER BY ts_rank_cd(tsv, (SELECT tsq FROM q)) DESC, id)::int AS f_rank
            FROM brain_chunks
            ${whereFts}
            ORDER BY fts_score DESC, id
            LIMIT 40
          ),
          fused AS (
            SELECT
              COALESCE(v.slug, f.slug) AS slug,
              COALESCE(v.heading, f.heading) AS heading,
              COALESCE(v.content, f.content) AS content,
              COALESCE(v.cosine, 0.0)::float8 AS cosine,
              COALESCE(f.fts_score, 0.0)::float8 AS fts_score,
              (
                COALESCE(0.62 / (40.0 + v.v_rank), 0.0)
                + COALESCE(0.38 / (40.0 + f.f_rank), 0.0)
                + COALESCE(v.cosine * 0.018, 0.0)
              )::float8 AS rrf_score
            FROM vec_hits v
            FULL OUTER JOIN fts_hits f ON v.id = f.id
          ),
          per_slug AS (
            SELECT DISTINCT ON (slug)
              slug, heading, content, cosine, fts_score, rrf_score
            FROM fused
            ORDER BY slug, rrf_score DESC, cosine DESC
          )
          SELECT slug, heading, content, cosine, fts_score, rrf_score
          FROM per_slug
          WHERE cosine >= 0.42 OR fts_score > 0
          ORDER BY rrf_score DESC, cosine DESC
          LIMIT $${limParam}
        `;
      } else {
        const limParam = pIdx++;
        params.push(lim);
        sql = `
          WITH vec_hits AS (
            SELECT
              slug, heading, content,
              (1.0 - (embedding <=> $1::vector))::float8 AS cosine
            FROM brain_chunks
            ${whereBase}
            ORDER BY embedding <=> $1::vector
            LIMIT 40
          ),
          per_slug AS (
            SELECT DISTINCT ON (slug)
              slug, heading, content, cosine, 0.0::float8 AS fts_score, cosine AS rrf_score
            FROM vec_hits
            ORDER BY slug, cosine DESC
          )
          SELECT slug, heading, content, cosine, fts_score, rrf_score
          FROM per_slug
          WHERE cosine >= 0.45
          ORDER BY cosine DESC
          LIMIT $${limParam}
        `;
      }
      const { rows } = await pool.query(sql, params);
      return rows.map((r) => ({
        slug: String(r.slug),
        heading: String(r.heading || ""),
        content: String(r.content || ""),
        cosine: Number(r.cosine || 0),
        ftsScore: Number(r.fts_score || 0),
        rrfScore: Number(r.rrf_score || 0),
      }));
    } catch {
      this.failUntil = Date.now() + 15000;
      return null;
    }
  }
}
