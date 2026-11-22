/**
 * Engine Strip — 生成区上方常驻引擎状态条。
 *
 * 解决的问题（2026-11-22 演示实测）：模型未加载或引擎与本页不匹配时，
 * 用户只有点「生成」撞到错误 toast 才知道要切换/加载引擎，且 toast 消失后
 * 加载入口随之消失（结果音频面板在上方，加载入口却不在旁边）。
 *
 * 行为：
 *   1. 读取当前激活侧边栏项的 data-model（本页所需引擎）；
 *   2. 与全局当前引擎（window.__CURRENT_ENGINE__，model_switcher.js 维护）比对；
 *   3. 不匹配或未加载时，在生成表单上方渲染提示条 + 「切换并加载」一键按钮；
 *   4. 匹配时隐藏。
 *
 * 挂载点：DOM ready / htmx:afterSwap（tab 切换）/ 3s 轮询（引擎状态异步更新后同步）。
 */
(function () {
    'use strict';

    var ESC = function (s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    };

    function neededEngine() {
        var active = document.querySelector('button.sidebar-item.active');
        return active ? active.getAttribute('data-model') : null;
    }

    function ensureStrip() {
        var needed = neededEngine();
        if (!needed) return;
        var current = window.__CURRENT_ENGINE__ || null;
        var anchor = document.querySelector('#tab-content form') || document.querySelector('#tab-content');
        if (!anchor) return;

        var strip = document.getElementById('engine-strip');
        if (!strip) {
            strip = document.createElement('div');
            strip.id = 'engine-strip';
            strip.setAttribute('role', 'status');
            anchor.parentNode.insertBefore(strip, anchor);
        }

        var mismatch = current && current !== needed;
        var unloaded = !current;
        if (mismatch || unloaded) {
            var msg = unloaded
                ? (ESC(window.I18N && window.I18N['model_none'] || '引擎未加载'))
                : (ESC(window.I18N && window.I18N['engine_mismatch_prefix'] || '当前引擎')
                    + ' ' + ESC(current) + ' · '
                    + ESC(window.I18N && window.I18N['engine_needed'] || '本页需要')
                    + ' ' + ESC(needed));
            strip.innerHTML = '<span class="engine-strip-msg">' + msg + '</span>'
                + '<button type="button" class="engine-strip-btn" onclick="window.switchModel(\'' + needed + '\')">'
                + ESC(window.I18N && window.I18N['engine_load_switch'] || '切换并加载')
                + '</button>';
            strip.style.display = 'flex';
        } else {
            strip.style.display = 'none';
        }
    }

    document.addEventListener('DOMContentLoaded', function () { setTimeout(ensureStrip, 400); });
    document.body.addEventListener('htmx:afterSwap', function () { setTimeout(ensureStrip, 500); });
    setInterval(ensureStrip, 3000);
})();
