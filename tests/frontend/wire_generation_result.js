/**
 * 生成结果「统一接线」行为测试（jsdom）
 *
 * Usage:
 *   node tests/frontend/wire_generation_result.js
 *
 * Requires: npm install (devDependency jsdom, 见 tests/package.json)
 *
 * WHY 需要这个测试（GOTCHAS #91）：
 *   htmx 路径与 SSE 流式路径共用 window.wireGenerationResult() 完成生成后接线：
 *   ① 显示后处理折叠区 <prefix>-pp-section；② 回填保存表单隐藏字段
 *   <prefix>-result-audio。2026-09-14 发现两个流式分支各自手写且各漏一项
 *   （voice_clone 漏显示后处理区、voice_design 漏回填 vd-result-audio），
 *   导致流式生成完成后后处理区不出现、点「保存为音色」被后端判为"缺少音频"。
 *
 * WHY 用行为断言而非源码字符串扫描：
 *   test_fe_be_consistency.py 的守卫 6 只能证明「流式模板调用了本函数」，
 *   无法证明函数本身正确。本测试直接 eval 真实的 app_init.js（非副本）并断言
 *   DOM 副作用，两个守卫互补：守卫 6 管调用点，本测试管函数语义。
 */
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const APP_INIT = path.join(
  __dirname,
  "..",
  "..",
  "app",
  "integrated_app",
  "static",
  "js",
  "app_init.js"
);

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

/** 构造最小页面并加载真实 app_init.js。 */
function makeDom(bodyHtml) {
  const dom = new JSDOM(
    `<!doctype html><html><body>${bodyHtml || ""}</body></html>`,
    { runScripts: "outside-only" }
  );
  dom.window.eval(fs.readFileSync(APP_INIT, "utf-8"));
  return dom;
}

/** 语音设计页的最小 DOM 骨架（prefix=vd）。 */
const VD_PAGE = `
  <div id="vd-result"></div>
  <details id="vd-pp-section" style="display:none"></details>
  <input type="hidden" name="result_audio" id="vd-result-audio" value="">
`;

console.log("\n--- 入口存在 ---");
{
  const dom = makeDom(VD_PAGE);
  assert(
    typeof dom.window.wireGenerationResult === "function",
    "app_init.js 暴露 window.wireGenerationResult"
  );
}

console.log("\n--- 正常路径：有生成结果 ---");
{
  const dom = makeDom(VD_PAGE);
  const doc = dom.window.document;
  doc.getElementById("vd-result").innerHTML =
    '<div data-audio-filename="a.wav"></div>';
  dom.window.wireGenerationResult(doc.getElementById("vd-result"));
  assert(
    doc.getElementById("vd-pp-section").style.display === "",
    "后处理折叠区被显示"
  );
  assert(
    doc.getElementById("vd-result-audio").value === "a.wav",
    "保存表单隐藏字段被回填为 a.wav"
  );
}

console.log("\n--- 非永真：无生成结果时不接线 ---");
{
  const dom = makeDom(VD_PAGE);
  const doc = dom.window.document;
  dom.window.wireGenerationResult(doc.getElementById("vd-result")); // 空容器
  assert(
    doc.getElementById("vd-pp-section").style.display === "none",
    "无 data-audio-filename 时后处理区保持隐藏"
  );
  assert(
    doc.getElementById("vd-result-audio").value === "",
    "无生成结果时不回填（避免把上一次的文件名当成新结果）"
  );
}

console.log("\n--- 只认 <prefix>-result 容器 ---");
{
  const dom = makeDom(VD_PAGE);
  const doc = dom.window.document;
  doc.getElementById("vd-result").innerHTML =
    '<div data-audio-filename="b.wav"></div>';
  // id 以 -audio 结尾（不含 -result$），必须被忽略
  dom.window.wireGenerationResult(doc.getElementById("vd-result-audio"));
  assert(
    doc.getElementById("vd-result-audio").value === "",
    "非 -result 结尾的容器被忽略"
  );
  let threw = null;
  try {
    dom.window.wireGenerationResult(null);
  } catch (e) {
    threw = e;
  }
  assert(threw === null, "null 入参不抛异常");
}

console.log("\n--- 缺配套元素时安全返回 ---");
{
  const dom = makeDom('<div id="zz-result"></div>');
  const doc = dom.window.document;
  doc.getElementById("zz-result").innerHTML =
    '<div data-audio-filename="c.wav"></div>';
  let threw = null;
  try {
    dom.window.wireGenerationResult(doc.getElementById("zz-result"));
  } catch (e) {
    threw = e;
  }
  assert(
    threw === null,
    "无 zz-pp-section / zz-result-audio 时安全返回（不抛异常）"
  );
}

console.log("\n--- htmx:afterSwap 路径（非流式生成）---");
{
  const dom = makeDom(VD_PAGE);
  const doc = dom.window.document;
  doc.getElementById("vd-result").innerHTML =
    '<div data-audio-filename="d.wav"></div>';
  doc.body.dispatchEvent(
    new dom.window.CustomEvent("htmx:afterSwap", {
      detail: { target: doc.getElementById("vd-result") },
    })
  );
  assert(
    doc.getElementById("vd-result-audio").value === "d.wav",
    "htmx 路径回填 vd-result-audio"
  );
  assert(
    doc.getElementById("vd-pp-section").style.display === "",
    "htmx 路径显示后处理区"
  );
}

console.log(`\n=== RESULT: pass=${pass} fail=${fail} ===`);
process.exit(fail ? 1 : 0);
