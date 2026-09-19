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

// U4: 某引擎已加载后，给其它（非激活）模型 tab 一个悬浮说明。
// 用 CSS 自定义 tooltip（data-inactive-tip + .model-tab-inactive），不占用 title，
// 避免与上面 _setModelTabsDisabled 的 title 管理互相覆盖。
window._applyInactiveTabTip = function(activeModel) {
    var tipText = (window.I18N && window.I18N['switch_engine_hint']) ||
        '当前已加载其它引擎，点击可切换（将自动卸载并重新加载）';
    document.querySelectorAll('.model-tab').forEach(function(tab) {
        var isActive = tab.dataset.model === activeModel;
        var showInactive = !!activeModel && !isActive;
        tab.classList.toggle('model-tab-inactive', showInactive);
        if (showInactive) {
            tab.setAttribute('data-inactive-tip', tipText);
        } else {
            tab.removeAttribute('data-inactive-tip');
        }
    });
};

// 引擎切换成功后，让**内容区**跟上引擎。
// WHY：activateTab() 只改高亮，页面内容靠侧栏按钮上的 hx-get；切换时程序化调用它
// 不会重新拉模板，于是出现「侧栏写着 IndexTTS 2.5 的语音克隆、屏幕上还是 VoxCPM2 的表单」，
// 用户点生成就打去 /api/generate/voxcpm_clone → 400（GOTCHAS #131）。
// 只在"当前页属于另一个引擎"时才跳转；引擎无关页（工具组 data-model="all"）与本来就
// 属于新引擎的页都不动，免得把用户已填的表单清掉。
window._syncTabToEngine = function (modelName) {
    var active = window.__ACTIVE_TAB_ID__;
    if (!active) return; // 不知道屏幕上现在是哪一页就别动，宁可少跳也不要顶掉用户的内容
    var activeItem = document.querySelector('.sidebar-item[data-tab="' + active + '"]');
    if (activeItem) {
        var owner = activeItem.getAttribute('data-model');
        // 引擎无关页（工具组）与本来就属于新引擎的页都不动，免得清掉用户已填的表单
        if (!owner || owner === 'all' || owner === modelName) return;
    }
    var target = document.querySelector('.sidebar-item[data-model="' + modelName + '"]');
    if (target && window.TTSApp && window.TTSApp.sidebar) window.TTSApp.sidebar.gotoTab(target);
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

        // IndexTTS 2.0 拥有独立的侧栏分组与独立模板（data-model="indextts20"），
        // 不再把 indextts20 重映射到 indextts2 的 tab 集并按引擎版本裁剪。
        var tabModel = modelName;

        var sections = document.querySelectorAll('.sidebar-nav-section[data-section-model]');
        sections.forEach(function(sec) {
            var secModel = sec.dataset.sectionModel;
            var isVisible = (secModel === tabModel || modelName === 'none');
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
            var shouldShow = (item.dataset.model === tabModel || item.dataset.model === 'all');
            item.classList.toggle('sidebar-item-hidden', !shouldShow);
        });

        // 这里**不能**提前 activateTab 到新引擎的首个 tab：那只会把高亮挪过去，而
        // #tab-content 里仍是旧引擎的表单（内容靠按钮的 hx-get 拉，程序化高亮不触发它）。
        // 高亮与内容一起对齐，交给切换成功后的 _syncTabToEngine()。

        // Fade-in transition after switching
        if (tabContent) {
            tabContent.classList.remove('tab-switching');
        }

        if (modelName === 'none') {
            window._modelSwitching = false;
            window._setModelTabsDisabled(false);
            // 报告 B13：卸载后清空全局引擎缓存
            window.__CURRENT_ENGINE__ = null;
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
            // 报告 B13：维护全局当前引擎缓存，供各 Tab 的版本守卫同步读取
            if (currentEngine) { window.__CURRENT_ENGINE__ = currentEngine; }
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
                // 报告 B13：切换成功即更新全局引擎缓存
                window.__CURRENT_ENGINE__ = modelName;
                window.updateEngineStatus('loaded', modelName, (window.I18N && window.I18N['ready']) || 'Ready');
                if (window._syncTabToEngine) window._syncTabToEngine(modelName);
            } else if (data.message === '引擎已就绪，无需切换') {
                window.__CURRENT_ENGINE__ = modelName;
                window.updateEngineStatus('loaded', modelName, (window.I18N && window.I18N['ready']) || 'Ready');
                if (window._syncTabToEngine) window._syncTabToEngine(modelName);
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
        // 无引擎时清除 U4 非激活说明
        if (typeof window._applyInactiveTabTip === 'function') window._applyInactiveTabTip(null);
    } else if (status === 'loading') {
        statusEl.classList.add('loading');
        statusText.textContent = extra || (window.I18N && window.I18N["loading"]) || 'Loading...';
        statusText.classList.remove('status-weight-bold');
        if (statusIcon) { statusIcon.classList.remove('status-icon-hidden'); statusIcon.innerHTML = iconLoading; }
        announceText = extra || (window.I18N && window.I18N["loading"]) || 'Loading...';
        // 加载进行中：清除稳态非激活说明（由 _setModelTabsDisabled 接管 loading 提示）
        if (typeof window._applyInactiveTabTip === 'function') window._applyInactiveTabTip(null);
    } else if (status === 'loaded') {
        statusEl.classList.add('loaded');
        statusText.textContent = modelName + ' | ' + ((window.I18N && window.I18N["ready"]) || 'Ready');
        statusText.classList.remove('status-weight-bold');
        if (statusIcon) { statusIcon.classList.remove('status-icon-hidden'); statusIcon.innerHTML = iconLoaded; }
        announceText = modelName + ' ' + ((window.I18N && window.I18N["ready"]) || 'Ready');
        // Re-enable model tabs after loading completes
        window._modelSwitching = false;
        window._setModelTabsDisabled(false);
        // U4: 给其它非激活模型 tab 加悬浮说明
        if (typeof window._applyInactiveTabTip === 'function') window._applyInactiveTabTip(modelName);
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
    // status='none' 表示 DOM 推断不到明确状态（#engine-status 无 loaded/loading/error class），
    // 此时不做任何变更——避免首屏渲染瞬态把按钮永久 disabled。
    // 真实状态由 _syncInitialState 的 API fetch 覆盖。
    if (status === 'none') return;
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

/* ===== U2: 模型加载 / 引擎切换全屏遮罩控制器 =====
 * 订阅 SSE model_load 与 engine_switch（payload 同形：
 * {active, step, status, error, engine}）。SSE 循环会按当前状态周期性重发，
 * 故按 status 驱动：in_progress 显示并刷新阶段；completed 短暂停留后收起；
 * failed 显示错误并延时收起；idle/active=false 收起。
 * 注意：model_switcher.js 先于 sse_manager.js 加载，需轮询等待 SSEManager 就绪。 */
(function ModelLoadingOverlayController() {
    'use strict';

    var _overlay = null;
    var _engineEl = null;
    var _stepEl = null;
    var _hideTimer = null;
    var _shown = false;

    function _ensureRefs() {
        if (_overlay) return true;
        _overlay = document.getElementById('ml-loading-overlay');
        if (!_overlay) return false;
        _engineEl = document.getElementById('ml-loading-engine');
        _stepEl = document.getElementById('ml-loading-step');
        return true;
    }

    var _ENGINE_NAMES = {
        voxcpm2: 'VoxCPM2',
        indextts2: 'IndexTTS 2.5',
        indextts20: 'IndexTTS 2.0'
    };
    function _engineName(e) { return _ENGINE_NAMES[e] || e || ''; }

    function _show(engine, step, isError) {
        if (!_ensureRefs()) return;
        if (_hideTimer) { clearTimeout(_hideTimer); _hideTimer = null; }
        _overlay.hidden = false;
        void _overlay.offsetWidth; // 强制 reflow，确保过渡动画生效
        _overlay.classList.add('active');
        _overlay.classList.toggle('is-error', !!isError);
        if (_engineEl) _engineEl.textContent = _engineName(engine) + ' 模型';
        if (_stepEl) _stepEl.textContent = step || '正在加载...';
        _shown = true;
    }

    function _hide(delayMs) {
        if (!_ensureRefs()) return;
        var doHide = function() {
            if (!_overlay) return;
            _overlay.classList.remove('active');
            _overlay.classList.remove('is-error');
            var ov = _overlay;
            setTimeout(function() { ov.hidden = true; }, 260);
            _hideTimer = null;
            _shown = false;
        };
        if (delayMs && delayMs > 0) {
            if (_hideTimer) clearTimeout(_hideTimer);
            _hideTimer = setTimeout(doHide, delayMs);
        } else {
            if (_hideTimer) { clearTimeout(_hideTimer); _hideTimer = null; }
            doHide();
        }
    }

    function _onModelLoadEvent(data) {
        if (!data || typeof data !== 'object') return;
        if (data.status === 'in_progress' && data.active !== false) {
            _show(data.engine, data.step || '正在加载...', false);
        } else if (data.status === 'completed') {
            // 完成：短暂停留"完成"再收起 500ms
            _show(data.engine, (window.I18N && window.I18N['ready']) || '加载完成', false);
            _hide(500);
        } else if (data.status === 'failed') {
            _show(data.engine,
                (data.error || (window.I18N && window.I18N['error']) || '加载失败'),
                true);
            _hide(1800);
        } else {
            // idle / active=false：无进行中加载才立即收起；
            // 若 completed/failed 已排好延时收起，则不打断它
            if (_shown && !_hideTimer) _hide(0);
        }
    }

    function _bind() {
        if (window.SSEManager && typeof window.SSEManager.on === 'function') {
            window.SSEManager.on('model_load', _onModelLoadEvent);
            window.SSEManager.on('engine_switch', _onModelLoadEvent);
            return true;
        }
        return false;
    }

    // 初始化时通过 API 获取真实模型状态，避免 #engine-status 类名未就绪时
    // 默认 'none' 导致所有生成按钮永久 disabled。
    function _syncInitialState() {
        window._syncGenerateButtons();
        fetch('/api/model/status', {credentials: 'same-origin'})
            .then(function(r) { return r.json(); })
            .then(function(data) {
                var status = data.loaded ? 'loaded' : (data.model_status || 'idle');
                _syncGenerateButtonsState(status);
            })
            .catch(function() { /* API 不可达时保留 DOM 推断结果 */ });
    }

    function _waitAndBind() {
        if (_bind()) { _syncInitialState(); return; }
        var tries = 0;
        var timer = setInterval(function() {
            tries++;
            if (_bind()) { clearInterval(timer); _syncInitialState(); }
            else if (tries > 50) clearInterval(timer);
        }, 100);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _waitAndBind);
    } else {
        _waitAndBind();
    }
})();
