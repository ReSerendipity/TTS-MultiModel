/*
 * TTS MultiModel 桌面壳前端桥接（desktop-bridge.js）
 * ------------------------------------------------------------------
 * 由 Tauri 侧通过 `initialization_script` 注入到主窗口与更新窗口
 * （见 src-tauri/src/window.rs、main.rs），因此**不依赖任何 CDN 或
 * withGlobalTauri 的 window.__TAURI__**：直接走内核注入的
 * `window.__TAURI_INTERNALS__` IPC 通道。
 *
 * 设计约束：
 * 1. 浏览器模式降级：用户在纯浏览器（start.bat + Chrome）打开应用时
 *    `window.__TAURI_INTERNALS__` 不存在，本脚本整体 no-op，绝不报错。
 * 2. 主应用运行在后端源 http://127.0.0.1:PORT，其页面里没有 window.__TAURI__，
 *    但初始化脚本已注入 __TAURI_INTERNALS__，故 IPC 可用（capability 放行本机源）。
 * 3. 任务 4「禁止 CDN」：文件内不外链任何第三方脚本。
 *
 * 与 Rust 的事件/命令契约：
 *   命令(invoke): set_window_busy, show_main_window, save_window_state,
 *                read_dragged_file, check_update, get_pending_update,
 *                get_update_state, start_update, dismiss_update,
 *                open_update_dialog, show_notification
 *   事件(Rust→本窗/广播): update-available, update-progress, update-done,
 *                update-error, open-update-dialog, update-not-available,
 *                update-check-failed
 */
(function () {
  "use strict";
  var I = window.__TAURI_INTERNALS__;
  if (!I || typeof I.invoke !== "function") {
    // 浏览器模式：静默退出，壳功能全部降级为不启用
    return;
  }
  if (window.__TTS_MULTIMODEL_BRIDGE__) return; // 幂等：避免 init 脚本 + HTML 内联双份执行
  window.__TTS_MULTIMODEL_BRIDGE__ = true;

  // ---------- IPC 基础封装 ----------
  function invoke(cmd, args) {
    return I.invoke(cmd, args || {});
  }
  function xform(cb) {
    return I.transformCallback(cb);
  }
  var listeners = {}; // event -> [unlistenFn]
  function listen(event, handler) {
    return invoke("plugin:event|listen", {
      event: event,
      target: { kind: "Any" },
      handler: xform(function (e) {
        try { handler(e.payload); } catch (err) { console.error("bridge listener", err); }
      }),
    }).then(function (id) {
      var un = function () {
        return invoke("plugin:event|unlisten", { event: event, eventId: id }).catch(function () {});
      };
      (listeners[event] = listeners[event] || []).push(un);
      return un;
    });
  }

  // ---------- 小工具 ----------
  function toast(msg, type) {
    try {
      // TTS MultiModel 前端命名空间：TTSApp.toast.show
      if (window.TTSApp && window.TTSApp.toast && typeof window.TTSApp.toast.show === "function") {
        window.TTSApp.toast.show(msg, type || "info");
        return;
      }
      if (window.Toast && typeof window.Toast.show === "function") {
        window.Toast.show(msg, type || "info");
        return;
      }
    } catch (e) {}
    // 后端 app.js 尚未就绪时兜底
    var box = document.createElement("div");
    box.textContent = msg;
    box.style.cssText = "position:fixed;top:16px;left:50%;transform:translateX(-50%);z-index:99999;padding:10px 18px;border-radius:8px;background:#222;color:#fff;font:14px system-ui;box-shadow:0 4px 16px rgba(0,0,0,.25)";
    document.body && document.body.appendChild(box);
    setTimeout(function () { box.remove(); }, 3500);
  }
  function fmtSize(b) {
    if (!b || b <= 0) return "未知";
    if (b < 1024) return b + " B";
    if (b < 1048576) return (b / 1024).toFixed(1) + " KB";
    return (b / 1048576).toFixed(1) + " MB";
  }
  // TTS MultiModel 支持拖入的素材类型：参考音频 / 训练素材（WAV/MP3/FLAC/OGG 等）
  function isSupportedMedia(name) {
    return /\.(wav|mp3|flac|ogg|m4a|aac|wma|pcm|opus)$/i.test(name) ||
           /\.(png|jpe?g|bmp|webp|gif)$/i.test(name);
  }

  // ---------- 任务 3：系统通知（前端→Rust）----------
  // 任务完成时 app.js 里已有 toast；桥接额外向壳请求原生通知（后台时才弹，Rust 侧判定）
  function notifyShell(title, body) {
    invoke("show_notification", { title: title, body: body || "" }).catch(function () {});
  }
  // 通知去重：同一标题 30s 内只发一次（显式钩子与 DOM 观察器双保险时避免重复弹）
  var _lastNotify = { k: "", t: 0 };
  function notifyTaskDone(title, body) {
    var now = Date.now();
    if (_lastNotify.k === title && now - _lastNotify.t < 30000) return;
    _lastNotify = { k: title, t: now };
    notifyShell(title, body);
  }

  // ---------- 任务 2：窗口标题动态更新（显式钩子，供 app.js 调用）----------
  var taskActive = false;
  function setBusy(busy) {
    busy = !!busy;
    if (busy === taskActive) return;
    taskActive = busy;
    invoke("set_window_busy", { busy: busy }).catch(function () {});
  }
  function onTaskStart() {
    setBusy(true);
  }
  function onTaskEnd(status) {
    setBusy(false);
    var ok = status === "completed";
    notifyTaskDone(
      ok ? "TTSMultiModel 处理完成" : "TTSMultiModel 处理结束",
      ok ? "任务已完成，点击查看结果" : "任务已结束"
    );
  }

  // 探测任务忙闲：TTS 合成通过 htmx 表单（hx-post=/api/generate/...）同步完成，
  // 辅以进度卡片/状态徽标 DOM 观察，避免深改页面脚本。
  var GENERATE_RE = /\/api\/generate\//;
  function detectTaskActivity() {
    var el = document.querySelector("[id*='spinner' i][class*='htmx-request' i],[id*='progress' i],.sv-badge-processing");
    if (el) return true;
    var st = "";
    var txt = document.querySelector("[id*='status' i],[class*='task-status']");
    if (txt) st = (txt.textContent || "") + " " + (txt.className || "");
    return /processing|running|合成中|生成中|克隆中|训练中|处理中|上传中|加载中|进行中/i.test(st);
  }
  function watchTaskLifecycle() {
    // 主信号：htmx 表单请求生命周期（合成/克隆/训练均走 hx-post=/api/generate/...）
    document.addEventListener("htmx:beforeRequest", function (evt) {
      var target = evt.detail && evt.detail.requestConfig;
      var url = (target && (target.path || target.url)) || "";
      if (GENERATE_RE.test(url)) setBusy(true);
    });
    document.addEventListener("htmx:afterRequest", function (evt) {
      var target = evt.detail && evt.detail.requestConfig;
      var url = (target && (target.path || target.url)) || "";
      if (GENERATE_RE.test(url)) {
        setBusy(false);
        var ok = evt.detail && evt.detail.successful;
        notifyTaskDone(
          ok ? "TTSMultiModel 合成完成" : "TTSMultiModel 合成结束",
          ok ? "任务已完成，点击查看结果" : "任务已结束（请查看页面提示）"
        );
      }
    });
    // 兜底：轮询忙闲状态（非 htmx 页面/旧任务流）
    var idleTicks = 0;
    setInterval(function () {
      if (taskActive) {
        if (detectTaskActivity()) {
          idleTicks = 0;
        } else {
          idleTicks++;
          if (idleTicks >= 6) {
            setBusy(false);
            idleTicks = 0;
          }
        }
      }
    }, 1000);
  }

  // 通过全局 SSE 事件精确感知完成（若页面暴露了钩子则优先用，否则兜底轮询）
  function hookSseCompletion() {
    if (!window.EventSource) return;
    var mo = new MutationObserver(function () {
      var done = document.querySelector(".sv-badge-completed");
      var processing = document.querySelector(".sv-badge-processing,[class*='processing']");
      if (taskActive && !processing && done) {
        setBusy(false);
        notifyTaskDone("TTSMultiModel 合成完成", "任务已完成，点击查看详情");
      }
    });
    if (document.body) mo.observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ["class"] });
  }

  // ---------- 任务 4：文件拖拽 ----------
  function findUploadInput() {
    // TTS 页面：参考音频输入 #it2-ref-audio-input，或任意 .tts-file-upload 下的音频输入
    var el = document.getElementById("it2-ref-audio-input");
    if (el) return el;
    var any = document.querySelector(".tts-file-upload input[type='file'], .file-upload-area input[type='file']");
    return any || null;
  }
  function handleDropPaths(paths) {
    if (!paths || !paths.length) return;
    var input = findUploadInput();
    if (!input) {
      toast("请打开「语音合成/声音克隆」页面后使用文件拖拽", "warning");
      return;
    }
    var files = paths.filter(function (p) { return !/[\\/]$/.test(p) && isSupportedMedia(p); });
    var dirs = paths.filter(function (p) { return /[\\/]$/.test(p) || !isSupportedMedia(p); });
    if (dirs.length > 0) {
      toast("桌面端暂不支持拖入文件夹，请拖入单个音频文件", "info");
    }
    if (!files.length) return;
    if (files.length > 1) {
      toast("一次请拖入一个参考音频文件", "info");
    }
    injectSingleFile(files[0], input);
  }
  function injectSingleFile(path, input) {
    invoke("read_dragged_file", { path: path }).then(function (b64) {
      var bin = atob(b64);
      var len = bin.length;
      var bytes = new Uint8Array(len);
      for (var i = 0; i < len; i++) bytes[i] = bin.charCodeAt(i);
      var name = path.split(/[\\/]/).pop() || "dropped";
      var type = guessMime(name);
      var file = new File([bytes], name, { type: type });
      var dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
      // 触发 TTS 页面的 onchange（更新文件名展示与校验状态）
      input.dispatchEvent(new Event("change", { bubbles: true }));
      toast("已载入参考音频：" + name + "，完善文本后点击开始合成", "success");
    }).catch(function (e) {
      toast("读取拖入文件失败：" + (e && e.message ? e.message : e), "error");
    });
  }
  function guessMime(name) {
    var m = name.toLowerCase().match(/\.(\w+)$/);
    var ext = m && m[1];
    var map = { wav: "audio/wav", mp3: "audio/mpeg", flac: "audio/flac", ogg: "audio/ogg", m4a: "audio/mp4", aac: "audio/aac", wma: "audio/x-ms-wma", pcm: "audio/x-pcm", opus: "audio/opus", png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", webp: "image/webp", bmp: "image/bmp", gif: "image/gif" };
    return map[ext] || "application/octet-stream";
  }

  // ---------- 任务 8：更新 UI ----------
  var dlgEl = null;
  function ensureDialog() {
    if (dlgEl || !document.body) return dlgEl;
    dlgEl = document.createElement("div");
    dlgEl.id = "sv-desktop-update-dialog";
    dlgEl.style.cssText = "position:fixed;inset:0;z-index:99998;display:none;align-items:center;justify-content:center;background:rgba(0,0,0,.45);font-family:'Segoe UI','Microsoft YaHei',system-ui,sans-serif";
    dlgEl.innerHTML =
      '<div style="width:440px;max-width:92vw;background:#fff;color:#222;border-radius:14px;box-shadow:0 20px 60px rgba(0,0,0,.35);overflow:hidden">' +
      '  <div style="padding:18px 22px;font-weight:600;font-size:17px;background:linear-gradient(135deg,#e94560,#f39c12);color:#fff">TTSMultiModel 更新</div>' +
      '  <div id="sdu-body" style="padding:20px 22px;font-size:14px;line-height:1.7"></div>' +
      '  <div style="padding:0 22px 20px;display:flex;gap:10px;justify-content:flex-end" id="sdu-actions"></div>' +
      '</div>';
    document.body.appendChild(dlgEl);
    return dlgEl;
  }
  function showUpdateDialog(info) {
    ensureDialog();
    var body = document.getElementById("sdu-body");
    var actions = document.getElementById("sdu-actions");
    if (!dlgEl || !body) return;
    if (!info) {
      body.innerHTML = '<div>当前已是最新版本。</div>';
      actions.innerHTML = '<button data-a="close" style="padding:8px 20px;border:none;border-radius:6px;background:#eee;cursor:pointer">关闭</button>';
    } else {
      var log = escapeHtml(info.changelog || "（无变更日志）");
      body.innerHTML =
        '<div style="display:flex;justify-content:space-between;margin-bottom:8px"><span>当前版本</span><strong>' + escapeHtml(info.current || "-") + '</strong></div>' +
        '<div style="display:flex;justify-content:space-between;margin-bottom:12px"><span>最新版本</span><strong style="color:#e94560">' + escapeHtml(info.version || "-") + '</strong></div>' +
        '<div style="margin-bottom:6px"><span>更新包大小</span>：<strong>' + fmtSize(info.size) + '</strong></div>' +
        '<div style="margin:10px 0 4px;color:#666">变更日志</div>' +
        '<div style="max-height:160px;overflow:auto;background:#f7f7f9;border-radius:8px;padding:10px 12px;font-size:13px;white-space:pre-wrap">' + log + '</div>';
      actions.innerHTML =
        '<button data-a="later" style="padding:8px 18px;border:1px solid #ddd;border-radius:6px;background:#fff;cursor:pointer">稍后提醒</button>' +
        '<button data-a="now" style="padding:8px 22px;border:none;border-radius:6px;background:#e94560;color:#fff;cursor:pointer;font-weight:600">立即更新</button>';
    }
    dlgEl.style.display = "flex";
    actions.onclick = function (e) {
      var a = e.target.getAttribute("data-a");
      if (a === "close") closeDialog();
      else if (a === "later") { invoke("dismiss_update").catch(function(){}); closeDialog(); }
      else if (a === "now") startUpdateFlow();
    };
  }
  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function closeDialog() { if (dlgEl) dlgEl.style.display = "none"; }

  function renderProgress(prog) {
    ensureDialog();
    var body = document.getElementById("sdu-body");
    var actions = document.getElementById("sdu-actions");
    if (!body) return;
    var labels = { download: "下载中", verify: "校验中", extract: "解压中", swap: "换载中", restart: "重启中" };
    var pct = typeof prog.percent === "number" ? prog.percent : 0;
    body.innerHTML =
      '<div style="margin-bottom:10px;font-weight:600">' + escapeHtml(labels[prog.phase] || prog.phase) + '</div>' +
      '<div style="height:10px;border-radius:6px;background:#eee;overflow:hidden"><div style="height:100%;width:' + pct + '%;background:linear-gradient(90deg,#e94560,#f39c12);transition:width .3s"></div></div>' +
      '<div style="margin-top:8px;color:#666;font-size:13px">' + escapeHtml(prog.message || (pct + "%")) + '</div>';
    actions.innerHTML = '<span style="color:#999;font-size:13px">请勿关闭窗口…</span>';
    dlgEl.style.display = "flex";
  }

  function startUpdateFlow() {
    renderProgress({ phase: "download", percent: 0, message: "准备下载…" });
    invoke("start_update").catch(function (e) {
      showUpdateDialogError(e && e.message ? e.message : String(e));
    });
  }
  function showUpdateDialogError(msg) {
    var body = document.getElementById("sdu-body");
    var actions = document.getElementById("sdu-actions");
    if (body) body.innerHTML = '<div style="color:#c0392b">更新失败（已自动回滚，应用不受影响）：</div><div style="margin-top:8px;font-size:13px">' + escapeHtml(msg) + "</div>";
    if (actions) actions.innerHTML = '<button data-a="close" style="padding:8px 20px;border:none;border-radius:6px;background:#eee;cursor:pointer">关闭</button>';
    if (dlgEl) { dlgEl.style.display = "flex"; actions && (actions.onclick = function (e) { if (e.target.getAttribute("data-a") === "close") closeDialog(); }); }
  }

  // ---------- 事件订阅 ----------
  function wireEvents() {
    listen("update-available", function (info) {
      // 托盘手动检查会 emit open-update-dialog；这里是后台发现更新，标记即可，不主动打扰
      if (window.location.pathname.indexOf("update") >= 0) showUpdateDialog(info);
      else tryShowOnUpdateWindow(info);
    });
    listen("update-progress", function (p) { renderProgress(p); });
    listen("update-done", function (r) {
      notifyShell("TTSMultiModel 更新完成", (r && r.message) || "应用已更新到最新版本");
      var body = document.getElementById("sdu-body");
      var actions = document.getElementById("sdu-actions");
      if (body) body.innerHTML = '<div style="color:#27ae60;font-weight:600;font-size:16px;text-align:center;padding:20px 0">✓ ' + escapeHtml((r && r.message) || "更新完成") + "</div>";
      if (actions) actions.innerHTML = '<button data-a="close" style="padding:8px 22px;border:none;border-radius:6px;background:#e94560;color:#fff;cursor:pointer">完成</button>' + (actions.onclick = function (e) { if (e.target.getAttribute("data-a") === "close") closeDialog(); });
    });
    listen("update-error", function (e) { showUpdateDialogError((e && e.message) || "更新失败"); });
    listen("open-update-dialog", function () { showUpdateDialogFromState(); });
    listen("update-not-available", function () { showUpdateDialog(null); });
    listen("update-check-failed", function (e) { var m = (e && e.message) || ""; showUpdateDialogError("检查更新失败：" + m + (m && /请求|连接|超时|TLS|网络|HTTP/i.test(m) ? "\n\n提示：无法连接到更新服务器（GitHub）。请检查网络连接或代理设置后重试。" : "")); });
  }
  function tryShowOnUpdateWindow(info) {
    // 若更新窗口已存在（本窗非 update.html），交给它自己拉取；主窗只提示角标
    toast("发现新版本 " + (info.version || ""), "info");
  }
  function showUpdateDialogFromState() {
    Promise.all([invoke("get_update_state", {}), invoke("get_pending_update", {}).catch(function(){return null;})])
      .then(function (res) {
        var st = res[0] || {};
        var pending = (st.pending && st.pending.info) || res[1] || null;
        showUpdateDialog(pending);
      })
      .catch(function (e) { showUpdateDialogError("无法获取版本信息：" + (e && e.message ? e.message : e)); });
  }

  // ---------- 拖拽事件（Tauri 原生 → 本窗）----------
  function wireDrag() {
    // 关闭 WebView 原生文件导航兜底（HTML5 模式下页面自身的 drop 处理器优先；
    // 这里只接 Tauri 的 tauri://drag-drop，payload 带真实绝对路径）
    listen("tauri://drag-drop", function (p) {
      if (p && p.paths) handleDropPaths(p.paths);
    });
  }

  // ---------- 更新窗口（update.html）专用：加载即拉取状态 ----------
  function initUpdateWindow() {
    if (window.location.pathname.indexOf("update") < 0) return;
    // 更新窗口独立存在：桥接加载后主动拉取缓存结果
    document.addEventListener("DOMContentLoaded", function () {
      showUpdateDialogFromState();
    });
    if (document.readyState !== "loading") showUpdateDialogFromState();
  }

  // ---------- 主入口 ----------
  function boot() {
    // 防御：极端注入时序下 document 可能尚未就绪（正常 WebView2 恒存在）。
    // 无 document 时不触碰 DOM；文件尾部的顶层导出（__tts_multimodelShell）仍会执行。
    if (typeof document === "undefined") return;
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", function () { wire(); });
    } else {
      wire();
    }
  }
  function wire() {
    wireEvents();
    wireDrag();
    initUpdateWindow();
    // 主应用页面才挂任务生命周期观察器（TTS 页面标记：htmx 合成表单/音频上传区）
    if (document.getElementById("it2-form") || findUploadInput() || document.querySelector(".tts-file-upload, .file-upload-area")) {
      watchTaskLifecycle();
      hookSseCompletion();
    }
  }
  boot();

  // 暴露给控制台/其它脚本（非必需，便于调试与 AI-3 集成）
  window.__tts_multimodelShell = {
    invoke: invoke,
    notify: notifyShell,
    showUpdateDialog: showUpdateDialog,
    checkUpdate: function () { return invoke("check_update", { auto: false }); },
    // 供 app.js / 页面脚本在任务生命周期节点调用（浏览器模式不存在，调用方需判空）
    onTaskStart: onTaskStart,
    onTaskEnd: onTaskEnd,
  };
})();
