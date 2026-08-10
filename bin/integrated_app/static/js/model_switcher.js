/* ===== Model Switcher Module ===== */
(function() {
// ===== Shortcut API Functions =====
window.loadModel = function(engineName) {
    var engine = engineName || 'voxcpm2';
    var engineStatusText = document.getElementById('engine-status-text');
    if (engineStatusText) {
        engineStatusText.textContent = window.I18N['loading'] || '加载中...';
    }

    var csrfToken = window.getCsrfToken ? window.getCsrfToken() : '';
    var headers = { 'Content-Type': 'application/x-www-form-urlencoded' };
    if (csrfToken) headers['X-CSRF-Token'] = csrfToken;
    fetch('/api/model/load', {
        method: 'POST',
        headers: headers,
        body: 'engine=' + encodeURIComponent(engine)
    })
        .then(function(response) {
            if (!response.ok) {
                throw new Error('HTTP ' + response.status);
            }
            return response.json();
        })
        .then(function(data) {
            if (engineStatusText) {
                engineStatusText.textContent = window.I18N['ready'] || '就绪';
            }
            if (window.appState) {
                window.appState.setLoaded(true);
            }
            console.log('[KeyboardManager] Model loaded:', data);
        })
        .catch(function(error) {
            if (engineStatusText) {
                engineStatusText.textContent = (window.I18N['error'] || '错误') + ': ' + error.message;
            }
            console.error('[KeyboardManager] Failed to load model:', error);
        });
};

// @deprecated 使用 TTSApp.model.unloadModel 替代，此 window 挂载点将在未来版本移除
window.unloadModel = function() {
    var engineStatusText = document.getElementById('engine-status-text');
    if (engineStatusText) {
        engineStatusText.textContent = window.I18N['unloading'] || '卸载中...';
    }

    var csrfToken = window.getCsrfToken ? window.getCsrfToken() : '';
    var headers = csrfToken ? {'X-CSRF-Token': csrfToken} : {};
    fetch('/api/model/unload', { method: 'POST', headers: headers })
        .then(function(response) {
            if (!response.ok) {
                throw new Error('HTTP ' + response.status);
            }
            return response.json();
        })
        .then(function(data) {
            if (engineStatusText) {
                engineStatusText.textContent = window.I18N['not_loaded'] || '未加载';
            }
            if (window.appState) {
                window.appState.setLoaded(false);
            }
            console.log('[KeyboardManager] Model unloaded:', data);
        })
        .catch(function(error) {
            if (engineStatusText) {
                engineStatusText.textContent = (window.I18N['error'] || '错误') + ': ' + error.message;
            }
            console.error('[KeyboardManager] Failed to unload model:', error);
        });
};

// ============================================================
// Model Switching
// ============================================================
window._modelSwitching = false;

// Enable/disable model tab buttons during loading
window._setModelTabsDisabled = function(disabled) {
    document.querySelectorAll('.model-tab').forEach(function(tab) {
        tab.disabled = disabled;
        tab.classList.toggle('model-tab-disabled', disabled);
        if (disabled) {
            tab.setAttribute('data-original-title', tab.getAttribute('title') || '');
            tab.setAttribute('title', (window.I18N && window.I18N['model_loading_please_wait']) || '模型加载中，请稍候...');
        } else {
            var origTitle = tab.getAttribute('data-original-title');
            if (origTitle !== null) {
                tab.setAttribute('title', origTitle);
                tab.removeAttribute('data-original-title');
            }
        }
    });
};

// @deprecated 使用 TTSApp.model.switch 替代，此 window 挂载点将在未来版本移除
window.switchModel = function(modelName) {
    if (window._modelSwitching) return;
    // Lock immediately to prevent double-clicks during the 150ms fade-out delay
    window._modelSwitching = true;
    window._setModelTabsDisabled(true);

    // Fade-out transition before switching
    var tabContent = document.getElementById('tab-content');
    if (tabContent) {
        tabContent.classList.add('tab-switching');
    }

    // Wait for fade-out, then perform the switch
    setTimeout(function() {
        var tabs = document.querySelectorAll('.model-tab');
        tabs.forEach(function(tab) {
            var isActive = tab.dataset.model === modelName;
            tab.classList.toggle('active', isActive);
            tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
        });

        var sections = document.querySelectorAll('.sidebar-nav-section[data-section-model]');
        sections.forEach(function(sec) {
            var secModel = sec.dataset.sectionModel;
            var isVisible = (secModel === modelName || modelName === 'none');
            sec.classList.toggle('section-hidden', !isVisible);
            // 引擎维度已收敛到顶部栏：已加载状态仅显示功能语义；未加载（none）状态补充引擎名便于浏览
            var engName = sec.dataset.engineName;
            if (engName) {
                var labelText = sec.querySelector('.sidebar-nav-label-text');
                if (labelText && !labelText.dataset.baseLabel) {
                    labelText.dataset.baseLabel = labelText.textContent;
                }
                if (labelText && labelText.dataset.baseLabel) {
                    labelText.textContent = (modelName === 'none')
                        ? engName + ' · ' + labelText.dataset.baseLabel
                        : labelText.dataset.baseLabel;
                }
            }
        });

        var items = document.querySelectorAll('.sidebar-item[data-model]');
        items.forEach(function(item) {
            var itemModel = item.dataset.model;
            var shouldShow = (itemModel === modelName || itemModel === 'all');
            item.classList.toggle('sidebar-item-hidden', !shouldShow);
        });

        var firstVisible = document.querySelector('.sidebar-item[data-model="' + modelName + '"]:not(.sidebar-item-hidden)');
        if (firstVisible) {
            TTSApp.sidebar.activateTab(firstVisible);
        }

        // Fade-in transition after switching
        if (tabContent) {
            tabContent.classList.remove('tab-switching');
        }

        if (modelName === 'none') {
            window._modelSwitching = false;
            window._setModelTabsDisabled(false);
            window.updateEngineStatus('none', (window.I18N && window.I18N['model_none']) || 'None', '');
            if (window._unloadCurrentModel) {
                window._unloadCurrentModel();
            }
            return;
        }

        window.updateEngineStatus('loading', modelName, (window.I18N && window.I18N['switching_engine']) || 'Switching engine...');

        fetch('/api/model/status').then(function(res) { return res.json(); }).then(function(statusData) {
            var isLoaded = statusData && statusData.loaded;
            var currentEngine = statusData ? statusData.engine : null;
            var isSwitching = isLoaded && currentEngine && currentEngine !== modelName;
            var apiPath = isSwitching ? '/api/model/switch' : '/api/model/load';
            var csrfToken2 = window.getCsrfToken ? window.getCsrfToken() : '';
            var switchHeaders = { 'Content-Type': 'application/x-www-form-urlencoded' };
            if (csrfToken2) switchHeaders['X-CSRF-Token'] = csrfToken2;
            return fetch(apiPath, {
                method: 'POST',
                headers: switchHeaders,
                body: 'engine=' + encodeURIComponent(modelName)
            }).then(function(res) { return res.json(); });
        }).then(function(data) {
            window._modelSwitching = false;
            if (data.status === 'ok') {
                window.updateEngineStatus('loaded', modelName, (window.I18N && window.I18N['ready']) || 'Ready');
            } else if (data.message === '引擎已就绪，无需切换') {
                window.updateEngineStatus('loaded', modelName, (window.I18N && window.I18N['ready']) || 'Ready');
            } else {
                window.updateEngineStatus('error', modelName, data.message || (window.I18N && window.I18N['error']) || 'Error');
                setTimeout(function() {
                    var currentBtn = document.querySelector('.model-tab.active');
                    var prevModel = currentBtn ? currentBtn.dataset.model : 'none';
                    if (prevModel !== modelName) {
                        document.querySelectorAll('.model-tab').forEach(function(t) {
                            t.classList.toggle('active', t.dataset.model === prevModel);
                        });
                    }
                }, 100);
            }
        }).catch(function(err) {
            window._modelSwitching = false;
            window._setModelTabsDisabled(false);
            window.updateEngineStatus('error', modelName, (window.I18N && window.I18N['error']) || 'Error');
            console.error('Failed to switch model:', err);
        });
    }, 150);
};

window._unloadCurrentModel = function() {
    if (window._modelSwitching) return;
    window._modelSwitching = true;
    window.updateEngineStatus('loading', (window.I18N && window.I18N['model_none']) || 'None', (window.I18N && window.I18N['unloading_model']) || 'Unloading model...');
    fetch('/api/model/unload', {
        method: 'POST',
        headers: Object.assign({'Content-Type': 'application/json'}, window.getCsrfToken ? {'X-CSRF-Token': window.getCsrfToken()} : {})
    }).then(function(res) { return res.json(); }).then(function(data) {
        window._modelSwitching = false;
        if (data.status === 'ok') {
            window.updateEngineStatus('none', (window.I18N && window.I18N['model_none']) || 'None', '');
        } else {
            window.updateEngineStatus('error', (window.I18N && window.I18N['model_none']) || 'None', data.message || (window.I18N && window.I18N['error']) || 'Error');
        }
    }).catch(function(err) {
        window._modelSwitching = false;
        window.updateEngineStatus('error', (window.I18N && window.I18N['model_none']) || 'None', (window.I18N && window.I18N['error']) || 'Error');
        console.error('Failed to unload model:', err);
    });
};

// ============================================================
// Engine Status Management
// ============================================================
window.updateEngineStatus = function(status, modelName, extra) {
    var statusEl = document.getElementById('engine-status');
    var statusText = document.getElementById('engine-status-text');
    var statusIcon = document.getElementById('engine-status-icon');
    var ariaLiveEl = document.getElementById('aria-live-status');
    if (!statusEl || !statusText) return;

    // Helper to remove engine switch progress bar
    function _removeSwitchBar() {
        try {
            var bar = document.getElementById('engine-switch-progress');
            if (bar) {
                bar.style.opacity = '0';
                setTimeout(function() { if (bar.parentNode) bar.parentNode.removeChild(bar); }, 300);
            }
        } catch(e) {}
    }

    statusEl.classList.remove('loaded', 'loading', 'error');

    // SVG icons for each state (not relying on color alone)
    var iconLoaded = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>';
    var iconLoading = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>';
    var iconError = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';

    var announceText = '';

    if (status === 'none') {
        statusText.textContent = (window.I18N && window.I18N['model_none']) || 'None';
        statusText.classList.add('status-weight-bold');
        if (statusIcon) { statusIcon.classList.add('status-icon-hidden'); statusIcon.innerHTML = ''; }
        announceText = (window.I18N && window.I18N['model_none']) || 'None';
    } else if (status === 'loading') {
        statusEl.classList.add('loading');
        statusText.textContent = extra || (window.I18N && window.I18N["loading"]) || 'Loading...';
        statusText.classList.remove('status-weight-bold');
        if (statusIcon) { statusIcon.classList.remove('status-icon-hidden'); statusIcon.innerHTML = iconLoading; }
        announceText = extra || (window.I18N && window.I18N["loading"]) || 'Loading...';
    } else if (status === 'loaded') {
        statusEl.classList.add('loaded');
        statusText.textContent = modelName + ' | ' + ((window.I18N && window.I18N["ready"]) || 'Ready');
        statusText.classList.remove('status-weight-bold');
        if (statusIcon) { statusIcon.classList.remove('status-icon-hidden'); statusIcon.innerHTML = iconLoaded; }
        announceText = modelName + ' ' + ((window.I18N && window.I18N["ready"]) || 'Ready');
        // Re-enable model tabs after loading completes
        window._modelSwitching = false;
        window._setModelTabsDisabled(false);
        // Remove engine switch progress bar immediately
        _removeSwitchBar();
    } else if (status === 'error') {
        statusEl.classList.add('error');
        statusText.textContent = extra || (window.I18N && window.I18N["error"]) || 'Error';
        statusText.classList.remove('status-weight-bold');
        if (statusIcon) { statusIcon.classList.remove('status-icon-hidden'); statusIcon.innerHTML = iconError; }
        announceText = (window.I18N && window.I18N["error"]) || 'Error';
        if (extra) announceText = extra;
        // Re-enable model tabs on error
        window._modelSwitching = false;
        window._setModelTabsDisabled(false);
        // Remove engine switch progress bar immediately on error too
        _removeSwitchBar();
    }

    // Sync generate button states based on engine status
    _syncGenerateButtonsState(status);

    // Update ARIA live region for screen readers
    if (ariaLiveEl && announceText) {
        ariaLiveEl.textContent = announceText;
    }
};

// Sync generate buttons enabled/disabled state with model status
function _syncGenerateButtonsState(status) {
    var buttons = document.querySelectorAll('.btn-generate, .generate-btn');
    var isReady = (status === 'loaded') && !window._isGenerating;
    var isGenerating = window._isGenerating;
    var hintKey = 'model_not_loaded_hint';
    if (isGenerating) hintKey = 'generation_in_progress';
    else if (status === 'loading') hintKey = 'model_loading_hint';
    else if (status === 'error') hintKey = 'model_error_hint';
    var hintText = (window.I18N && window.I18N[hintKey]) || (isGenerating ? 'Generating...' : '');

    for (var i = 0; i < buttons.length; i++) {
        var btn = buttons[i];
        if (isReady) {
            btn.disabled = false;
            btn.removeAttribute('aria-disabled');
            btn.removeAttribute('data-hint');
            btn.style.cursor = '';
            btn.style.opacity = '';
            btn.style.pointerEvents = '';
            // Remove existing tooltip
            var existingTooltip = btn.querySelector('.btn-disabled-tooltip');
            if (existingTooltip && existingTooltip.parentNode) existingTooltip.parentNode.removeChild(existingTooltip);
        } else {
            btn.disabled = true;
            btn.setAttribute('aria-disabled', 'true');
            if (hintText) btn.setAttribute('data-hint', hintText);
            btn.style.cursor = isGenerating ? 'wait' : 'not-allowed';
            btn.style.opacity = isGenerating ? '0.65' : '0.55';
            btn.style.pointerEvents = 'auto';
            // Prevent form submission when disabled button clicked
            if (!btn._disabledHandlerAttached) {
                btn.addEventListener('click', function(e) {
                    if (this.disabled) {
                        e.preventDefault();
                        e.stopPropagation();
                        e.stopImmediatePropagation();
                        // Show hint as a temporary toast/tooltip
                        _showButtonHint(this);
                        return false;
                    }
                }, true); // capturing phase to block other listeners
                btn._disabledHandlerAttached = true;
            }
        }
    }
}

// Show a floating hint near the disabled button
function _showButtonHint(btn) {
    // Remove any existing hint
    var old = document.querySelector('.btn-hint-tooltip');
    if (old) old.parentNode.removeChild(old);

    var hint = btn.getAttribute('data-hint') || '';
    if (!hint) return;

    var tooltip = document.createElement('div');
    tooltip.className = 'btn-hint-tooltip';
    tooltip.textContent = hint;
    document.body.appendChild(tooltip);

    // Position the tooltip above the button
    var rect = btn.getBoundingClientRect();
    tooltip.style.position = 'fixed';
    tooltip.style.left = Math.max(10, rect.left + rect.width / 2 - 120) + 'px';
    tooltip.style.top = Math.max(10, rect.top - 44) + 'px';
    tooltip.style.zIndex = '9999';

    // Auto-dismiss after 2s
    setTimeout(function() {
        if (tooltip && tooltip.parentNode) {
            tooltip.style.opacity = '0';
            tooltip.style.transform = 'translateY(-4px)';
            setTimeout(function() {
                if (tooltip && tooltip.parentNode) tooltip.parentNode.removeChild(tooltip);
            }, 250);
        }
    }, 2200);
}

// Expose for external call after tab content loads
window._syncGenerateButtons = function() {
    // Determine current status from engine-status element
    var statusEl = document.getElementById('engine-status');
    var currentStatus = 'none';
    if (statusEl) {
        if (statusEl.classList.contains('loaded')) currentStatus = 'loaded';
        else if (statusEl.classList.contains('loading')) currentStatus = 'loading';
        else if (statusEl.classList.contains('error')) currentStatus = 'error';
    }
    _syncGenerateButtonsState(currentStatus);
};

// Global generation state flag
window._isGenerating = false;

// Set global generating state and sync all generate buttons
window._setGeneratingState = function(isGenerating) {
    window._isGenerating = !!isGenerating;
    // Re-sync buttons to reflect the generating state
    window._syncGenerateButtons();
};

// Expose module API
// @deprecated 使用 TTSApp.model 替代，此 window 挂载点将在未来版本移除
window.ModelSwitcher = {
    switch: window.switchModel,
    unload: window._unloadCurrentModel,
    updateStatus: window.updateEngineStatus,
    load: window.loadModel,
    unloadModel: window.unloadModel
};
})();

(function UXFixEngineSwitchGuard() {
    'use strict';

    var _engineSwitchTimer = null;

    function _isGenerating() {
        try {
            var progContainer = document.getElementById('progress-container');
            if (progContainer && progContainer.classList.contains('active') === true) {
                return true;
            }
        } catch (e) {}
        try {
            var cancelBtn = document.querySelector('.progress-cancel-btn');
            if (cancelBtn) {
                var style = window.getComputedStyle(cancelBtn);
                if (style.display !== 'none' && style.visibility !== 'hidden' && cancelBtn.offsetParent !== null) {
                    return true;
                }
            }
        } catch (e) {}
        try {
            if (window.TTSApp && window.TTSApp.generation && window.TTSApp.generation.isRunning === true) {
                return true;
            }
        } catch (e) {}
        try {
            var pctTextEl = document.getElementById('progress-percent');
            if (pctTextEl) {
                var txt = pctTextEl.textContent || '';
                var m = txt.match(/(\d+(?:\.\d+)?)/);
                if (m) {
                    var pct = parseFloat(m[1]);
                    if (!isNaN(pct) && pct > 0 && pct < 100) {
                        return true;
                    }
                }
            }
        } catch (e) {}
        return false;
    }

    function _setModelTabsRunningStyle(running) {
        var selectors = '.model-tabs-group .model-tab, .model-tabs-group button, #top-engine-selector label, .model-tab';
        var tabs = document.querySelectorAll(selectors);
        for (var i = 0; i < tabs.length; i++) {
            var tab = tabs[i];
            if (running) {
                tab.style.opacity = '0.6';
                tab.style.cursor = 'not-allowed';
                tab.style.pointerEvents = 'none';
                tab.setAttribute('title', '生成完成后可切换');
                tab.setAttribute('data-running-disabled', 'true');
            } else {
                if (tab.getAttribute('data-running-disabled') === 'true') {
                    tab.style.opacity = '';
                    tab.style.cursor = '';
                    tab.style.pointerEvents = '';
                    tab.removeAttribute('title');
                    tab.removeAttribute('data-running-disabled');
                }
            }
        }
    }

    function _showEngineSwitchProgress() {
        // D9: 切换状态内联化——不再创建全宽横幅，改为顶部引擎 tab 内旋转指示
        document.querySelectorAll('.model-tab').forEach(function(t) {
            t.classList.add('model-tab-loading');
        });
        if (_engineSwitchTimer) clearTimeout(_engineSwitchTimer);
        _engineSwitchTimer = setTimeout(function() {
            _removeEngineSwitchProgress();
        }, 40000);
    }

    function _removeEngineSwitchProgress() {
        // 移除引擎 tab 内联切换指示
        document.querySelectorAll('.model-tab').forEach(function(t) {
            t.classList.remove('model-tab-loading');
        });
        // 兼容清理历史版本遗留的全宽横幅（若 DOM 中仍存在）
        try {
            var bar = document.getElementById('engine-switch-progress');
            if (bar) {
                bar.style.opacity = '0';
                setTimeout(function() {
                    if (bar.parentNode) bar.parentNode.removeChild(bar);
                }, 300);
            }
        } catch (e) {}
        if (_engineSwitchTimer) {
            clearTimeout(_engineSwitchTimer);
            _engineSwitchTimer = null;
        }
    }

    var _originalSwitchModel = window.switchModel;
    var _originalModelSwitcherSwitch = window.ModelSwitcher ? window.ModelSwitcher.switch : null;

    function _wrappedSwitchModel(modelName) {
        if (_isGenerating()) {
            var ok = window.confirm('当前正在生成语音，切换引擎将终止当前任务且无法恢复。\n\n确定要切换吗？');
            if (!ok) return;
        }
        _showEngineSwitchProgress();
        var ret = _originalSwitchModel.apply(this, arguments);
        return ret;
    }

    window.switchModel = _wrappedSwitchModel;
    if (window.ModelSwitcher) {
        window.ModelSwitcher.switch = _wrappedSwitchModel;
    }

    document.addEventListener('click', function(e) {
        var btn = e.target.closest('.model-tabs-group .model-tab, .model-tabs-group button, #top-engine-selector label, .model-tab');
        if (!btn) return;
        if (_isGenerating()) {
            _setModelTabsRunningStyle(true);
        }
    }, true);

    setInterval(function() {
        _setModelTabsRunningStyle(_isGenerating());
    }, 1000);

    if (window.EventSource || window.htmx) {
        (function attachSSECleanup() {
            function tryClean() {
                try {
                    var engineDisplay = document.getElementById('engine-status-text');
                    if (engineDisplay) {
                        var txt = engineDisplay.textContent || '';
                        if (txt.indexOf('Ready') !== -1 || txt.indexOf('就绪') !== -1 || txt.indexOf('Error') !== -1 || txt.indexOf('错误') !== -1) {
                            _removeEngineSwitchProgress();
                        }
                    }
                } catch (e) {}
            }
            if (typeof EventSource !== 'undefined') {
                var origAdd = EventSource.prototype.addEventListener;
                if (origAdd) {
                    document.addEventListener('engine_switch_cleanup', tryClean);
                }
            }
            tryClean();
            setTimeout(tryClean, 2000);
            setTimeout(tryClean, 5000);
            setTimeout(tryClean, 10000);
        })();
    }
})();
