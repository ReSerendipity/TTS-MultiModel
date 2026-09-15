/**
 * TTS MultiModel — Frontend Smoke Test (jsdom)
 *
 * Usage:
 *   python scripts/render_pages.py
 *   node tests/frontend/smoke.js
 *
 * Requires: npm install jsdom (devDependency in tests/package.json)
 *
 * 两部分：
 *   ① 对 _rendered/ 里渲染过的页面做结构断言（元素齐全、无未渲染的 Jinja 残留）；
 *   ② 对 **全部** 模板的内联 <script> 做语法解析（剥离 Jinja 后 new Function，
 *      只解析不执行）—— 内联 JS 语法错误在服务端渲染阶段看不出来，只在浏览器
 *      里表现为「页面在、按钮全没反应」，见文末该段的 WHY。
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

// ===========================================================================
// 模板内联 <script> 语法检查（全量模板，不止 _rendered 里渲染过的页面）
// ===========================================================================
// WHY：内联 JS 的语法错误在服务端渲染阶段**完全看不出来**（Jinja 照常吐出
// HTML、HTTP 200、页面结构断言全过），只有浏览器里那整块脚本不执行 ——
// 表现为「页面在、按钮全没反应」这种最难查的静默失效。
// 本段把每个模板的内联脚本剥离 Jinja 表达式后交给 new Function() **只解析不执行**。
// 占位符不能带引号：Jinja 表达式绝大多数出现在 JS 字符串内部，带引号会插进
// 已有字符串里造出假语法错误（本次实测踩过）。
const TPL_ROOT = path.join(__dirname, "..", "..", "app", "integrated_app", "templates");
const JINJA_RE = /\{\{[\s\S]*?\}\}|\{%[\s\S]*?%\}|\{#[\s\S]*?#\}/g;
const INLINE_SCRIPT_RE = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi;

function walkHtml(dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walkHtml(full));
    else if (entry.name.endsWith(".html")) out.push(full);
  }
  return out;
}

console.log("\n--- 模板内联 JS 语法 ---");
let tplBlocks = 0;
let tplBroken = 0;
for (const file of walkHtml(TPL_ROOT)) {
  const html = fs.readFileSync(file, "utf-8");
  const rel = path.relative(path.join(__dirname, "..", ".."), file);
  let idx = 0;
  for (const match of html.matchAll(INLINE_SCRIPT_RE)) {
    idx++;
    const code = match[1].replace(JINJA_RE, "__jinja__");
    if (!code.trim()) continue;
    tplBlocks++;
    try {
      // 只做语法解析，不执行任何模板代码
      new Function(code); // eslint-disable-line no-new-func
    } catch (err) {
      tplBroken++;
      assert(false, `${rel} 内联脚本 #${idx} 语法错误: ${err.message}`);
    }
  }
}
// 非永真自证：扫描规则若失效（正则写坏/目录改路径）这里立刻变红
assert(
  tplBlocks >= 20,
  `内联脚本扫描覆盖 ${tplBlocks} 个块（要求 >=20，低于此值说明扫描规则已失效）`
);
if (tplBroken === 0) {
  console.log(`  ok - ${tplBlocks} 个模板内联脚本块语法全部通过`);
}

console.log(`\n=== RESULT: pass=${pass} fail=${fail} ===`);
process.exit(fail ? 1 : 0);
