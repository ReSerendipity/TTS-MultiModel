/* ===== data-icon → inline SVG converter (Lucide-style) ===== */
(function() {
var ICONS = {
    'pen-line': '<path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/><path d="m15 5 4 4"/>',
    'volume-2': '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>',
    'copy': '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
    'sliders': '<line x1="4" x2="4" y1="21" y2="14"/><line x1="4" x2="4" y1="10" y2="3"/><line x1="12" x2="12" y1="21" y2="12"/><line x1="12" x2="12" y1="8" y2="3"/><line x1="20" x2="20" y1="21" y2="16"/><line x1="20" x2="20" y1="12" y2="3"/><line x1="2" x2="6" y1="14" y2="14"/><line x1="10" x2="14" y1="8" y2="8"/><line x1="18" x2="22" y1="16" y2="16"/>',
    'message-circle': '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/>',
    'zap': '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
    'history': '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l4 2"/>',
    'mic': '<path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" x2="12" y1="19" y2="22"/>',
    'settings': '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>'
};

function renderIcons(root) {
    var els = (root || document).querySelectorAll('[data-icon]');
    els.forEach(function(el) {
        var name = el.getAttribute('data-icon');
        var paths = ICONS[name];
        if (!paths) return;
        var size = el.getAttribute('data-icon-size') || '20';
        var svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
        svg.setAttribute('width', size);
        svg.setAttribute('height', size);
        svg.setAttribute('viewBox', '0 0 24 24');
        svg.setAttribute('fill', 'none');
        svg.setAttribute('stroke', 'currentColor');
        svg.setAttribute('stroke-width', '2');
        svg.setAttribute('stroke-linecap', 'round');
        svg.setAttribute('stroke-linejoin', 'round');
        svg.classList.add('icon-svg');
    svg.innerHTML = paths;
    el.removeAttribute('data-icon');
    el.innerHTML = '';
    el.classList.add('icon-container');
    el.appendChild(svg);
    });
}

/* Wait for DOM ready then initial render */
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function() { renderIcons(document); });
} else {
    renderIcons(document);
}

/* Re-render after HTMX content swap - backup for edge cases */
document.body.addEventListener('htmx:afterSettle', function() {
    renderIcons(document);
});

/* Use MutationObserver for any dynamic content - only scan changed subtrees */
if (window.MutationObserver) {
    var _iconObserver = new MutationObserver(function(mutations) {
        for (var i = 0; i < mutations.length; i++) {
            var mutation = mutations[i];
            if (mutation.addedNodes && mutation.addedNodes.length > 0) {
                for (var j = 0; j < mutation.addedNodes.length; j++) {
                    var node = mutation.addedNodes[j];
                    if (node.nodeType === 1) { // Element node
                        renderIcons(node); // Only scan the changed subtree
                    }
                }
            }
        }
    });
    _iconObserver.observe(document.body, { childList: true, subtree: true });
}

// Expose module API
// @deprecated 使用 TTSApp.icons 替代，此 window 挂载点将在未来版本移除
window.IconConverter = {
    render: renderIcons
};
})();
