/* ============================================================
   Prototype V4 Theme - Shared JS Utilities
   ------------------------------------------------------------
   Provides tab-template-level interactive behaviors for the
   prototype_v4.css component classes:
     - toggleCollapse(headerEl)         - collapse/expand sections
     - switchSubTab(buttonEl, panelId)  - sub-tab switching
     - switchMode(buttonEl, panelId)    - mode pill switching
     - switchFormat(buttonEl)           - format selector
     - switchView(buttonEl)             - card/list view toggle
     - updateCharCounter(textarea, counterId, maxChars, segChars)
     - initPvTabs()                     - initialize all .stab/.mode-pill

   These functions are global and idempotent — safe to call
   multiple times as tabs get loaded via HTMX.
   ============================================================ */

(function () {
    "use strict";

    // ===== Collapsible Sections =====
    window.toggleCollapse = function (el) {
        if (!el) return;
        el.classList.toggle('open');
        var body = el.nextElementSibling;
        if (body && body.classList.contains('collapse-body')) {
            body.classList.toggle('open');
        }
    };

    // ===== Sub-tab Switching (for .stabs / .stab / .tab-panel) =====
    window.switchSubTab = function (el, panelId) {
        if (!el) return;
        var parent = el.closest('.card') || el.closest('.pv-theme');
        if (!parent) return;
        var tabs = el.parentElement.querySelectorAll('.stab');
        tabs.forEach(function (t) { t.classList.remove('active'); });
        el.classList.add('active');
        var allPanels = parent.querySelectorAll('.tab-panel');
        allPanels.forEach(function (p) {
            p.classList.remove('active');
            p.style.display = 'none';
        });
        var target = document.getElementById(panelId);
        if (target) {
            target.classList.add('active');
            target.style.display = '';
        }
    };

    // ===== Mode Pill Switching (for .mode-pills / .mode-pill / .mode-panel) =====
    window.switchMode = function (el, panelId) {
        if (!el) return;
        var container = el.closest('.card') || el.closest('.pv-theme');
        if (!container) return;
        var pills = el.parentElement.querySelectorAll('.mode-pill');
        pills.forEach(function (p) { p.classList.remove('active'); });
        el.classList.add('active');
        var panels = container.querySelectorAll('.mode-panel');
        panels.forEach(function (p) {
            p.classList.remove('active');
            p.style.display = 'none';
        });
        var target = document.getElementById(panelId);
        if (target) {
            target.classList.add('active');
            target.style.display = '';
        }
    };

    // ===== Format Selector =====
    window.switchFormat = function (el) {
        if (!el) return;
        var siblings = el.parentElement.querySelectorAll('.format-btn');
        siblings.forEach(function (b) { b.classList.remove('active'); });
        el.classList.add('active');
    };

    // ===== View Toggle (Card / List) =====
    window.switchView = function (el, cardSelector, listSelector) {
        if (!el) return;
        var siblings = el.parentElement.querySelectorAll('.view-btn');
        siblings.forEach(function (b) { b.classList.remove('active'); });
        el.classList.add('active');
        var isCard = el.dataset.view === 'card';
        var card = document.querySelector(cardSelector);
        var list = document.querySelector(listSelector);
        if (card) card.style.display = isCard ? '' : 'none';
        if (list) list.style.display = isCard ? 'none' : '';
    };

    // ===== Tag Toggle (for .tag pills) =====
    window.toggleTag = function (el) {
        if (!el) return;
        // Single-select within a .tags container
        var siblings = el.parentElement.querySelectorAll('.tag');
        siblings.forEach(function (t) { t.classList.remove('active'); });
        el.classList.add('active');
    };

    // ===== Emotion Pill Toggle =====
    window.toggleEmotion = function (el) {
        if (!el) return;
        var siblings = el.parentElement.querySelectorAll('.emotion-pill');
        siblings.forEach(function (t) { t.classList.remove('active'); });
        el.classList.add('active');
    };

    // ===== Toggle Switch (for .toggle elements) =====
    window.toggleSwitch = function (el) {
        if (!el) return;
        el.classList.toggle('on');
        // Dispatch change event for listeners
        var event = new Event('change', { bubbles: true });
        el.dispatchEvent(event);
    };

    // ===== Character Counter =====
    window.updateCharCounter = function (textarea, counterId, maxChars, segChars) {
        var counter = document.getElementById(counterId);
        if (!counter || !textarea) return;
        var len = textarea.value.length;
        var seg = segChars || 200;
        var max = maxChars || 8192;
        if (len > 0) {
            var segCount = Math.ceil(len / seg);
            counter.textContent = len + '/' + max + ' | ' + segCount + ' ' + (window.I18N && window.I18N['segments'] || '段');
        } else {
            counter.textContent = '0/' + max + ' | ' + (window.I18N && window.I18N['per_segment'] || '每段') + ' ' + seg + (window.I18N && window.I18N['chars'] || '字');
        }
        // Warn on overflow
        if (len > max) {
            counter.style.color = 'var(--red, #EF4444)';
        } else if (len > max * 0.9) {
            counter.style.color = 'var(--orange, #F59E0B)';
        } else {
            counter.style.color = '';
        }
    };

    // ===== Initialize PV tabs on HTMX load =====
    // Auto-wire .stab, .mode-pill, .tag, .emotion-pill, .toggle, .collapse-header
    // that don't already have onclick handlers
    function initPvTabs() {
        // .stab buttons without onclick
        document.querySelectorAll('.pv-theme .stab:not([onclick]):not([data-pv-init])').forEach(function (stab) {
            stab.setAttribute('data-pv-init', '1');
            var panelId = stab.getAttribute('data-panel');
            if (panelId) {
                stab.addEventListener('click', function () {
                    window.switchSubTab(this, panelId);
                });
            }
        });
        // .mode-pill buttons without onclick
        document.querySelectorAll('.pv-theme .mode-pill:not([onclick]):not([data-pv-init])').forEach(function (pill) {
            pill.setAttribute('data-pv-init', '1');
            var panelId = pill.getAttribute('data-panel');
            if (panelId) {
                pill.addEventListener('click', function () {
                    window.switchMode(this, panelId);
                });
            }
        });
        // .collapse-header without onclick
        document.querySelectorAll('.pv-theme .collapse-header:not([onclick]):not([data-pv-init])').forEach(function (hdr) {
            hdr.setAttribute('data-pv-init', '1');
            hdr.addEventListener('click', function () {
                window.toggleCollapse(this);
            });
        });
        // .toggle without onclick
        document.querySelectorAll('.pv-theme .toggle:not([onclick]):not([data-pv-init])').forEach(function (tg) {
            tg.setAttribute('data-pv-init', '1');
            tg.addEventListener('click', function () {
                window.toggleSwitch(this);
            });
        });
        // .tag pills (multi-select toggle)
        document.querySelectorAll('.pv-theme .tag:not([onclick]):not([data-pv-init])').forEach(function (tag) {
            tag.setAttribute('data-pv-init', '1');
            tag.addEventListener('click', function () {
                this.classList.toggle('active');
            });
        });
        // .emotion-pill (single-select)
        document.querySelectorAll('.pv-theme .emotion-pill:not([onclick]):not([data-pv-init])').forEach(function (pill) {
            pill.setAttribute('data-pv-init', '1');
            pill.addEventListener('click', function () {
                window.toggleEmotion(this);
            });
        });
        // Range sliders with .val sibling
        document.querySelectorAll('.pv-theme input[type="range"]:not([data-pv-init])').forEach(function (slider) {
            slider.setAttribute('data-pv-init', '1');
            slider.addEventListener('input', function () {
                var valEl = this.parentElement.querySelector('.val');
                if (valEl) valEl.textContent = parseFloat(this.value).toFixed(1);
            });
        });
    }

    // Run on initial load
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initPvTabs);
    } else {
        initPvTabs();
    }

    // Re-init after HTMX swaps (tab content loads)
    document.addEventListener('htmx:afterSettle', function () {
        initPvTabs();
    });

    // Expose init for manual invocation
    window.initPvTabs = initPvTabs;
})();

/* P2-5 X-03: Settings change state visualization + reset */
(function uxFixSettingsDirtyIndicators() {
    'use strict';
    const SETTINGS_ROW_SELECTOR = '.srow';

    function boot() {
        const rows = document.querySelectorAll(SETTINGS_ROW_SELECTOR);
        if (!rows.length) return;
        rows.forEach(row => {
            const input = row.querySelector('input, select, textarea');
            if (!input) return;
            const initial = input.type === 'checkbox' ? input.checked : input.value;
            row.dataset.initialValue = JSON.stringify(initial);
            input.addEventListener('input', () => checkModified(row, input));
            input.addEventListener('change', () => checkModified(row, input));
        });
        injectActionButtons();
    }

    function checkModified(row, input) {
        const current = input.type === 'checkbox' ? input.checked : input.value;
        const initial = JSON.parse(row.dataset.initialValue);
        const isModified = (current !== initial);
        row.classList.toggle('srow--modified', isModified);
        updateCount();
    }

    function updateCount() {
        const count = document.querySelectorAll('.srow--modified').length;
        let counter = document.getElementById('settings-modified-counter');
        if (!counter) {
            const bar = document.getElementById('settings-action-bar');
            if (!bar) return;
            counter = bar.querySelector('#settings-modified-counter');
        }
        if (counter) {
            counter.textContent = count;
            counter.style.display = count > 0 ? 'inline' : 'none';
        }
    }

    function injectActionButtons() {
        const saveBtns = Array.from(document.querySelectorAll('button')).filter(b => /保存所有设置|Save/i.test(b.textContent));
        const saveBtn = saveBtns[0];
        if (!saveBtn) return;
        if (document.getElementById('settings-save-all')) return;
        const parent = saveBtn.parentNode;

        saveBtn.id = 'settings-save-all';
        saveBtn.classList.add('tts-btn-primary');
        saveBtn.parentNode.insertAdjacentHTML('beforeend', `
            <div id="settings-action-bar" class="settings-action-bar" style="display:inline-flex;align-items:center;gap:8px;margin-left:12px;">
                <span id="settings-modified-counter" style="font-variant-numeric:tabular-nums;color:var(--accent-warning);font-weight:600;font-size:var(--font-size-sm);display:none;">0</span>
                <button type="button" id="btn-reset-changes" class="tts-btn tts-btn-secondary">重置修改</button>
                <button type="button" id="btn-restore-defaults" class="tts-btn tts-btn-secondary" title="把当前页面所有设置恢复为出厂推荐值">恢复默认值</button>
            </div>
        `);

        document.getElementById('btn-reset-changes').addEventListener('click', () => {
            document.querySelectorAll('.srow').forEach(row => {
                const input = row.querySelector('input, select, textarea');
                if (!input) return;
                const initial = JSON.parse(row.dataset.initialValue);
                if (input.type === 'checkbox') input.checked = initial;
                else input.value = initial;
                input.dispatchEvent(new Event('input', { bubbles: true }));
                row.classList.remove('srow--modified');
            });
            updateCount();
        });

        document.getElementById('btn-restore-defaults').addEventListener('click', () => {
            if (!confirm('确定要把所有设置恢复为默认值吗？此操作不可撤销。')) return;
            document.querySelectorAll('.srow input, .srow select, .srow textarea').forEach(input => {
                const def = input.dataset.default ?? input.getAttribute('value') ?? (input.type === 'checkbox' ? false : '');
                if (input.type === 'checkbox') input.checked = def === 'true' || def === true;
                else input.value = def;
                input.dispatchEvent(new Event('change', { bubbles: true }));
            });
        });

        saveBtn.addEventListener('click', () => {
            setTimeout(() => {
                document.querySelectorAll('.srow--modified').forEach(el => {
                    el.classList.remove('srow--modified');
                    const input = el.querySelector('input, select, textarea');
                    if (input) el.dataset.initialValue = JSON.stringify(input.type === 'checkbox' ? input.checked : input.value);
                });
                updateCount();
            }, 600);
        });

        document.addEventListener('htmx:beforeSwap', (e) => {
            const nModified = document.querySelectorAll('.srow--modified').length;
            if (nModified > 0) {
                if (!confirm(`您有 ${nModified} 项设置修改尚未保存，离开本页将丢失修改。\n\n确定继续？`)) e.preventDefault();
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
    setInterval(() => {
        if (document.querySelector('#settings-save-all')) return;
        boot();
    }, 1000);
})();
