/* ===== SSE Manager — 统一 SSE 事件通道（GET /api/sse/events） =====
 *
 * 此前本文件为空，后端 sse.py 广播的 progress/complete/cancelled/error/
 * status/engine_switch/model_load/time_estimate 事件无人消费，
 * base.html 的全局进度条（#progress-container）是没有任何驱动的死 UI。
 *
 * 职责：
 *   1. 维持单条 EventSource 连接（浏览器原生断线重连），向任意模块提供
 *      SSEManager.on(type, fn) 订阅接口；
 *   2. 内置默认 UI 行为：
 *      - progress            → 激活顶部进度条，写填充宽度与 phase/progress/speed/remaining 明细
 *      - complete            → 置 100% 后延迟收起（不 Toast，结果由各页自身渲染）
 *      - cancelled / error   → 收起并 Toast 提示
 *      - time_estimate       → 更新"剩余时间"明细
 *      - engine_switch       → 切换结束(idle/completed/failed/inactive)时派发
 *                              'engine_switch_cleanup' DOM 事件，接活 model_switcher
 *                              里悬空的清理钩子
 *      - status / model_load → 仅存入 getState() 供其它模块读取
 *   3. 定义 window.updateProgressDetail(phase, progress, speed, remaining) 基础实现——
 *      voice_design.html 等页面包裹的就是这个全局函数，此前它从未被定义。
 *
 * 事件 payload 见 routes/sse.py：progress={html,phase,progress,speed,remaining}，
 * complete 为纯文本 "done"，cancelled/error={status,message}，
 * time_estimate={status,elapsed,remaining,total_est,text}。
 */
window.SSEManager = (function () {
    'use strict';

    var SSE_URL = '/api/sse/events';
    var EVENT_TYPES = ['progress', 'complete', 'cancelled', 'error', 'status', 'engine_switch', 'model_load', 'time_estimate'];

    var _es = null;
    var _handlers = {};
    var _state = {};
    var _hideTimer = null;

    function _toast(msg, type) {
        try {
            if (window.Toast && typeof Toast.show === 'function') {
                Toast.show(msg, type || 'info');
                return;
            }
        } catch (e) { /* fallthrough */ }
        if (typeof window.showToast === 'function') window.showToast(msg, type);
    }

    function _dispatchDOM(eventName, detail) {
        try {
            document.dispatchEvent(new CustomEvent(eventName, { detail: detail || {} }));
        } catch (e) { /* CustomEvent 不可用时静默 */ }
    }

    /* ---- 全局进度条 UI ---- */

    function _ensureFill() {
        var area = document.getElementById('progress-bar');
        if (!area) return null;
        var fill = document.getElementById('sse-progress-fill');
        if (!fill) {
            fill = document.createElement('div');
            fill.id = 'sse-progress-fill';
            fill.style.cssText = 'height:100%;width:0%;border-radius:999px;' +
                'background:linear-gradient(90deg, var(--p500, #8b5cf6), var(--p600, #7c3aed));' +
                'transition:width .35s ease;';
            area.appendChild(fill);
        }
        return fill;
    }

    function _setActive(on) {
        var container = document.getElementById('progress-container');
        if (!container) return;
        container.classList.toggle('active', !!on);
        if (!on && _hideTimer) {
            clearTimeout(_hideTimer);
            _hideTimer = null;
        }
    }

    /* 基础实现：此前 voice_design.html 等页面包裹的 window.updateProgressDetail 并不存在 */
    window.updateProgressDetail = function (phase, progress, speed, remaining) {
        function set(id, v) {
            var el = document.getElementById(id);
            if (el) el.textContent = (v === undefined || v === null || v === '') ? '-' : String(v);
        }
        set('detail-phase', phase);
        set('detail-progress', progress);
        set('detail-speed', speed);
        set('detail-remaining', remaining);
    };

    function _showGenerationProgress(data) {
        if (!data || typeof data !== 'object') return;
        _setActive(true);
        var pct = Math.max(0, Math.min(100, parseInt(data.progress, 10) || 0));
        var fill = _ensureFill();
        if (fill) fill.style.width = pct + '%';
        window.updateProgressDetail(data.phase || '', pct + '%', data.speed || '', data.remaining || '');
    }

    function _finishGeneration(kind, data) {
        var fill = _ensureFill();
        if (fill) fill.style.width = '100%';
        var phaseText = kind === 'complete' ? '完成' : (kind === 'cancelled' ? '已取消' : '失败');
        window.updateProgressDetail(phaseText, '100%', '', '');
        if (_hideTimer) clearTimeout(_hideTimer);
        _hideTimer = setTimeout(function () {
            _setActive(false);
            if (fill) fill.style.width = '0%';
            window.updateProgressDetail('-', '-', '-', '-');
        }, 1200);
        if (kind === 'cancelled' && data && data.message) _toast(data.message, 'info');
        if (kind === 'error' && data && data.message) _toast(data.message, 'error');
    }

    /* ---- 默认事件处理 ---- */

    var _defaultHandlers = {
        progress: function (data) { _showGenerationProgress(data); },
        complete: function () { _finishGeneration('complete', null); },
        cancelled: function (data) { _finishGeneration('cancelled', data); },
        error: function (data) { _finishGeneration('error', data); },
        status: function (data) { /* 仅存 state，引擎状态 UI 归 model_switcher 管 */ },
        model_load: function (data) { /* 仅存 state */ },
        engine_switch: function (data) {
            // 切换结束/失败/空闲 → 接活 model_switcher 的清理钩子
            if (data && (data.active === false || data.status === 'completed' || data.status === 'idle' || data.status === 'failed')) {
                _dispatchDOM('engine_switch_cleanup', { source: 'sse' });
            }
        },
        time_estimate: function (data) {
            if (!data || typeof data !== 'object') return;
            if ((data.status === 'generating' || data.status === 'complete') && data.text) {
                _setActive(true);
                window.updateProgressDetail(undefined, undefined, undefined, data.text);
            }
        }
    };

    function _onEvent(type, e) {
        var data = e.data;
        try { data = JSON.parse(e.data); } catch (_) { /* 纯文本事件（如 complete:"done"）保留原样 */ }
        _state[type] = { data: data, ts: Date.now() };
        _dispatchDOM('tts-sse', { type: type, data: data });
        var hs = _handlersFor(type);
        for (var i = 0; i < hs.length; i++) {
            try { hs[i](data, e); } catch (err) { console.error('[SSEManager] handler error (' + type + '):', err); }
        }
    }

    /* ---- 公共 API ---- */

    function connect() {
        if (_es || typeof window.EventSource === 'undefined') return _es;
        try {
            _es = new EventSource(SSE_URL);
        } catch (err) {
            console.error('[SSEManager] EventSource 创建失败:', err);
            return null;
        }
        _es.onopen = function () { _state._connected = true; };
        _es.onerror = function () { _state._connected = false; /* 浏览器原生自动重连 */ };
        EVENT_TYPES.forEach(function (type) {
            _es.addEventListener(type, function (e) { _onEvent(type, e); });
        });
        return _es;
    }

    function disconnect() {
        if (_es) {
            try { _es.close(); } catch (e) { /* ignore */ }
            _es = null;
            _state._connected = false;
        }
    }

    function on(type, fn) {
        if (typeof fn !== 'function') return;
        (_handlers[type] = _handlers[type] || []).push(fn);
    }

    function off(type, fn) {
        var hs = _handlers[type];
        if (!hs) return;
        var idx = hs.indexOf(fn);
        if (idx !== -1) hs.splice(idx, 1);
    }

    function getState() { return _state; }

    function isConnected() { return !!(_es && _state._connected); }

    /* 绑定默认 UI 行为（在 connect 前注册即可） */
    Object.keys(_defaultHandlers).forEach(function (type) {
        on(type, _defaultHandlers[type]);
    });

    /* 自启动：DOM 就绪后建立连接（base.html 静态进度条已存在，事件早到也有守卫） */
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', connect);
    } else {
        connect();
    }

    return {
        connect: connect,
        disconnect: disconnect,
        on: on,
        off: off,
        getState: getState,
        isConnected: isConnected,
        EVENT_TYPES: EVENT_TYPES
    };
})();
