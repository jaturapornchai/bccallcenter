#!/usr/bin/env node
/**
 * Renders a native 4K UHD (3840x2160 @ 30fps) YouTube showcase video + 4K Thumbnail
 * by driving Chrome Puppeteer across all chapters in videos/work/timeline.json and encoding via FFmpeg.
 */
import fs from "node:fs";
import path from "node:path";
import { spawn, execSync } from "node:child_process";
import { once } from "node:events";
import { pathToFileURL } from "node:url";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const puppeteer = require("C:/Users/jatur/AppData/Roaming/npm/node_modules/puppeteer");

const ROOT = process.cwd();
const VIDEOS_DIR = path.join(ROOT, "videos");
const WORK_DIR = path.join(VIDEOS_DIR, "work");
const SCENES_DIR = path.join(WORK_DIR, "scenes");
const TIMELINE_PATH = path.join(WORK_DIR, "timeline.json");
const MASTER_AUDIO_PATH = path.join(WORK_DIR, "master_audio.wav");
const TEMPLATE_PATH = path.join(ROOT, "scripts", "youtube-4k-template.html");
const FINAL_MP4_PATH = path.join(VIDEOS_DIR, "BC-Call-Center-JARVIS-4K-YouTube.mp4");
const THUMB_PNG_PATH = path.join(VIDEOS_DIR, "BC-Call-Center-JARVIS-Thumbnail-4K.png");
const CHROME_PATH = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";

const ONLY_THUMBNAIL = process.argv.includes("--only-thumbnail");
const FORCE = process.argv.includes("--force");
const CHROME_ARGS = ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--font-render-hinting=none"];

async function launchWorkerBrowser() {
  return puppeteer.launch({
    executablePath: fs.existsSync(CHROME_PATH) ? CHROME_PATH : undefined,
    headless: true,
    protocolTimeout: 120000,
    args: CHROME_ARGS,
  });
}

function detectEncoder() {
  try {
    execSync(
      'ffmpeg -hide_banner -loglevel error -f lavfi -i color=c=white:s=640x360:d=0.2 -c:v h264_nvenc -f null -',
      { stdio: "pipe" }
    );
    return "h264_nvenc";
  } catch {
    return "libx264";
  }
}

async function waitForImages(page) {
  await page.evaluate(async () => {
    const imgs = Array.from(document.querySelectorAll("img"));
    await Promise.all(
      imgs.map((img) =>
        img.complete
          ? Promise.resolve()
          : new Promise((r) => {
              img.addEventListener("load", r, { once: true });
              img.addEventListener("error", r, { once: true });
              setTimeout(r, 3000);
            })
      )
    );
  });
}

async function renderScene(page, timeline, scene, encoder) {
  const fps = timeline.fps || 30;
  const startFrame = Math.round(scene.startSec * fps);
  const endFrame = Math.round(scene.endSec * fps);
  const frameCount = Math.max(1, endFrame - startFrame);
  const outMp4 = path.join(SCENES_DIR, `${scene.id}.mp4`);

  if (!FORCE && fs.existsSync(outMp4) && fs.statSync(outMp4).size > 100_000) {
    console.log(`  [${scene.id}] cached -> ${outMp4}`);
    return outMp4;
  }

  const encArgs =
    encoder === "h264_nvenc"
      ? ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "19", "-b:v", "35M", "-maxrate", "55M", "-pix_fmt", "yuv420p"]
      : ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"];

  const ff = spawn(
    "ffmpeg",
    [
      "-y",
      "-hide_banner",
      "-loglevel",
      "error",
      "-f",
      "image2pipe",
      "-vcodec",
      "mjpeg",
      "-framerate",
      String(fps),
      "-i",
      "pipe:0",
      ...encArgs,
      outMp4,
    ],
    { stdio: ["pipe", "inherit", "pipe"] }
  );

  ff.stdin.on("error", () => {});

  let ffStderr = "";
  ff.stderr.on("data", (d) => {
    ffStderr += d.toString();
  });

  const ffDone = new Promise((resolve, reject) => {
    ff.on("close", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`ffmpeg ${scene.id} exited ${code}: ${ffStderr}`));
    });
    ff.on("error", reject);
  });

  const t0 = Date.now();
  try {
    for (let f = 0; f < frameCount; f++) {
      const localT = f / fps;
      const globalT = scene.startSec + localT;
      await page.evaluate(
        (idx, lt, sd, gt) => window.renderAt(idx, lt, sd, gt),
        scene.index,
        localT,
        scene.durationSec,
        globalT
      );
      if (f === 0) await waitForImages(page);
      const buf = await page.screenshot({ type: "jpeg", quality: 90, optimizeForSpeed: true });
      if (!ff.stdin.write(buf)) {
        await once(ff.stdin, "drain");
      }
      if ((f + 1) % 100 === 0 || f + 1 === frameCount) {
        const elapsed = ((Date.now() - t0) / 1000).toFixed(1);
        console.log(`  [${scene.id}] ${f + 1}/${frameCount} frames (${elapsed}s)`);
      }
    }
    ff.stdin.end();
    await ffDone;
  } catch (err) {
    try {
      ff.stdin.destroy();
    } catch {}
    try {
      ff.kill("SIGKILL");
    } catch {}
    try {
      fs.rmSync(outMp4, { force: true });
    } catch {}
    throw err;
  }
  return outMp4;
}

async function main() {
  fs.mkdirSync(SCENES_DIR, { recursive: true });
  for (const entry of fs.readdirSync(SCENES_DIR)) {
    if (entry.endsWith(".mp4")) {
      const fp = path.join(SCENES_DIR, entry);
      if (fs.statSync(fp).size < 100_000) {
        fs.rmSync(fp, { force: true });
      }
    }
  }

  const timeline = JSON.parse(fs.readFileSync(TIMELINE_PATH, "utf8"));
  const templateUrl = pathToFileURL(TEMPLATE_PATH).href;

  // 1. Render 4K Thumbnail first using a short-lived dedicated browser
  const thumbBrowser = await launchWorkerBrowser();
  try {
    const tPage = await thumbBrowser.newPage();
    await tPage.setViewport({ width: 1920, height: 1080, deviceScaleFactor: 2 });
    await tPage.goto(templateUrl, { waitUntil: "load" });
    await tPage.evaluate(() => window.renderThumbnail());
    await waitForImages(tPage);
    await tPage.screenshot({ path: THUMB_PNG_PATH });
    await tPage.close();
    console.log("Rendered 4K Thumbnail ->", THUMB_PNG_PATH);
  } finally {
    await thumbBrowser.close();
  }

  if (ONLY_THUMBNAIL) return;

  const encoder = detectEncoder();
  console.log("Selected FFmpeg H.264 encoder:", encoder);

  // 2. Render scenes concurrently using dedicated single-tab Chrome workers
  const scenes = timeline.scenes || [];
  const concurrency = Math.min(3, scenes.length);
  let nextIdx = 0;
  const workers = Array.from({ length: concurrency }, async () => {
    const browser = await launchWorkerBrowser();
    try {
      const page = await browser.newPage();
      await page.setViewport({ width: 1920, height: 1080, deviceScaleFactor: 2 });
      await page.goto(templateUrl, { waitUntil: "load" });
      await page.evaluate((tl) => window.initTimeline(tl), timeline);

      while (nextIdx < scenes.length) {
        const idx = nextIdx++;
        await renderScene(page, timeline, scenes[idx], encoder);
      }
    } finally {
      await browser.close();
    }
  });
  await Promise.all(workers);

  // 3. Concatenate all 11 4K scenes + mux broadcast 48kHz stereo master_audio.wav
  const concatListPath = path.join(WORK_DIR, "concat.txt");
  const concatBody = scenes
    .map((s) => `file '${path.join(SCENES_DIR, `${s.id}.mp4`).replace(/\\/g, "/")}'`)
    .join("\n");
  fs.writeFileSync(concatListPath, concatBody, "utf8");

  console.log("Muxing final 4K YouTube MP4...");
  const muxProc = spawn(
    "ffmpeg",
    [
      "-y",
      "-hide_banner",
      "-loglevel",
      "warning",
      "-f",
      "concat",
      "-safe",
      "0",
      "-i",
      concatListPath,
      "-i",
      MASTER_AUDIO_PATH,
      "-c:v",
      "copy",
      "-c:a",
      "aac",
      "-b:a",
      "192k",
      "-ar",
      "48000",
      "-ac",
      "2",
      "-movflags",
      "+faststart",
      "-shortest",
      FINAL_MP4_PATH,
    ],
    { stdio: "inherit" }
  );
  await new Promise((resolve, reject) => {
    muxProc.on("close", (code) => (code === 0 ? resolve() : reject(new Error(`Final mux failed: ${code}`))));
    muxProc.on("error", reject);
  });

  const sizeMb = (fs.statSync(FINAL_MP4_PATH).size / (1024 * 1024)).toFixed(1);
  console.log(`FINAL 4K YOUTUBE MP4 READY: ${FINAL_MP4_PATH} (${sizeMb} MB)`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
