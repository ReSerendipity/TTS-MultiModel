/* ===== Theme & Language Module ===== */
(function() {
// Restore theme from localStorage before render to prevent flash
var savedTheme = localStorage.getItem('app_theme');
var theme = (savedTheme === 'light' || savedTheme === 'dark') ? savedTheme : 'light';
document.documentElement.classList.add(theme);
document.documentElement.style.colorScheme = theme;

// Restore language from localStorage and apply to html element
var savedLang = localStorage.getItem('app_lang');
if (savedLang) {
    document.documentElement.setAttribute('lang', savedLang);
}

// ============================================================
// Theme Toggle (legacy alias - call switchTheme)
// ============================================================
window.switchTheme = function() {
    window.toggleTheme();
};

// ============================================================
// Theme Toggle
// ============================================================
// @deprecated 使用 TTSApp.theme.toggle 替代，此 window 挂载点将在未来版本移除
window.toggleTheme = function() {
    var html = document.documentElement;
    var isDark = html.classList.contains('dark');
    var newTheme = isDark ? 'light' : 'dark';

    // Add transition class for smooth theme switch
    html.classList.add('theme-transitioning');

    html.classList.remove('dark', 'light');
    html.classList.add(newTheme);
    html.style.colorScheme = newTheme;
    localStorage.setItem('app_theme', newTheme);

    // Update icon visibility via CSS class on html element
    // CSS handles: .dark #theme-icon-sun { display: block } etc.
    // No inline style needed

    // Remove transition class after animation completes
    setTimeout(function() {
        html.classList.remove('theme-transitioning');
    }, 350);
};

// Theme initialization - only update icons (theme class already set by IIFE above)
function initTheme() {
    var html = document.documentElement;
    var isDark = html.classList.contains('dark');
    // Icon visibility is now CSS-driven via .dark/.light on html element
    // No inline style manipulation needed
}

// ============================================================
// Language Toggle
// ============================================================
// Supported languages: zh-CN, zh-TW, en, ja, ko
window._supportedLangs = ['zh-CN', 'zh-TW', 'en', 'ja', 'ko'];
window._langLabels = { 'zh-CN': '中', 'zh-TW': '繁', 'en': 'EN', 'ja': '日', 'ko': '한' };

// @deprecated 使用 TTSApp.lang.toggle 替代，此 window 挂载点将在未来版本移除
window.toggleLang = function() {
    // Get current language from URL param, localStorage, cookie, or html lang attribute
    var urlParams = new URLSearchParams(window.location.search);
    var currentLang = urlParams.get('lang') || localStorage.getItem('app_lang') || document.cookie.match(/lang=([^;]+)/)?.[1] || document.documentElement.getAttribute('lang') || 'zh-CN';

    // Normalize language code - preserve zh-TW
    if (currentLang === 'zh-TW' || currentLang === 'zh-Hant' || currentLang === 'zh-tw') {
        currentLang = 'zh-TW';
    } else if (currentLang.startsWith('zh')) {
        currentLang = 'zh-CN';
    }

    // Cycle to next language
    var currentIndex = window._supportedLangs.indexOf(currentLang);
    if (currentIndex === -1) currentIndex = 0;
    var newLang = window._supportedLangs[(currentIndex + 1) % window._supportedLangs.length];

    // Save to localStorage (separate from theme)
    localStorage.setItem('app_lang', newLang);

    // Update UI
    document.documentElement.setAttribute('lang', newLang);
    var label = document.getElementById('lang-current-label');
    if (label) label.textContent = window._langLabels[newLang];
    var topLabel = document.getElementById('top-lang-current-label');
    if (topLabel) topLabel.textContent = window._langLabels[newLang];

    // Set cookie for server-side compatibility
    document.cookie = 'lang=' + newLang + '; path=/; max-age=31536000';

    // Reload page with new language - theme state preserved in localStorage
    var url = new URL(window.location.href);
    url.searchParams.set('lang', newLang);
    window.location.href = url.toString();
};

// ============================================================
// Language Menu — 直选下拉（B4：循环切换无法直达目标语言）
// ============================================================
window._langMenuEl = null;
window._langMenuClose = function () {
    var m = window._langMenuEl;
    if (m) { m.remove(); window._langMenuEl = null; }
    document.removeEventListener('click', window._langMenuClose);
};
window.openLangMenu = function (ev, anchorEl) {
    ev.stopPropagation();
    if (window._langMenuEl) { window._langMenuClose(); return; }
    var urlParams = new URLSearchParams(window.location.search);
    var cur = urlParams.get('lang') || localStorage.getItem('app_lang') || 'zh-CN';
    if (cur === 'zh-Hant' || cur === 'zh-tw') cur = 'zh-TW';
    else if (cur.startsWith('zh')) cur = 'zh-CN';
    var menu = document.createElement('div');
    menu.className = 'lang-menu';
    // 关键样式内联：不依赖样式表级联——部分 WebView2 环境下 JS 注入的规则不参与
    // 级联，菜单会按普通流布局落到文档末尾（表现为点击后无任何可见变化）
    menu.style.cssText = 'position:fixed;z-index:10000;min-width:150px;background:rgba(24,20,34,.97);'
        + 'border:1px solid rgba(139,92,246,.4);border-radius:10px;padding:6px;'
        + 'box-shadow:0 8px 24px rgba(0,0,0,.5);';
    window._langMenuEl = menu;
    window._supportedLangs.forEach(function (lg) {
        var item = document.createElement('button');
        item.type = 'button';
        item.className = 'lang-menu-item' + (lg === cur ? ' current' : '');
        item.style.cssText = 'display:flex;align-items:center;justify-content:space-between;gap:16px;width:100%;'
            + 'padding:8px 12px;border:none;background:transparent;color:rgba(237,233,250,.92);'
            + 'font-size:13px;border-radius:6px;cursor:pointer;text-align:left;'
            + (lg === cur ? 'background:rgba(139,92,246,.28);font-weight:600;' : '');
        item.innerHTML = '<span>' + (window._langLabels[lg] || lg) + '</span><span class="lang-menu-code">' + lg + '</span>';
        item.onmouseenter = function () { item.style.background = 'rgba(139,92,246,.18)'; };
        item.onmouseleave = function () { item.style.background = lg === cur ? 'rgba(139,92,246,.28)' : 'transparent'; };
        item.onclick = function (e) {
            e.stopPropagation();
            window._langMenuClose();
            window.setLang(lg);
        };
        menu.appendChild(item);
    });
    // 先隐藏挂载，量出「fixed 坐标 → 视口坐标」的缩放/偏移（宿主带 zoom/transform
    // 时两套坐标不同），再按锚点换算目标位置；下方放不下则向上弹（侧栏底栏按钮场景）
    menu.style.top = '0px';
    menu.style.left = '0px';
    menu.style.visibility = 'hidden';
    document.body.appendChild(menu);
    var probe = menu.getBoundingClientRect();
    var scale = (menu.offsetWidth > 0 && probe.width > 0) ? (probe.width / menu.offsetWidth) : 1;
    if (!isFinite(scale) || scale <= 0) scale = 1;
    var ox = probe.left, oy = probe.top;
    var r = anchorEl.getBoundingClientRect();
    var menuH = menu.offsetHeight * scale;
    var menuW = menu.offsetWidth * scale;
    var vy = r.bottom + 6;
    if (vy + menuH > window.innerHeight - 8) vy = r.top - menuH - 6;
    if (vy < 8) vy = 8;
    var vx = Math.max(8, Math.min(r.left, window.innerWidth - menuW - 8));
    menu.style.top = ((vy - oy) / scale) + 'px';
    menu.style.left = ((vx - ox) / scale) + 'px';
    menu.style.visibility = '';
    setTimeout(function () { document.addEventListener('click', window._langMenuClose); }, 0);
};

window.setLang = function(lang) {
    if (!window._supportedLangs.includes(lang)) return;

    // Save to localStorage (separate from theme)
    localStorage.setItem('app_lang', lang);

    // Update UI
    document.documentElement.setAttribute('lang', lang);
    var btn = document.getElementById('lang-toggle-btn');
    var label = document.getElementById('lang-current-label');
    if (btn) btn.textContent = window._langLabels[lang];
    if (label) label.textContent = window._langLabels[lang];
    var topLabel = document.getElementById('top-lang-current-label');
    if (topLabel) topLabel.textContent = window._langLabels[lang];

    // Set cookie for server-side compatibility
    document.cookie = 'lang=' + lang + '; path=/; max-age=31536000';

    // Reload page with new language - theme state preserved in localStorage
    var url = new URL(window.location.href);
    url.searchParams.set('lang', lang);
    window.location.href = url.toString();
};

// Language initialization from localStorage
function initLanguage() {
    var savedLang = localStorage.getItem('app_lang');
    if (savedLang && window._supportedLangs.includes(savedLang)) {
        document.documentElement.setAttribute('lang', savedLang);
        var label = document.getElementById('lang-current-label');
        if (label) label.textContent = window._langLabels[savedLang];
        var topLabel = document.getElementById('top-lang-current-label');
        if (topLabel) topLabel.textContent = window._langLabels[savedLang];
    }
}

// Expose module API
window.TTSTheme = {
    toggle: window.toggleTheme,
    switch: window.switchTheme,
    init: initTheme
};

// @deprecated 使用 TTSApp.lang 替代，此 window 挂载点将在未来版本移除
window.TTSLang = {
    toggle: window.toggleLang,
    openMenu: window.openLangMenu,
    set: window.setLang,
    init: initLanguage
};
})();

// Language menu styles (injected once)
(function () {
    var css = '.lang-menu{position:fixed;z-index:10000;min-width:150px;background:rgba(24,20,34,.97);'
        + 'border:1px solid rgba(139,92,246,.4);border-radius:10px;padding:6px;box-shadow:0 8px 24px rgba(0,0,0,.5);}'
        + '.lang-menu-item{display:flex;align-items:center;justify-content:space-between;gap:16px;width:100%;'
        + 'padding:8px 12px;border:none;background:transparent;color:rgba(237,233,250,.92);font-size:13px;'
        + 'border-radius:6px;cursor:pointer;text-align:left;}'
        + '.lang-menu-item:hover{background:rgba(139,92,246,.18);}'
        + '.lang-menu-item.current{background:rgba(139,92,246,.28);font-weight:600;}'
        + '.lang-menu-code{opacity:.55;font-size:11px;}';
    var st = document.createElement('style');
    st.textContent = css;
    document.head.appendChild(st);
})();
