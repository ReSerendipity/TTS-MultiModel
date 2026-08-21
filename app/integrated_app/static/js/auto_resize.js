/**
 * Auto-resize Textarea Module
 * Automatically adjusts textarea height based on content.
 */
var AutoResize = (function() {
    'use strict';

    function resize(textarea) {
        if (!textarea) return;
        textarea.style.height = 'auto';
        textarea.style.height = Math.min(textarea.scrollHeight, 400) + 'px';
    }

    function init(textareaId) {
        var textarea = document.getElementById(textareaId);
        if (!textarea) return;
        if (textarea.getAttribute('data-auto-resize-initialized') === 'true') return;
        textarea.setAttribute('data-auto-resize-initialized', 'true');
        textarea.addEventListener('input', function() { resize(textarea); });
        textarea.addEventListener('paste', function() { setTimeout(function() { resize(textarea); }, 0); });
        resize(textarea);
    }

    return { resize: resize, init: init };
})();
