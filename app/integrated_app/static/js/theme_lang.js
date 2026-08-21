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
    set: window.setLang,
    init: initLanguage
};
})();
