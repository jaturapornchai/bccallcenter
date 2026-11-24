#!/usr/bin/env node
/**
 * Captures 8 crisp native-4K (3840x2160) live UI screenshots from http://127.0.0.1:3000/
 * in Light Mode default using our synthetic SME demo dataset.
 * Masks admin login email & SIP host IP inside the DOM before every frame for public YouTube safety.
 */
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const puppeteer = require("C:/Users/jatur/AppData/Roaming/npm/node_modules/puppeteer");

function loadDotEnvLocal() {
  const fp = path.resolve(process.cwd(), ".env.local");
  if (!fs.existsSync(fp)) return;
  for (const line of fs.readFileSync(fp, "utf8").split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const eq = trimmed.indexOf("=");
    if (eq <= 0) continue;
    const k = trimmed.slice(0, eq).trim();
    const v = trimmed.slice(eq + 1).trim().replace(/^["']|["']$/g, "");
    if (!process.env[k]) process.env[k] = v;
  }
}

loadDotEnvLocal();
const { createSessionToken } = await import("../lib/auth.mjs");

const OUT_DIR = path.resolve(process.cwd(), "videos", "assets");
fs.mkdirSync(OUT_DIR, { recursive: true });

const BASE_URL = "http://127.0.0.1:3000";
const CHROME_PATH = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function sanitizeHeaderDom(page) {
  await page.evaluate(() => {
    for (const el of document.querySelectorAll("header span, header button")) {
      const txt = (el.textContent || "").trim();
      if (/@/.test(txt) && txt.includes(".")) {
        el.textContent = "admin-demo@example.com";
      } else if (/^ออนไลน์ \(\d+\.\d+\.\d+\.\d+\)$/.test(txt)) {
        el.textContent = "ออนไลน์ (SIP Trunk)";
      }
    }
  });
}

async function clickButtonByText(page, needle) {
  const clicked = await page.evaluate((text) => {
    const buttons = Array.from(document.querySelectorAll("button"));
    const target = buttons.find((b) => (b.innerText || "").includes(text));
    if (!target) return false;
    target.click();
    return true;
  }, needle);
  if (!clicked) {
    console.warn(`[WARN] Button not found containing: ${needle}`);
  }
  await sleep(650);
  return clicked;
}

async function scrollToHeadingOrSelector(page, matcherText) {
  await page.evaluate((needle) => {
    const candidates = Array.from(document.querySelectorAll("section, h2, h3, div"));
    const match = candidates.find((el) => {
      const t = (el.innerText || "").trim();
      return t.startsWith(needle) && el.getBoundingClientRect().height < 1400;
    });
    if (match) {
      const top = Math.max(0, match.getBoundingClientRect().top + window.scrollY - 24);
      window.scrollTo({ top, behavior: "instant" });
    } else {
      window.scrollTo({ top: 0, behavior: "instant" });
    }
  }, matcherText);
  await sleep(500);
}

async function waitForWsLoaded(page) {
  await page.waitForFunction(
    () => (document.body?.innerText || "").includes("ระบบ BC Call Center") && !(document.body?.innerText || "").includes("กำลังเชื่อมสด"),
    { timeout: 15000 }
  );
  await sleep(1200);
}

async function run() {
  const adminEmail = String(process.env.ADMIN_EMAIL || "").trim();
  if (!adminEmail) throw new Error("ADMIN_EMAIL is required in .env.local");
  const token = createSessionToken({ email: adminEmail, name: "BC Demo Admin" });

  const browser = await puppeteer.launch({
    executablePath: fs.existsSync(CHROME_PATH) ? CHROME_PATH : undefined,
    headless: true,
    args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--font-render-hinting=none"],
  });

  try {
    // 1. Authenticated Admin Context in Crisp Light Mode (3840x2160)
    const adminCtx = await browser.createBrowserContext();
    await adminCtx.setCookie({
      name: "bc_cc_session",
      value: token,
      domain: "127.0.0.1",
      path: "/",
      httpOnly: true,
      sameSite: "Lax",
    });

    const page = await adminCtx.newPage();
    await page.setViewport({ width: 1920, height: 1080, deviceScaleFactor: 2 });
    await page.evaluateOnNewDocument(() => {
      try {
        localStorage.setItem("yalecom-theme", "light");
      } catch {}
    });

    await page.goto(BASE_URL, { waitUntil: "networkidle2", timeout: 25000 });
    await waitForWsLoaded(page);
    await sanitizeHeaderDom(page);

    // 03: Capture Full Callback Alert Banner + Top Header first (Light Mode)
    await page.evaluate(() => window.scrollTo(0, 0));
    await sleep(500);
    await page.screenshot({ path: path.join(OUT_DIR, "03-callback-focus.png"), fullPage: false });
    console.log("Saved 03-callback-focus.png");

    // 01: Command Center KPI + 5-Line Switchboard + Right Rail (Light Mode)
    await scrollToHeadingOrSelector(page, "สแตนด์บาย");
    await page.screenshot({ path: path.join(OUT_DIR, "01-command-light.png"), fullPage: false });
    console.log("Saved 01-command-light.png");

    // 02: Command Center Dark Mode (toggle preview)
    await clickButtonByText(page, "โหมดมืด");
    await sanitizeHeaderDom(page);
    await scrollToHeadingOrSelector(page, "สแตนด์บาย");
    await page.screenshot({ path: path.join(OUT_DIR, "02-command-dark.png"), fullPage: false });
    console.log("Saved 02-command-dark.png");

    // Switch back to Light Mode for all detailed walkthrough screens
    await clickButtonByText(page, "โหมดสว่าง");
    await sanitizeHeaderDom(page);
    await sleep(500);

    // 04: Post-Call QA Drawer open (Light Mode)
    await clickButtonByText(page, "บันทึก QA");
    await sleep(500);
    await page.evaluate(() => {
      const aside = document.querySelector("aside");
      const items = aside ? Array.from(aside.querySelectorAll("button")) : [];
      const qaCard = items.find((b) => (b.innerText || "").includes("CSAT") || (b.innerText || "").includes("/10"));
      if (qaCard) qaCard.click();
    });
    await sleep(1000);
    await page.screenshot({ path: path.join(OUT_DIR, "04-qa-drawer.png"), fullPage: false });
    console.log("Saved 04-qa-drawer.png");

    await page.keyboard.press("Escape");
    await page.evaluate(() => {
      const btn = Array.from(document.querySelectorAll("button")).find(
        (b) => (b.getAttribute("aria-label") || "").includes("ปิด") || (b.innerText || "").trim() === "ปิด"
      );
      if (btn) btn.click();
    });
    await sleep(400);

    // 05: CRM 360 Directory (Light Mode — scrolled past Callback Alert straight onto CRM KPI + Directory)
    await clickButtonByText(page, "ระบบ CRM");
    await sanitizeHeaderDom(page);
    await sleep(700);
    await page.evaluate(() => {
      // Pick VIP/multilingual customer if present in list
      const buttons = Array.from(document.querySelectorAll("button"));
      const vipBtn = buttons.find((b) => (b.innerText || "").includes("ไทยเจริญยางยนต์") || (b.innerText || "").includes("EN"));
      if (vipBtn) vipBtn.click();
    });
    await sleep(400);
    await scrollToHeadingOrSelector(page, "ลูกค้าในระบบ CRM");
    await page.screenshot({ path: path.join(OUT_DIR, "05-crm-360.png"), fullPage: false });
    console.log("Saved 05-crm-360.png");

    // 06: CRM Tickets Board (Light Mode — consolidated cards + staff callback advice)
    await clickButtonByText(page, "บอร์ดติดตามเคส");
    await sleep(700);
    await scrollToHeadingOrSelector(page, "ทำเนียบลูกค้า 360°");
    await page.screenshot({ path: path.join(OUT_DIR, "06-crm-tickets.png"), fullPage: false });
    console.log("Saved 06-crm-tickets.png");

    // 07: Analytics & ROI Dashboard (Light Mode — scrolled right onto Analytics heading & charts)
    await clickButtonByText(page, "สถิติ & ROI");
    await sanitizeHeaderDom(page);
    await sleep(1600);
    await scrollToHeadingOrSelector(page, "แดชบอร์ดสถิติ Call Center");
    await page.screenshot({ path: path.join(OUT_DIR, "07-analytics-roi.png"), fullPage: false });
    console.log("Saved 07-analytics-roi.png");

    await adminCtx.close();

    // 2. Unauthenticated Incognito Viewer Context (Light Mode — Privacy Redaction Proof)
    const viewerCtx = await browser.createBrowserContext();
    const vPage = await viewerCtx.newPage();
    await vPage.setViewport({ width: 1920, height: 1080, deviceScaleFactor: 2 });
    await vPage.goto(BASE_URL, { waitUntil: "networkidle2", timeout: 25000 });
    await waitForWsLoaded(vPage);
    await vPage.evaluate(() => window.scrollTo(0, 0));
    await sleep(700);
    await vPage.screenshot({ path: path.join(OUT_DIR, "08-viewer-privacy.png"), fullPage: false });
    console.log("Saved 08-viewer-privacy.png");

    await viewerCtx.close();
  } finally {
    await browser.close();
  }
}

run().catch((err) => {
  console.error(err);
  process.exit(1);
});
