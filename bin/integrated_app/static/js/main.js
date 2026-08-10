/* ===== main.js - Entry Point & Initialization ===== */
/* All modules are loaded before this file via base.html script tags */

// Global i18n translations - initialized inline in base.html (Jinja2 template)
// window.I18N = {{ i18n_json|safe }};
// window.CURRENT_LANG = '{{ lang }}';

document.addEventListener('DOMContentLoaded', function() {
    // Populate TTSApp namespace with module references
    TTSApp.audio = window.GlobalAudioPlayer || window.globalAudioPlayer;
    TTSApp.theme = window.TTSTheme || { toggle: window.toggleTheme, switch: window.switchTheme };
    TTSApp.lang = window.TTSLang || { toggle: window.toggleLang, set: window.setLang };
    TTSApp.model = window.ModelSwitcher || { switch: window.switchModel, load: window.loadModel, unload: window.unloadModel };
    TTSApp.sse = window.SSEManager || {};
    TTSApp.sidebar = TTSApp.sidebar || { toggle: window.toggleSidebar, close: function(){}, toggleCollapse: function(){}, activateTab: function(){} };
    TTSApp.help = window.HelpDrawer || { toggle: window.toggleHelpDrawer, close: window.closeHelpDrawer };
    TTSApp.health = window.HealthMonitor || { open: window.openHealthPanel, close: window.closeHealthPanel };
    TTSApp.keyboard = window.keyboardManager;
    TTSApp.icons = window.IconConverter || {};
    TTSApp.micro = TTSApp.micro || {};
    TTSApp.toast = TTSApp.toast || {
        show: function(message, type) { if (window.Toast) Toast.show(message, type); },
        error: function(message) { this.show(message, 'error'); },
        success: function(message) { this.show(message, 'success'); },
        warning: function(message) { this.show(message, 'warning'); },
        info: function(message) { this.show(message, 'info'); }
    };

    // ARIA live region announcer for screen readers
    TTSApp.announce = function(message, priority) {
        var ariaLiveEl = document.getElementById('aria-live-status');
        if (!ariaLiveEl || !message) return;
        // Temporarily switch to assertive for high-priority announcements
        if (priority === 'assertive') {
            ariaLiveEl.setAttribute('aria-live', 'assertive');
        } else {
            ariaLiveEl.setAttribute('aria-live', 'polite');
        }
        // Clear first to ensure the announcement is triggered even if the text is the same
        ariaLiveEl.textContent = '';
        setTimeout(function() {
            ariaLiveEl.textContent = message;
            // Revert to polite after the announcement has been processed
            if (priority === 'assertive') {
                setTimeout(function() {
                    ariaLiveEl.setAttribute('aria-live', 'polite');
                }, 1000);
            }
        }, 100);
    };

    // Listen for server-triggered toast events
    document.body.addEventListener('tts-toast', function(evt) {
        if (evt.detail && evt.detail.message) {
            TTSApp.toast.show(evt.detail.message, evt.detail.type || 'info');
        }
    });

    // Initialize theme and language from localStorage
    try { if (window.TTSTheme) window.TTSTheme.init(); } catch(e) { console.error('[TTSApp] 主题模块初始化失败:', e); }
    try { if (window.TTSLang) window.TTSLang.init(); } catch(e) { console.error('[TTSApp] 语言模块初始化失败:', e); }

    // Tab restoration is handled by app_init.js - no need to trigger firstTab here

    try { if (window.initTTSUI) { window.initTTSUI(); } } catch(e) { console.error('[TTSApp] TTS界面模块初始化失败:', e); }

    // Task 15: Add click handler to progress bar for detail toggle
    var progressContainer = document.getElementById('progress-container');
    if (progressContainer) {
        progressContainer.addEventListener('click', function(e) {
            // Don't toggle if clicking the cancel button
            if (e.target.closest('.progress-cancel-btn')) return;
            if (window.toggleProgressDetail) {
                window.toggleProgressDetail();
            }
        });
    }

    // Init mini monitor widget (always-on top bar) - 只获取一次数据，不轮询
    try { if (window.initMiniMonitor) { window.initMiniMonitor(); } } catch(e) { console.error('[TTSApp] 迷你监控模块初始化失败:', e); }

    // Register keyboard shortcuts
    try {
    if (window.keyboardManager) {
        var km = window.keyboardManager;

        // Ctrl+L: Load model
        km.register('load_model', ['Control', 'L'],
            window.I18N['load_model_shortcut'] || '加载模型',
            function() { window.loadModel(); },
            'general'
        );

        // Ctrl+U: Unload model
        km.register('unload_model', ['Control', 'U'],
            window.I18N['unload_model_shortcut'] || '卸载模型',
            function() { window.unloadModel(); },
            'general'
        );

        // F1: Toggle shortcut help panel
        km.register('toggle_help', ['F1'],
            window.I18N['toggle_help_shortcut'] || '显示快捷键帮助',
            function() { window.toggleShortcutHelp(); },
            'general'
        );

        // Attach global keydown listener
        document.addEventListener('keydown', function(e) {
            window.keyboardManager.handleKeydown(e);
        });

        console.log('[KeyboardManager] All shortcuts registered');
    }
    } catch(e) { console.error('[TTSApp] 快捷键管理器初始化失败:', e); }

    // 网络状态监听
    try {
    window.addEventListener('offline', function() {
        showNetworkToast((window.I18N && window.I18N['network_disconnected']) || '网络已断开，请检查连接', 'error');
    });
    window.addEventListener('online', function() {
        showNetworkToast((window.I18N && window.I18N['network_reconnected']) || '网络已恢复', 'success');
    });
    function showNetworkToast(message, type) {
        var existing = document.getElementById('network-toast');
        if (existing) existing.remove();
        var toast = document.createElement('div');
        toast.id = 'network-toast';
        toast.className = 'network-toast network-toast-' + type;
        toast.textContent = message;
        document.body.appendChild(toast);
        if (type === 'success') {
            setTimeout(function() { toast.classList.add('network-toast-fadeout'); }, 3000);
            setTimeout(function() { toast.remove(); }, 3500);
        }
    }
    } catch(e) { console.error('[TTSApp] 网络状态监听初始化失败:', e); }

    // 首次使用引导
    if (!localStorage.getItem('app_onboarded')) {
        var hint = document.createElement('div');
        hint.id = 'onboard-hint';
        hint.className = 'onboard-hint';
        hint.innerHTML = '<div class="onboard-title">' + ((window.I18N && window.I18N['welcome']) || '欢迎使用！') + '</div><div class="onboard-steps">' + ((window.I18N && window.I18N['onboard_steps']) || '1. 输入文本 → 2. 选择音色 → 3. 点击生成') + '</div><button class="onboard-dismiss-btn" onclick="localStorage.setItem(\'app_onboarded\',\'1\');this.parentElement.remove();">' + ((window.I18N && window.I18N['got_it']) || '知道了') + '</button>';
        document.body.appendChild(hint);
    }

    // Detect scrollable tables and add hint class
    var tableContainers = document.querySelectorAll('.table-container, .history-table-wrapper');
    tableContainers.forEach(function(container) {
        function checkScroll() {
            if (container.scrollWidth > container.clientWidth) {
                container.classList.add('is-scrollable');
            } else {
                container.classList.remove('is-scrollable');
            }
        }
        checkScroll();
        container.addEventListener('scroll', checkScroll);
        window.addEventListener('resize', checkScroll);
    });
});

/* P1-3 I-02: HTMX long loading hint + sidebar-item visual */
(function uxFixHtmxIndicator() {
    'use strict';
    let longTimer = null;
    document.addEventListener('htmx:beforeRequest', (e) => {
        clearTimeout(longTimer);
        const target = e.detail?.target;
        if (target && target.classList?.contains('sidebar-item')) return;
        const indicator = document.querySelector('.tts-tab-loading');
        if (indicator) {
            indicator.classList.remove('is-long');
            longTimer = setTimeout(() => indicator.classList.add('is-long'), 1500);
        }
    });
    document.addEventListener('htmx:afterRequest', () => clearTimeout(longTimer));
})();

/* P2-7 P-03: Skeleton layered UX: after 5s, switch to real % */
(function uxFixSkeletonLayers() {
    'use strict';
    const skeleton = document.getElementById('page-initial-skeleton');
    if (!skeleton) return;
    let layer0Timeout, layer1Interval, layer2Timeout;
    layer0Timeout = setTimeout(() => {
        skeleton.classList.remove('beauty-skeleton-layer-0');
        skeleton.classList.add('beauty-skeleton-layer-1');
        let pct = 15;
        layer1Interval = setInterval(() => {
            pct = Math.min(95, pct + Math.floor(Math.random() * 4 + 1));
            skeleton.innerHTML = `<div class="skeleton-percent">正在加载引擎权重… (${pct}%)</div>`;
        }, 1200);
    }, 5000);
    layer2Timeout = setTimeout(() => {
        skeleton.classList.add('beauty-skeleton-layer-2');
    }, 20000);
    window.addEventListener('TTS_APP_READY', () => {
        clearTimeout(layer0Timeout);
        clearInterval(layer1Interval);
        clearTimeout(layer2Timeout);
    }, { once: true });
    setTimeout(() => skeleton.style.display = 'none', 35000);
})();

/* ============================================================================
   FINAL ROUND FALLBACKS — 三个修复的 JS 兜底，不依赖 CSS 缓存生效
   ============================================================================ */

/* P0-4 L-02 FIX: JS 兜底手动给所有 settings 锚点设置 scroll-margin-top */
(function uxFixAnchorOffset_JSFallback() {
    'use strict';
    function apply() {
        const ids = ['settings-hardware','settings-gpu','settings-advanced','settings-defaults','settings-general'];
        ids.forEach(id => {
            const el = document.getElementById(id);
            if (el) { el.style.scrollMarginTop = '80px'; }
        });
        /* 通配兜底：任何 id 以 settings- 开头的 DOM 节点 */
        document.querySelectorAll('[id^="settings-"]').forEach(el => {
            if (!el.style.scrollMarginTop || el.style.scrollMarginTop !== '80px') {
                el.style.scrollMarginTop = '80px';
            }
        });
        /* 全局通用锚点：有 href="#xxx" 的 a 标签对应 id 元素 */
        document.querySelectorAll('a[href^="#"]').forEach(a => {
            const id = a.getAttribute('href').slice(1);
            if (!id) return;
            const target = document.getElementById(id);
            if (target && target.getBoundingClientRect().top > 100) {
                target.style.scrollMarginTop = target.style.scrollMarginTop || '80px';
            }
        });
    }
    apply();
    document.addEventListener('DOMContentLoaded', apply);
    document.addEventListener('htmx:afterSwap', apply);
    document.addEventListener('htmx:load', apply);
    /* 重复兜底三次，覆盖 HTMX 各种时序 */
    [400, 1200, 3000].forEach(ms => setTimeout(apply, ms));
})();

/* P3-4 / 全局兼容性：保证 showToast 全局可用 */
if (typeof window.showToast !== 'function') {
    window.showToast = function(msg, type) {
        try {
            if (window.Toast && typeof Toast.show === 'function') return Toast.show(msg, type || 'info');
        } catch(e) {}
        console.log('[TOAST '+(type||'info').toUpperCase()+']', msg);
    };
}

