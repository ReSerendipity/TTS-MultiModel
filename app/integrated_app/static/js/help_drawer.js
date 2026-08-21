/* ===== Help Drawer Module ===== */
(function() {
// ============================================================
// Focus Trap for modal-like popups
// ============================================================
var helpTriggerElement = null;
var shortcutTriggerElement = null;
var healthTriggerElement = null;
var _helpSearchInjected = false;

var _helpSectionTabMap = [
    { index: 0, tabs: ['voice_design'] },
    { index: 1, tabs: ['voice_clone'] },
    { index: 2, tabs: ['ultimate_clone'] },
    { index: 3, tabs: ['indextts2'] },
    { index: 4, tabs: ['settings'] },
    { index: 5, tabs: ['*'] }
];

function _detectCurrentTab() {
    var currentTab = document.body.dataset.currentTab
        || (document.querySelector('.sidebar-item.active') && document.querySelector('.sidebar-item.active').dataset && document.querySelector('.sidebar-item.active').dataset.tab)
        || (location.search.match(/tab=([^&]+)/) || [])[1]
        || 'voice_design';
    return currentTab;
}

function _ensureHelpSearchUI(drawer) {
    if (_helpSearchInjected) return;
    var popup = drawer.querySelector('.help-popup');
    if (!popup) return;

    var searchWrap = document.createElement('div');
    searchWrap.className = 'help-search-wrap';
    searchWrap.style.cssText = 'padding:12px 16px;border-bottom:1px solid var(--border-subtle,rgba(255,255,255,0.08));';
    var searchInput = document.createElement('input');
    searchInput.type = 'text';
    searchInput.className = 'help-search';
    searchInput.placeholder = '搜索帮助，如：种子、采样率...';
    searchInput.style.cssText = 'width:100%;padding:8px 12px;border-radius:6px;border:1px solid var(--border-muted,rgba(255,255,255,0.12));background:var(--bg-input,rgba(255,255,255,0.04));color:var(--text-primary,#fff);font-size:13px;outline:none;box-sizing:border-box;';
    searchWrap.appendChild(searchInput);

    popup.insertBefore(searchWrap, popup.firstChild);

    searchInput.addEventListener('input', function() {
        _handleHelpSearch(popup, this.value.trim());
    });

    var footerHint = document.createElement('div');
    footerHint.className = 'help-footer-hint';
    footerHint.style.cssText = 'padding:10px 16px;border-top:1px solid var(--border-subtle,rgba(255,255,255,0.08));font-size:11px;color:var(--text-muted,#888);text-align:center;';
    footerHint.textContent = '提示：按 Ctrl+? 随时打开/关闭帮助';
    popup.appendChild(footerHint);

    _helpSearchInjected = true;
}

function _handleHelpSearch(popup, keyword) {
    var sections = popup.querySelectorAll('.help-section');
    sections.forEach(function(section) {
        section.classList.remove('help-section--collapsed', 'help-section--highlight');
    });
    if (!keyword) {
        var currentTab = _detectCurrentTab();
        sections.forEach(function(section, idx) {
            _applySectionTabScope(section, idx, currentTab);
        });
        return;
    }
    var kwLower = keyword.toLowerCase();
    sections.forEach(function(section) {
        var steps = section.querySelectorAll('.help-step, li');
        var hasMatch = false;
        steps.forEach(function(step) {
            var originalText = step.getAttribute('data-help-original') || step.textContent;
            if (!step.getAttribute('data-help-original')) {
                step.setAttribute('data-help-original', originalText);
            }
            var textLower = originalText.toLowerCase();
            var match = textLower.indexOf(kwLower);
            if (match >= 0) {
                hasMatch = true;
                step.style.display = '';
                var before = originalText.slice(0, match);
                var matched = originalText.slice(match, match + keyword.length);
                var after = originalText.slice(match + keyword.length);
                step.innerHTML = before + '<mark style="background:var(--accent-info,#3B82F6);color:#fff;padding:0 2px;border-radius:2px;">' + matched + '</mark>' + after;
            } else {
                step.style.display = 'none';
                step.innerHTML = originalText;
            }
        });
        section.style.display = hasMatch ? '' : 'none';
    });
}

function _applySectionTabScope(section, idx, currentTab) {
    section.style.display = '';
    section.classList.remove('help-section--highlight', 'help-section--collapsed');
    var mapping = _helpSectionTabMap[idx];
    if (!mapping) return;
    var tabs = mapping.tabs;
    var isUniversal = tabs.indexOf('*') >= 0;
    var isMatch = isUniversal || tabs.indexOf(currentTab) >= 0;
    if (isMatch) {
        section.classList.add('help-section--highlight');
    } else {
        section.classList.add('help-section--collapsed');
        var title = section.querySelector('.help-section-title, h2, h3, .section-title, [class*="title"]');
        if (title) {
            title.style.cursor = 'pointer';
            title.onclick = function() {
                section.classList.toggle('help-section--collapsed');
            };
        }
    }
    return isMatch;
}

function getFocusableElements(element) {
    return Array.prototype.slice.call(
        element.querySelectorAll('a[href], button, textarea, input, select, [tabindex]:not([tabindex="-1"])')
    ).filter(function(el) {
        return !el.disabled && el.offsetParent !== null;
    });
}

function trapFocus(element) {
    var focusableEls = getFocusableElements(element);
    if (focusableEls.length === 0) return;
    var firstFocusableEl = focusableEls[0];
    var lastFocusableEl = focusableEls[focusableEls.length - 1];

    element.addEventListener('keydown', function(e) {
        if (e.key !== 'Tab') return;
        focusableEls = getFocusableElements(element);
        if (focusableEls.length === 0) return;
        firstFocusableEl = focusableEls[0];
        lastFocusableEl = focusableEls[focusableEls.length - 1];
        if (e.shiftKey) {
            if (document.activeElement === firstFocusableEl) {
                lastFocusableEl.focus();
                e.preventDefault();
            }
        } else {
            if (document.activeElement === lastFocusableEl) {
                firstFocusableEl.focus();
                e.preventDefault();
            }
        }
    });
}

// ============================================================
// Help Drawer
// ============================================================
window.toggleHelpDrawer = function() {
    var drawer = document.getElementById('help-drawer');
    var overlay = document.getElementById('help-popup-overlay');
    if (!drawer) return;
    var isVisible = drawer.classList.contains('visible');
    drawer.classList.toggle('visible', !isVisible);
    if (overlay) overlay.classList.toggle('visible', !isVisible);

    if (!isVisible) {
        helpTriggerElement = document.activeElement;
        _ensureHelpSearchUI(drawer);
        var currentTab = _detectCurrentTab();
        var popup = drawer.querySelector('.help-popup');
        var highlightSection = null;
        if (popup) {
            var sections = popup.querySelectorAll('.help-section');
            sections.forEach(function(section, idx) {
                var mapping = _helpSectionTabMap[idx];
                var tabs = mapping ? mapping.tabs : ['*'];
                var isUniversal = tabs.indexOf('*') >= 0;
                var isMatch = isUniversal || tabs.indexOf(currentTab) >= 0;
                section.style.display = '';
                section.classList.remove('help-section--highlight', 'help-section--collapsed');
                if (isMatch) {
                    section.classList.add('help-section--highlight');
                    if (!isUniversal && !highlightSection) highlightSection = section;
                } else {
                    section.classList.add('help-section--collapsed');
                    var title = section.querySelector('.help-section-title, h2, h3, .section-title, [class*="title"]');
                    if (title) {
                        title.style.cursor = 'pointer';
                        title.onclick = function() {
                            section.classList.toggle('help-section--collapsed');
                        };
                    }
                }
            });
            if (highlightSection) {
                setTimeout(function() {
                    highlightSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
                }, 60);
            }
            var searchInput = popup.querySelector('.help-search');
            if (searchInput) searchInput.value = '';
            _handleHelpSearch(popup, '');
        }
        trapFocus(drawer);
        var focusTarget = drawer.querySelector('.help-search') || drawer.querySelector('.help-popup-close');
        if (focusTarget) focusTarget.focus();
    } else {
        if (helpTriggerElement) {
            helpTriggerElement.focus();
            helpTriggerElement = null;
        }
    }
};

window.closeHelpDrawer = function() {
    var drawer = document.getElementById('help-drawer');
    var overlay = document.getElementById('help-popup-overlay');
    if (drawer) drawer.classList.remove('visible');
    if (overlay) overlay.classList.remove('visible');
    // Return focus to trigger element
    if (helpTriggerElement) {
        helpTriggerElement.focus();
        helpTriggerElement = null;
    }
};

// Close help drawer on Escape key when it is open
document.addEventListener('keydown', function(e) {
    if (e.key !== 'Escape') return;
    var drawer = document.getElementById('help-drawer');
    if (drawer && drawer.classList.contains('visible')) {
        closeHelpDrawer();
    }
});

// Shortcut Help Panel functions
window.toggleShortcutHelp = function() {
    var overlay = document.getElementById('shortcut-help-overlay');
    if (!overlay) return;
    if (overlay.classList.contains('visible')) {
        hideShortcutHelp();
    } else {
        showShortcutHelp();
    }
};

window.showShortcutHelp = function() {
    var overlay = document.getElementById('shortcut-help-overlay');
    var body = document.getElementById('shortcut-help-body');
    if (!overlay || !body) return;

    // Store trigger element for focus return
    shortcutTriggerElement = document.activeElement;

    // Build content from keyboardManager if available
    if (window.keyboardManager) {
        var allShortcuts = window.keyboardManager.getShortcuts();
        var categories = {};
        var categoryLabels = {
            general: window.I18N["tools_section"] || 'Tools',
            navigation: window.I18N["nav_section"] || 'Navigation',
            editor: window.I18N["input_settings"] || 'Editor'
        };
        allShortcuts.forEach(function(s) {
            if (!s.description) return;
            var cat = s.category || 'general';
            if (!categories[cat]) categories[cat] = [];
            categories[cat].push(s);
        });

        var html = '';
        Object.keys(categories).forEach(function(cat) {
            html += '<div class="shortcut-category">';
            html += '<div class="shortcut-category-title">' + (categoryLabels[cat] || cat) + '</div>';
            categories[cat].forEach(function(s) {
                var keysHtml = '';
                var displayKeys = [];
                s.keys.forEach(function(k) {
                    if (k === 'Control') displayKeys.push('Ctrl');
                    else if (k === 'Shift') displayKeys.push('Shift');
                    else if (k === 'Alt') displayKeys.push('Alt');
                    else if (k === 'Meta') displayKeys.push('Meta');
                    else displayKeys.push(k);
                });
                displayKeys.forEach(function(dk, idx) {
                    keysHtml += '<span class="shortcut-key">' + dk + '</span>';
                    if (idx < displayKeys.length - 1) keysHtml += '<span class="shortcut-separator">+</span>';
                });
                html += '<div class="shortcut-row">';
                html += '<span class="shortcut-desc">' + s.description + '</span>';
                html += '<div class="shortcut-keys">' + keysHtml + '</div>';
                html += '</div>';
            });
            html += '</div>';
        });

        // Add a tip at the bottom
        html += '<div style="margin-top:16px;padding-top:12px;border-top:1px solid var(--border-subtle,rgba(255,255,255,0.06));font-size:11px;color:var(--text-muted);text-align:center;">';
        html += (window.I18N["shortcut_hint"] || 'Tip: Most shortcuts are disabled while typing in text boxes to avoid interference');
        html += '</div>';

        body.innerHTML = html;
    }

    overlay.classList.add('visible');
    // Focus first focusable element in the panel
    var firstFocusable = overlay.querySelector('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
    if (firstFocusable) firstFocusable.focus();
};

window.hideShortcutHelp = function(e) {
    if (e && e.target !== e.currentTarget) return;
    var overlay = document.getElementById('shortcut-help-overlay');
    if (overlay) overlay.classList.remove('visible');
    // Return focus to trigger element
    if (shortcutTriggerElement) {
        shortcutTriggerElement.focus();
        shortcutTriggerElement = null;
    }
};

// Expose module API
// @deprecated 使用 TTSApp.help 替代，此 window 挂载点将在未来版本移除
window.HelpDrawer = {
    toggle: window.toggleHelpDrawer,
    close: window.closeHelpDrawer,
    toggleShortcut: window.toggleShortcutHelp,
    showShortcut: window.showShortcutHelp,
    hideShortcut: window.hideShortcutHelp
};
})();
