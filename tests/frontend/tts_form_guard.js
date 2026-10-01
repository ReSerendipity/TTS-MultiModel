/**
 * TTS MultiModel — tts_form.js 切页守卫行为测试（jsdom）
 *
 * 背景（2026-10-01）：守卫原先在 htmx:beforeSwap 里同步调 window.confirm ——
 * 原生对话框渲染在宿主层、不属于页面 DOM，桌面壳/内嵌浏览器里无法定制，
 * 也会卡住无头自动化。改为页内 ConfirmDialog + 确认后 htmx.ajax 重发原请求。
 *
 * 本测试用 jsdom 加载真实 tts_form.js，验证四条行为：
 *   ① 有脏表单 + /tab/ 切页 → preventDefault + ConfirmDialog.show（页内）
 *   ② 对话框确认 → 清脏 + htmx.ajax('get', 原path, '#tab-content') 重发
 *   ③ 生成请求（/api/...）的响应交换不触发切页守卫
 *   ④ 无脏表单时切页不拦截
 *
 * Usage: node tests/frontend/tts_form_guard.js
 */
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

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

function freshDom() {
  const dom = new JSDOM(`<!doctype html><html><body>
    <main><div id="tab-content">
      <form id="f"><textarea name="text"></textarea></form>
    </div></main>
  </body></html>`, { url: "http://127.0.0.1:7869/", runScripts: "outside-only" });
  const w = dom.window;
  w.confirm = () => true; // jsdom 不实现 confirm；守卫兜底分支不应被走到
  const calls = { confirmDialog: [], htmxAjax: [] };
  w.ConfirmDialog = {
    show: (msg, onConfirm) => { calls.confirmDialog.push({ msg, onConfirm }); },
  };
  w.htmx = {
    ajax: (verb, p, target) => { calls.htmxAjax.push({ verb, path: p, target }); },
  };
  const src = fs.readFileSync(
    path.join(__dirname, "..", "..", "app", "integrated_app", "static", "js", "tts_form.js"),
    "utf-8",
  );
  w.eval(src);
  return { w, calls };
}

function makeDirty(w) {
  const ta = w.document.querySelector('textarea[name="text"]');
  ta.value = "模拟用户输入的合成文本";
  ta.dispatchEvent(new w.Event("input", { bubbles: true }));
}

function fireBeforeSwap(w, verb, p) {
  const ev = new w.CustomEvent("htmx:beforeSwap", {
    detail: { requestConfig: { verb, path: p } },
    cancelable: true,
    bubbles: true,
  });
  w.document.dispatchEvent(ev);
  return ev;
}

// ① 脏表单 + /tab/ 切页 → 页内对话框接管
{
  const { w, calls } = freshDom();
  makeDirty(w);
  const ev = fireBeforeSwap(w, "get", "/tab/history");
  assert(ev.defaultPrevented, "① /tab/ 切页在脏表单下被 preventDefault");
  assert(calls.confirmDialog.length === 1, "① 走页内 ConfirmDialog.show 而非 window.confirm");
  assert(calls.confirmDialog[0].msg.includes("确定继续切换吗"), "① 提示文案保留原语义");
  assert(typeof calls.confirmDialog[0].onConfirm === "function", "① 携带确认回调");
}

// ② 确认后清脏并重发原请求
{
  const { w, calls } = freshDom();
  makeDirty(w);
  fireBeforeSwap(w, "get", "/tab/history");
  assert(calls.htmxAjax.length === 0, "② 确认前不重发请求");
  calls.confirmDialog[0].onConfirm();
  assert(calls.htmxAjax.length === 1, "② 确认后重发原请求");
  assert(calls.htmxAjax[0].verb === "get" && calls.htmxAjax[0].path === "/tab/history", "② 重发动词与路径一致");
  assert(calls.htmxAjax[0].target === "#tab-content", "② 重发目标为 #tab-content");
  const again = fireBeforeSwap(w, "get", "/tab/settings");
  assert(!again.defaultPrevented && calls.confirmDialog.length === 1, "② 清脏后同会话再切页不再拦截");
}

// ③ 生成请求的响应交换不触发守卫
{
  const { w, calls } = freshDom();
  makeDirty(w);
  const ev = fireBeforeSwap(w, "post", "/api/generate/voxcpm_clone");
  assert(!ev.defaultPrevented, "③ /api/ 交换不拦截");
  assert(calls.confirmDialog.length === 0, "③ 生成结果交换不弹对话框");
}

// ④ 无脏表单时切页不拦截
{
  const { w, calls } = freshDom();
  const ev = fireBeforeSwap(w, "get", "/tab/history");
  assert(!ev.defaultPrevented, "④ 干净表单切页放行");
  assert(calls.confirmDialog.length === 0, "④ 干净表单不弹对话框");
}

console.log(`\n=== RESULT: pass=${pass} fail=${fail} ===`);
process.exit(fail > 0 ? 1 : 0);
