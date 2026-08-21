/**
 * Alpine.js global stores for TTS MultiModel
 * Registers reactive stores for theme, language, and sidebar collapse state.
 */
document.addEventListener('alpine:init', function() {
    'use strict';

    var savedTheme = localStorage.getItem('app_theme') || 'light';
    var savedLang = localStorage.getItem('app_lang') || 'zh-CN';
    var savedCollapsed = localStorage.getItem('app_sidebar_collapsed') === 'true';

    Alpine.store('theme', {
        value: savedTheme,
        isDark: function() { return this.value === 'dark'; },
        toggle: function() {
            this.value = this.value === 'dark' ? 'light' : 'dark';
            localStorage.setItem('app_theme', this.value);
            document.documentElement.classList.remove('dark', 'light');
            document.documentElement.classList.add(this.value);
            document.documentElement.style.colorScheme = this.value;
        },
        set: function(theme) {
            if (theme !== 'dark' && theme !== 'light') return;
            this.value = theme;
            localStorage.setItem('app_theme', theme);
            document.documentElement.classList.remove('dark', 'light');
            document.documentElement.classList.add(theme);
            document.documentElement.style.colorScheme = theme;
        }
    });

    Alpine.store('lang', {
        value: savedLang,
        set: function(lang) {
            this.value = lang;
            localStorage.setItem('app_lang', lang);
            document.documentElement.setAttribute('lang', lang);
        }
    });

    Alpine.store('sidebar', {
        collapsed: savedCollapsed,
        toggle: function() {
            this.collapsed = !this.collapsed;
            localStorage.setItem('app_sidebar_collapsed', this.collapsed ? 'true' : 'false');
        },
        setCollapsed: function(collapsed) {
            this.collapsed = !!collapsed;
            localStorage.setItem('app_sidebar_collapsed', this.collapsed ? 'true' : 'false');
        }
    });
});
