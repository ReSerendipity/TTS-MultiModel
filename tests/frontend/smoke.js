/**
 * TTS MultiModel — Frontend Smoke Test (jsdom)
 *
 * Usage:
 *   python scripts/render_pages.py
 *   node tests/frontend/smoke.js
 *
 * Requires: npm install jsdom (devDependency in tests/package.json)
 */
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const RENDERED = path.join(__dirname, "_rendered");
const PAGES = ["download_guide.html"];

let pass = 0;
let fail = 0;

function assert(cond, msg) {
  if (cond) {
    pass++;
    console.log("  ok - " + msg);
  } else {
    fail++;
    console.log("  FAIL - " + msg);
  }
}

for (const pageFile of PAGES) {
  const filePath = path.join(RENDERED, pageFile);
  if (!fs.existsSync(filePath)) {
    console.log(`SKIP ${pageFile}: file not found (run: python scripts/render_pages.py)`);
    continue;
  }

  console.log(`\n--- ${pageFile} ---`);
  const html = fs.readFileSync(filePath, "utf-8");
  const dom = new JSDOM(html);
  const doc = dom.window.document;

  // 1. HTML parsed
  assert(doc.documentElement !== null, "document element exists");

  // 2. Head exists
  assert(doc.querySelector("head") !== null, "head element exists");

  // 3. Body exists
  assert(doc.querySelector("body") !== null, "body element exists");

  // 4. Title or h1 present
  const hasTitle = doc.querySelector("title") || doc.querySelector("h1");
  assert(!!hasTitle, "title or h1 present");

  // 5. No raw Jinja2 syntax leaked
  const bodyText = doc.body ? doc.body.textContent : "";
  assert(!bodyText.includes("{{"), "no unrendered Jinja2 {{ }} in body");
  assert(!bodyText.includes("{%"), "no unrendered Jinja2 {% %} in body");

  // 6. Charset meta or UTF-8 hint
  const charsetMeta = doc.querySelector("meta[charset]");
  assert(!!charsetMeta, "charset meta tag present");
}

console.log(`\n=== RESULT: pass=${pass} fail=${fail} ===`);
process.exit(fail ? 1 : 0);
