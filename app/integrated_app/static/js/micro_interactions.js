/* ===== Micro Interactions Module ===== */
(function() {
var microObserver = null;

function addRippleEffect(e) {
    var btn = e.currentTarget;
    if (btn.disabled) return;
    var rect = btn.getBoundingClientRect();
    var x = ((e.clientX - rect.left) / rect.width * 100).toFixed(1);
    var y = ((e.clientY - rect.top) / rect.height * 100).toFixed(1);
    btn.style.setProperty('--ripple-x', x + '%');
    btn.style.setProperty('--ripple-y', y + '%');
    var size = Math.max(rect.width, rect.height);
    var ripple = document.createElement('span');
    ripple.className = 'micro-ripple-effect';
    ripple.style.width = ripple.style.height = size + 'px';
    ripple.style.left = (e.clientX - rect.left - size / 2) + 'px';
    ripple.style.top = (e.clientY - rect.top - size / 2) + 'px';
    btn.appendChild(ripple);
    setTimeout(function() { ripple.remove(); }, 600);
}

function initDragOverStates() {
    document.querySelectorAll('.file-upload-area').forEach(function(area) {
        if (area.dataset.dragInit) return;
        area.dataset.dragInit = '1';
        area.addEventListener('dragenter', function() { area.classList.add('drag-over'); });
        area.addEventListener('dragover', function(e) { e.preventDefault(); area.classList.add('drag-over'); });
        area.addEventListener('dragleave', function(e) {
            // only remove if leaving to outside of element
            if (!area.contains(e.relatedTarget)) {
                area.classList.remove('drag-over');
            }
        });
        area.addEventListener('drop', function() { area.classList.remove('drag-over'); });
    });
}

function initSlidersProgressFill() {
    document.querySelectorAll('input[type="range"]').forEach(function(slider) {
        if (slider.dataset.fillInit) return;
        // skip sliders that are volume/seek in global player (handled by their own logic)
        if (slider.closest('.global-audio-player')) return;
        slider.dataset.fillInit = '1';
        function updateFill() {
            var min = parseFloat(slider.min) || 0;
            var max = parseFloat(slider.max) || 100;
            var val = parseFloat(slider.value) || 0;
            var pct = ((val - min) / (max - min) * 100).toFixed(1);
            slider.style.setProperty('--slider-fill', pct + '%');
            slider.style.background = 'linear-gradient(to right, var(--accent-primary) 0%, var(--accent-primary) ' + pct + '%, var(--bg-tertiary) ' + pct + '%, var(--bg-tertiary) 100%)';
        }
        updateFill();
        slider.addEventListener('input', updateFill);
    });
}

function initAll() {
    document.querySelectorAll('.primary-btn.generate-btn, .tts-btn-primary.generate-btn, .primary-btn:not(.generate-btn), .tts-btn-primary:not(.generate-btn)').forEach(function(btn) {
        if (btn.dataset.microInit) return;
        btn.dataset.microInit = '1';
        btn.addEventListener('click', addRippleEffect);
    });

    document.querySelectorAll('.micro-tooltip').forEach(function(el) {
        if (el.dataset.microTooltipInit) return;
        el.dataset.microTooltipInit = '1';
    });

    initDragOverStates();
    initSlidersProgressFill();
}

function initMicroInteractions() { initAll(); }

function disconnectObserver() {
    if (microObserver) {
        microObserver.disconnect();
        microObserver = null;
    }
}

function startObserver() {
    disconnectObserver();
    microObserver = new MutationObserver(function() {
        initAll();
    });
    var target = document.getElementById('tab-content') || document.body;
    microObserver.observe(target, {
        childList: true,
        subtree: true
    });
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function() {
        initAll();
        startObserver();
    });
} else {
    initAll();
    startObserver();
}

// HTMX afterSwap 时重新初始化
document.body.addEventListener('htmx:afterSwap', function(event) {
    if (event.detail.target && event.detail.target.id === 'tab-content') {
        initAll();
    }
});

// Re-run on HTMX afterSettle for dynamically loaded content
document.body.addEventListener('htmx:afterSettle', function() {
    initAll();
});

// Expose module API via TTSApp namespace
TTSApp.micro = {
    init: initMicroInteractions,
    disconnect: disconnectObserver
};
})();
