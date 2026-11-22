/**
 * Draft Saver — 表单草稿持久化（B7）。
 *
 * 解决的问题：切换页面时表单内容丢失（应用已弹确认框，但长文本误触导航后需重填）。
 * 范围：#tab-content 内的所有 textarea（合成文本/剧本/描述等纯文本字段）；
 *       文件选择、复选框等非文本状态不在持久化范围（安全与复杂度考虑）。
 * 存储：sessionStorage（key: tts_draft_<tab>_<index>），关闭浏览器标签页即清空，
 *       不会把草稿带到下一次应用会话（避免用户困惑的陈旧草稿）。
 * 触发：input 事件（debounce 500ms）保存；htmx:afterSwap 恢复。
 */
(function () {
    'use strict';

    var KEY_PREFIX = 'tts_draft_';
    var SAVE_TIMER = null;

    function tabKey() {
        var active = document.querySelector('button.sidebar-item.active');
        return (active && active.getAttribute('data-tab')) || 'unknown';
    }

    function collect() {
        var out = {};
        var areas = document.querySelectorAll('#tab-content textarea');
        areas.forEach(function (ta, i) {
            var v = ta.value;
            if (v) out['f' + i] = v;
        });
        return out;
    }

    function save() {
        try {
            var data = collect();
            if (Object.keys(data).length === 0) {
                sessionStorage.removeItem(KEY_PREFIX + tabKey());
                return;
            }
            sessionStorage.setItem(KEY_PREFIX + tabKey(), JSON.stringify(data));
        } catch (e) { /* 存储满/隐私模式：静默 */ }
    }

    function restore() {
        try {
            var raw = sessionStorage.getItem(KEY_PREFIX + tabKey());
            if (!raw) return;
            var data = JSON.parse(raw);
            var areas = document.querySelectorAll('#tab-content textarea');
            areas.forEach(function (ta, i) {
                var v = data['f' + i];
                if (v && !ta.value) {
                    ta.value = v;
                    ta.dispatchEvent(new Event('input', { bubbles: true }));
                }
            });
        } catch (e) { /* 恢复失败不影响页面 */ }
    }

    document.addEventListener('input', function (e) {
        if (!e.target.matches || !e.target.matches('#tab-content textarea')) return;
        if (SAVE_TIMER) clearTimeout(SAVE_TIMER);
        SAVE_TIMER = setTimeout(save, 500);
    });

    document.body.addEventListener('htmx:afterSwap', function () {
        setTimeout(restore, 400);
    });

    document.addEventListener('DOMContentLoaded', function () {
        setTimeout(restore, 600);
    });
})();
