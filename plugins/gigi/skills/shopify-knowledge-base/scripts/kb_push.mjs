#!/usr/bin/env node
/**
 * kb_push.mjs — bulk-add FAQs to the Shopify "Knowledge Base" app (Store FAQs that
 * feed the AI shopping assistant / Storefront MCP).
 *
 * WHY a browser script: the Knowledge Base app has NO public Admin API to write
 * FAQs (verified: no faqCreate mutation, no FAQ metaobject). The only way in is
 * the embedded app UI ("Add FAQ"). This script drives that UI on a Chrome you are
 * already logged into, so it can write dozens of FAQs unattended.
 *
 * HARD-WON GOTCHAS baked in (from a verified manual run on esteban.ro):
 *   1. The app runs in a CROSS-ORIGIN iframe (qa-pairs-app.shopify.prod.shopifyapps.com).
 *   2. The Question/Answer fields are React-controlled: setting .value does NOT
 *      enable Save. You MUST type with REAL key events (puppeteer .type()).
 *   3. There are TWO "Save" buttons (App Bridge contextual bar OUTSIDE the iframe +
 *      the in-form one). We click the in-form one, inside the iframe.
 *   4. The backend occasionally throws "Application Error! TypeError: Failed to
 *      fetch" mid-save → the FAQ is NOT saved. We detect it and retry.
 *   5. Success signal = the toast / heading "FAQ created".
 *   6. macOS select-all is Cmd+A, not Ctrl+A — but we always start from an EMPTY
 *      /pairs/new form, so no clearing is needed (never reuse a dirty form).
 *
 * PREREQUISITES
 *   - Node + puppeteer-core (already on this machine under /tmp/shot; or `npm i puppeteer-core`).
 *   - A Chrome started with remote debugging, logged into the Shopify admin:
 *       "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
 *          --remote-debugging-port=9222 --user-data-dir="$HOME/.kb-chrome"
 *     then log into admin.shopify.com (Google SSO) ONCE in that window.
 *   - The "Knowledge Base" app installed on the store (apps.shopify.com/shopify-knowledge-base).
 *
 * USAGE
 *   node kb_push.mjs --store <admin-handle|myshopify-domain> --file faqs.json [--port 9222] [--skip-existing] [--dry-run]
 *   # --store: e.g. 6f9e22-9d   OR   6f9e22-9d.myshopify.com  (the *admin* handle, not esteban.ro)
 *
 * faqs.json: { "faqs": [ { "q": "Question?", "a": "Answer." }, ... ] }
 */

import fs from "node:fs";
import { createRequire } from "node:module";

// puppeteer-core may live in a sibling node_modules or in /tmp/shot — resolve flexibly.
const require = createRequire(import.meta.url);
let puppeteer;
for (const p of ["puppeteer-core", "/tmp/shot/node_modules/puppeteer-core"]) {
  try { puppeteer = require(p); break; } catch { /* try next */ }
}
if (!puppeteer) {
  console.error("puppeteer-core not found. Run: npm i puppeteer-core  (or use /tmp/shot).");
  process.exit(1);
}

// ---- args ----
const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, cur, i, arr) => {
    if (cur.startsWith("--")) {
      const key = cur.slice(2);
      const val = arr[i + 1] && !arr[i + 1].startsWith("--") ? arr[i + 1] : "true";
      acc.push([key, val]);
    }
    return acc;
  }, [])
);
const STORE = (args.store || "").replace(/^https?:\/\//, "").replace(/\.myshopify\.com.*$/, "").replace(/\/.*$/, "");
const FILE = args.file;
const PORT = args.port || "9222";
const DRY = args["dry-run"] === "true";
const SKIP_EXISTING = args["skip-existing"] === "true";

if (!STORE || !FILE) {
  console.error("Required: --store <admin-handle> --file <faqs.json>");
  process.exit(1);
}

const faqs = JSON.parse(fs.readFileSync(FILE, "utf8")).faqs;
if (!Array.isArray(faqs) || !faqs.length) { console.error("No faqs[] in file."); process.exit(1); }

const APP = `https://admin.shopify.com/store/${STORE}/apps/shopify-knowledge-base/app`;
const NEW = `${APP}/pairs/new`;
const IFRAME_HOST = "qa-pairs-app.shopify";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Find the embedded app iframe (cross-origin). Retries while the admin shell boots.
async function getAppFrame(page, timeout = 30000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeout) {
    const f = page.frames().find((fr) => fr.url().includes(IFRAME_HOST));
    if (f) { try { await f.waitForFunction(() => document.readyState !== "loading", { timeout: 3000 }); } catch {} return f; }
    await sleep(400);
  }
  throw new Error("App iframe (qa-pairs-app) not found — is the Knowledge Base app installed & are you logged in?");
}

// Resolve the Polaris field selectors (#id) by matching the <label> text.
async function fieldSelectors(frame) {
  return frame.evaluate(() => {
    const byLabel = (txt) => {
      const lab = [...document.querySelectorAll("label")].find((l) => l.textContent.trim() === txt);
      const id = lab && lab.getAttribute("for");
      return id ? "#" + CSS.escape(id) : null;
    };
    return { q: byLabel("Question"), a: byLabel("Answer") };
  });
}

// Click the in-form Save button (the one INSIDE the iframe, text === "Save").
async function clickSave(frame) {
  return frame.evaluate(() => {
    const btn = [...document.querySelectorAll("button")].find((b) => b.textContent.trim() === "Save" && !b.disabled);
    if (!btn) return false;
    btn.click();
    return true;
  });
}

async function existingQuestions(page) {
  // Page through the Custom tab collecting question texts (best-effort dedup).
  const seen = new Set();
  await page.goto(`${APP}?faqs_tab=custom`, { waitUntil: "networkidle2" });
  let frame = await getAppFrame(page);
  for (let guard = 0; guard < 50; guard++) {
    await sleep(800);
    const { rows, hasNext } = await frame.evaluate(() => {
      // The question text sits in the first column of each FAQ row.
      const txt = [...document.querySelectorAll("td, [role='cell']")].map((c) => c.textContent.trim());
      const next = [...document.querySelectorAll("button")].find((b) => b.textContent.trim() === "Next");
      return { rows: txt, hasNext: !!(next && !next.disabled) };
    });
    rows.forEach((r) => r && seen.add(r));
    if (!hasNext) break;
    await frame.evaluate(() => [...document.querySelectorAll("button")].find((b) => b.textContent.trim() === "Next")?.click());
    await sleep(1200);
    frame = await getAppFrame(page);
  }
  return seen;
}

async function addOne(page, q, a, attempt = 1) {
  await page.goto(NEW, { waitUntil: "networkidle2" });
  const frame = await getAppFrame(page);
  // wait for the empty form
  let sels;
  for (let i = 0; i < 30; i++) {
    sels = await fieldSelectors(frame);
    if (sels.q && sels.a) break;
    await sleep(400);
  }
  if (!sels.q || !sels.a) throw new Error("Question/Answer fields not found on /pairs/new");

  // REAL keystrokes (fixes the React controlled-input issue). Fields start empty.
  await frame.click(sels.q); await frame.type(sels.q, q, { delay: 4 });
  await frame.click(sels.a); await frame.type(sels.a, a, { delay: 2 });
  await sleep(150);

  if (!(await clickSave(frame))) throw new Error("Save button not enabled (typing did not register?)");

  // Wait for success OR detect the transient Application Error.
  const t0 = Date.now();
  while (Date.now() - t0 < 15000) {
    const state = await frame.evaluate(() => {
      const t = document.body.innerText;
      if (t.includes("FAQ created")) return "ok";
      if (t.includes("Application Error")) return "err";
      return "wait";
    }).catch(() => "wait");
    if (state === "ok") return true;
    if (state === "err") break;
    await sleep(400);
  }
  if (attempt < 3) { console.log(`   ↻ retry (${attempt + 1}) after transient error`); await sleep(1500); return addOne(page, q, a, attempt + 1); }
  throw new Error("save failed after 3 attempts (Application Error / timeout)");
}

(async () => {
  const browser = await puppeteer.connect({ browserURL: `http://127.0.0.1:${PORT}`, defaultViewport: null });
  const pages = await browser.pages();
  const page = pages.find((p) => p.url().includes("admin.shopify.com")) || pages[0] || (await browser.newPage());

  let skip = new Set();
  if (SKIP_EXISTING) {
    console.log("Scanning existing custom FAQs for dedup…");
    skip = await existingQuestions(page);
  }

  let ok = 0, skipped = 0, failed = 0;
  for (const [i, f] of faqs.entries()) {
    const n = `${i + 1}/${faqs.length}`;
    if (SKIP_EXISTING && skip.has(f.q.trim())) { console.log(`• ${n} SKIP (exists): ${f.q}`); skipped++; continue; }
    if (DRY) { console.log(`• ${n} DRY: ${f.q}`); continue; }
    try { await addOne(page, f.q, f.a); console.log(`✓ ${n} ${f.q}`); ok++; }
    catch (e) { console.error(`✗ ${n} ${f.q}\n   ${e.message}`); failed++; }
  }
  console.log(`\nDone. created=${ok} skipped=${skipped} failed=${failed} (total ${faqs.length})`);
  browser.disconnect();
})().catch((e) => { console.error(e); process.exit(1); });
