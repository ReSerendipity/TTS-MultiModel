/**
 * Unified Toast Notification Module
 * Replaces the duplicated showToast functions across templates.
 *
 * Usage:
 *   Toast.show('Operation successful', 'success');
 *   Toast.show('Something went wrong', 'error');
 *   TTSApp.toast.error('Something went wrong');
 *   window.showToast('Message', 'info');
 */
var Toast = (function() {
    'use strict';

    var _container = null;

    var _typeConfig = {
        success: {
            cssClass: 'toast-info toast-success-minor',
            duration: 2000,
            ariaLive: 'polite',
            role: 'status',
            needCloseBtn: false
        },
        info: {
            cssClass: 'toast-info toast-success-minor',
            duration: 2000,
            ariaLive: 'polite',
            role: 'status',
            needCloseBtn: false
        },
        warning: {
            cssClass: 'toast-warning',
            duration: 4000,
            ariaLive: 'assertive',
            role: 'status',
            needCloseBtn: true
        },
        error: {
            cssClass: 'toast-error toast-critical',
            duration: 8000,
            ariaLive: 'assertive',
            role: 'alert',
            needCloseBtn: true
        },
        critical: {
            cssClass: 'toast-error toast-critical',
            duration: 8000,
            ariaLive: 'assertive',
            role: 'alert',
            needCloseBtn: true
        }
    };

    function _ensureContainer() {
        var existing = document.getElementById('toast-container');
        if (existing) {
            _container = existing;
            return _container;
        }
        _container = document.createElement('div');
        _container.id = 'toast-container';
        _container.setAttribute('role', 'status');
        _container.setAttribute('aria-live', 'polite');
        _container.setAttribute('aria-atomic', 'true');
        _container.style.cssText = 'position:fixed;top:20px;right:20px;z-index:10001;display:flex;flex-direction:column;gap:12px;';
        document.body.appendChild(_container);
        return _container;
    }

    function show(message, type, duration) {
        type = type || 'info';
        var config = _typeConfig[type] || _typeConfig.info;
        duration = duration || config.duration;

        var container = _ensureContainer();
        var toast = document.createElement('div');
        toast.className = 'tts-toast ' + config.cssClass;
        toast.setAttribute('role', config.role);
        toast.setAttribute('aria-live', config.ariaLive);
        toast.setAttribute('data-autoclose', String(duration));

        toast.style.cssText = 'padding:12px 20px 12px 16px;border-radius:8px;color:var(--toast-text,#fff);font-size:14px;box-shadow:0 4px 12px rgba(0,0,0,0.15);transition:opacity 0.3s,transform 0.3s;margin-top:0;opacity:0;transform:translateY(-12px);position:relative;display:flex;align-items:center;gap:10px;box-sizing:border-box;';

        var msgSpan = document.createElement('span');
        msgSpan.style.cssText = 'flex:1;line-height:1.4;';
        msgSpan.textContent = message;
        toast.appendChild(msgSpan);

        if (config.needCloseBtn) {
            var closeBtn = document.createElement('button');
            closeBtn.className = 'toast-close-btn';
            closeBtn.setAttribute('aria-label', '关闭');
            closeBtn.type = 'button';
            closeBtn.innerHTML = '&times;';
            closeBtn.style.cssText = 'background:transparent;border:none;color:inherit;font-size:18px;line-height:1;cursor:pointer;padding:2px 6px;border-radius:4px;opacity:0.7;flex-shrink:0;';
            closeBtn.onmouseenter = function() { this.style.opacity = '1'; };
            closeBtn.onmouseleave = function() { this.style.opacity = '0.7'; };
            closeBtn.addEventListener('click', function(e) {
                e.stopPropagation();
                _removeToast(toast);
            });
            toast.appendChild(closeBtn);
        }

        container.insertBefore(toast, container.firstChild);

        requestAnimationFrame(function() {
            toast.style.opacity = '1';
            toast.style.transform = 'translateY(0)';
        });

        setTimeout(function() {
            _removeToast(toast);
        }, duration);
    }

    function _removeToast(toast) {
        if (!toast || !toast.parentNode) return;

        var removed = false;
        function removeDom() {
            if (removed) return;
            removed = true;
            if (toast.parentNode) toast.parentNode.removeChild(toast);
        }

        toast.classList.add('tts-toast-exit');
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(-8px)';

        toast.addEventListener('transitionend', function handler() {
            toast.removeEventListener('transitionend', handler);
            removeDom();
        });

        setTimeout(removeDom, 350);
    }

    var api = { show: show };

    window.showToast = function(message, type) {
        show(message, type);
    };

    window.TTSApp = window.TTSApp || {};
    window.TTSApp.toast = {
        show: show,
        error: function(message) { show(message, 'error'); },
        success: function(message) { show(message, 'success'); },
        warning: function(message) { show(message, 'warning'); },
        info: function(message) { show(message, 'info'); },
        critical: function(message) { show(message, 'critical'); }
    };

    return api;
})();
