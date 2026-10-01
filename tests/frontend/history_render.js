/**
 * TTS MultiModel — history 页行渲染回归测试（jsdom）
 *
 * 背景（2026-10-01 真机全流程模拟实测）：renderRows 把记录 id（r[0]）当文件名传给
 * inferEngine(filename)，forEach 内抛 `filename.toLowerCase is not a function` ——
 * tbody 已被清空、整批渲染失败、异常被 promise 静默吞掉。表现为「任何原地刷新
 * （隐藏/显示/同步/搜索/翻页）之后历史表全空」，而全新页面加载看到的是服务端
 * 静态行，所以平时看不出来。
 *
 * 夹具：_history_tab_capture.html 是从真机抓取的「已渲染 history tab 片段」
 * （含全部结构元素与内联脚本，服务端已注入 i18n）。重新抓取方式：
 *   起服务 → 浏览器打开 http://127.0.0.1:7869/ → 等就绪 →
 *   evaluate `document.getElementById('tab-content').outerHTML` 覆盖本文件。
 *
 * Usage: node tests/frontend/history_render.js
 */
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const capturePath = path.join(__dirname, "_rendered", "_history_tab_capture.html");
if (!fs.existsSync(capturePath)) {
  console.log("SKIP: _history_tab_capture.html 不存在（见文件头重新抓取说明）");
  process.exit(0);
}
const capture = fs.readFileSync(capturePath, "utf-8");

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

const STUB_RECORDS = [
  [901, "voxcpm_design_1790817378.wav", "2026-10-01 09:16:19", "42.5s", "0.6 MB"],
  [902, "indextts2_1789784109.wav", "2026-10-01 09:32:22", "51.3s", "0.4 MB"],
  [903, "indextts20_clone_1789784200.wav", "2026-10-01 09:34:24", "35.3s", "0.3 MB"],
];

const dom = new JSDOM(`<!doctype html><html><head></head><body><main><div id="tab-content">${capture}</div></main></body></html>`, {
  url: "http://127.0.0.1:7869/",
  runScripts: "outside-only",
  pretendToBeVisual: true,
});
const w = dom.window;
w.getCsrfToken = () => "stub";
w.Toast = { show: () => {} };
w.scrollTo = () => {};
w.confirm = () => true;
w.addEventListener("unhandledrejection", (e) => {
  fail++;
  console.log("  FAIL - 渲染链路抛了未处理异常: " + (e.reason && (e.reason.message || e.reason)));
});
let tableFetches = 0;
w.fetch = (url) => {
  const u = String(url);
  if (u.includes("/api/history/table")) {
    tableFetches++;
    return Promise.resolve({
      json: () => Promise.resolve({ status: "ok", records: STUB_RECORDS, total: STUB_RECORDS.length, hasMore: false, loaded: STUB_RECORDS.length }),
    });
  }
  return Promise.resolve({ json: () => Promise.resolve({ status: "ok" }) });
};

for (const s of w.document.querySelectorAll("#tab-content script")) {
  try {
    w.eval(s.textContent);
  } catch (e) {
    fail++;
    console.log("  FAIL - 内联脚本加载失败: " + e.message);
  }
}

// 真机上的复现路径：hideHistoryRecord 成功后走 refreshHistory() 原地重渲染。
// 这里等价地直接触发 refresh（同一渲染链路，去掉对 hide 端点返回值的耦合）。
w.refreshHistory();

setTimeout(() => {
  const tbody = w.document.getElementById("hist-table-body");
  const rows = tbody ? tbody.querySelectorAll("tr") : [];
  assert(tableFetches >= 1, "refreshHistory 发起了 /api/history/table 请求");
  assert(rows.length === STUB_RECORDS.length, `刷新后渲染 ${STUB_RECORDS.length} 行（修复前为 0 行：inferEngine(r[0]) 抛 TypeError）`);
  if (rows.length === STUB_RECORDS.length) {
    const badges = [...rows].map((r) => {
      const b = r.querySelector(".engine-badge");
      return b ? b.textContent.trim() : null;
    });
    assert(badges[0] === "VoxCPM2", "voxcpm_design_* 徽章 = VoxCPM2");
    assert(badges[1] === "IndexTTS 2.5", "indextts2_* 徽章 = IndexTTS 2.5（修复前 'idx' 前缀判断也匹配不上）");
    assert(badges[2] === "IndexTTS 2.0", "indextts20_* 徽章 = IndexTTS 2.0");
    const ids = [...tbody.querySelectorAll(".history-row-checkbox")].map((c) => c.getAttribute("data-history-id"));
    assert(ids.join(",") === "901,902,903", "行序与记录顺序一致（id 来自 r[0]）");
  }
  console.log(`\n=== RESULT: pass=${pass} fail=${fail} ===`);
  process.exit(fail > 0 ? 1 : 0);
}, 800);
