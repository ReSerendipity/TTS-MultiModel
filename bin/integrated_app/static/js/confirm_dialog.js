/**
 * Unified Confirm Dialog Module
 * Replaces the duplicated showConfirmDialog functions across templates.
 *
 * Usage:
 *   ConfirmDialog.show('Are you sure?', onConfirmCallback);
 */
var ConfirmDialog = (function() {
    'use strict';

    function _t(key, fallback) {
        if (window.I18N && window.I18N[key]) return window.I18N[key];
        return fallback || key;
    }

    function show(message, onConfirm, options) {
        options = options || {};

        var overlay = document.createElement('div');
        overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:var(--overlay-bg,rgba(0,0,0,0.5));z-index:10000;display:flex;align-items:center;justify-content:center;';

        var dialog = document.createElement('div');
        dialog.style.cssText = 'background:var(--bg-secondary);border-radius:12px;padding:24px;max-width:400px;width:90%;box-shadow:0 20px 60px rgba(0,0,0,0.3);';

        var msgEl = document.createElement('p');
        msgEl.style.cssText = 'color:var(--text-primary);margin-bottom:20px;font-size:14px;';
        msgEl.textContent = message;

        var btnContainer = document.createElement('div');
        btnContainer.style.cssText = 'display:flex;gap:12px;justify-content:center;';

        var cancelBtn = document.createElement('button');
        cancelBtn.textContent = _t('cancel', '取消');
        cancelBtn.style.cssText = 'padding:8px 20px;border-radius:8px;border:1px solid var(--border-subtle);background:var(--bg-tertiary);color:var(--text-primary);cursor:pointer;';

        var okBtn = document.createElement('button');
        okBtn.textContent = _t('confirm', '确认');
        var successColor = getComputedStyle(document.documentElement).getPropertyValue('--accent-success').trim() || '#10B981';
        okBtn.style.cssText = 'padding:8px 20px;border-radius:8px;border:none;background:' + successColor + ';color:var(--text-on-accent,#fff);cursor:pointer;';

        if (options.cancelId) cancelBtn.id = options.cancelId;
        if (options.okId) okBtn.id = options.okId;

        btnContainer.appendChild(cancelBtn);
        btnContainer.appendChild(okBtn);
        dialog.appendChild(msgEl);
        dialog.appendChild(btnContainer);
        overlay.appendChild(dialog);
        document.body.appendChild(overlay);

        cancelBtn.onclick = function() { document.body.removeChild(overlay); };
        okBtn.onclick = function() { document.body.removeChild(overlay); if (onConfirm) onConfirm(); };
        overlay.onclick = function(e) { if (e.target === overlay) document.body.removeChild(overlay); };
    }

    return { show: show };
})();
