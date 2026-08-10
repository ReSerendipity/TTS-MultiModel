/* ===== SSE Connection Manager ===== */
(function() {
var unifiedEventSource = null;
var retryCount = 0;
var maxRetries = 10;
var baseRetryDelay = 2000;
var isConnecting = false;
var isFirstConnection = true;

// Progress smoothing state
var lastDisplayedProgress = 0;
var progressSmoothTimeout = null;
var PROGRESS_MIN_INCREMENT = 0.5; // Minimum progress increment to show
var PROGRESS_SMOOTH_DELAY = 100; // Delay for smooth updates

function smoothProgressUpdate(targetProgress, updateFn) {
    if (progressSmoothTimeout) {
        clearTimeout(progressSmoothTimeout);
    }
    
    // Never go backwards
    if (targetProgress < lastDisplayedProgress) {
        targetProgress = lastDisplayedProgress;
    }
    
    // If jump is large, update immediately
    if (targetProgress - lastDisplayedProgress > 10) {
        lastDisplayedProgress = targetProgress;
        updateFn(targetProgress);
        return;
    }
    
    // Smooth incremental updates
    function step() {
        var diff = targetProgress - lastDisplayedProgress;
        if (diff <= PROGRESS_MIN_INCREMENT) {
            lastDisplayedProgress = targetProgress;
            updateFn(targetProgress);
            return;
        }
        lastDisplayedProgress += Math.max(diff * 0.3, PROGRESS_MIN_INCREMENT);
        updateFn(Math.min(lastDisplayedProgress, targetProgress));
        progressSmoothTimeout = setTimeout(step, PROGRESS_SMOOTH_DELAY);
    }
    step();
}

function resetProgressSmoothing() {
    lastDisplayedProgress = 0;
    if (progressSmoothTimeout) {
        clearTimeout(progressSmoothTimeout);
        progressSmoothTimeout = null;
    }
}

function createSSEConnection() {
    if (isConnecting) return;
    if (unifiedEventSource) {
        try { unifiedEventSource.close(); } catch(ex) {}
        unifiedEventSource = null;
    }

    isConnecting = true;

    try {
        unifiedEventSource = new EventSource('/api/sse/events');
    } catch(e) {
        console.error('[SSE] Failed to create EventSource:', e);
        isConnecting = false;
        scheduleReconnect();
        return;
    }

    unifiedEventSource.onopen = function() {
        console.log('[SSE] Connection established');
        retryCount = 0;
        isConnecting = false;
        isFirstConnection = false;
    };

    unifiedEventSource.onerror = function() {
        isConnecting = false;
        if (isFirstConnection) {
            // First connection may be aborted by browser during page load/navigation - this is normal
            console.debug('[SSE] Establishing connection...');
        } else {
            console.warn('[SSE] Connection error, readyState:', unifiedEventSource ? unifiedEventSource.readyState : 'unknown');
        }
        if (unifiedEventSource && unifiedEventSource.readyState === EventSource.CLOSED) {
            scheduleReconnect();
        }
    };

    unifiedEventSource.addEventListener('status', function(e) {
        try {
            var data = JSON.parse(e.data);
            var statusBar = document.getElementById('status-bar');
            var navStatus = document.getElementById('nav-status-text');
            var engineStatusEl = document.getElementById('engine-status');
            var engineStatusText = document.getElementById('engine-status-text');
            if (statusBar) {
                statusBar.innerHTML = '<span class="tts-status-indicator tts-status-ready">' + window.I18N["ready"] + '</span> <span class="status-pulse"></span> ' + data.status_text;
            }
            if (navStatus) {
                navStatus.textContent = data.status_text.replace(/\*\*/g, '');
            }
            if (data.model_type && engineStatusEl && engineStatusText) {
                // Multi-engine display: support dots.tts in addition to voxcpm2/indextts2
                var engineNameMap = {
                    'voxcpm2': 'VoxCPM2',
                    'indextts2': 'IndexTTS 2.0',
                    'dotstts': 'dots.tts'
                };
                var engineName = engineNameMap[data.engine] ||
                    (data.engine === 'none' || !data.engine
                        ? ((window.I18N && window.I18N['model_none']) || '无')
                        : data.engine);
                if (data.model_type !== 'none') {
                    engineStatusEl.classList.remove('error');
                    engineStatusText.textContent = engineName + ' | ' + window.I18N["ready"];
                } else {
                    engineStatusEl.classList.add('error');
                    engineStatusText.textContent = engineName + ' | ' + window.I18N["not_loaded"];
                }
                if (!window._sidebarInitialized && window.updateSidebarByEngine) {
                    window._sidebarInitialized = true;
                    window.updateSidebarByEngine(data.engine || '');
                }
            }
        } catch(err) {}
    });

    // Throttle ARIA announcements to avoid overwhelming screen readers
    var lastAnnounceTime = 0;
    var lastAnnounceText = '';
    function announceProgress(text) {
        if (!text || text === lastAnnounceText) return;
        var now = Date.now();
        if (now - lastAnnounceTime < 3000) return;
        lastAnnounceTime = now;
        lastAnnounceText = text;
        if (window.TTSApp && window.TTSApp.announce) {
            window.TTSApp.announce(text, 'polite');
        }
    }

    unifiedEventSource.addEventListener('progress', function(e) {
        var progressBar = document.getElementById('progress-bar');
        if (!progressBar || !e.data) return;

        // 显示进度容器
        var progressContainer = document.querySelector('.progress-container');
        if (progressContainer) progressContainer.classList.add('active');

        if (window.appState) window.appState.setGenerating(true);
        // Mark global generating state to disable all generate buttons (prevent double-submit)
        if (typeof window._setGeneratingState === 'function') {
            window._setGeneratingState(true);
        }

        var cancelBtn = document.getElementById('progress-cancel-btn');
        if (cancelBtn) cancelBtn.classList.add('visible');

        // 向后兼容：优先尝试 JSON 格式，降级为纯 HTML
        try {
            var data = JSON.parse(e.data);
            // 新格式：JSON 包含 html 字段
            if (data.html !== undefined) {
                progressBar.innerHTML = data.html;
            } else if (typeof data === 'string') {
                progressBar.innerHTML = data;
            }
            if (window.updateProgressDetail) {
                window.updateProgressDetail(
                    data.phase || '',
                    data.progress ? data.progress + '%' : '',
                    data.speed || '',
                    data.remaining || ''
                );
            }
            // Announce progress to screen readers (throttled)
            if (data.phase && data.progress !== undefined) {
                var progressText = (window.I18N && window.I18N['generating']) || '正在生成';
                announceProgress(progressText + ' ' + data.phase + ' ' + data.progress + '%');
            }
        } catch(ex) {
            // 降级：纯 HTML 格式（向后兼容）
            progressBar.innerHTML = e.data;
            if (window.updateProgressDetail) {
                var phaseMatch = e.data.match(/tts-progress-phase[^>]*>([^<]+)/);
                var pctMatch = e.data.match(/tts-progress-percentage[^>]*>([^<]+)/);
                var speedMatch = e.data.match(/tts-progress-speed[^>]*>([^<]+)/);
                var remainingMatch = e.data.match(/tts-progress-remaining[^>]*>([^<]+)/);
                window.updateProgressDetail(
                    phaseMatch ? phaseMatch[1] : '',
                    pctMatch ? pctMatch[1] : '',
                    speedMatch ? speedMatch[1] : '',
                    remainingMatch ? remainingMatch[1] : ''
                );
            }
        }
    });

    unifiedEventSource.addEventListener('complete', function(e) {
        resetProgressSmoothing();
        if (window.appState) window.appState.setGenerating(false);
        // Clear global generating state to re-enable generate buttons
        if (typeof window._setGeneratingState === 'function') {
            window._setGeneratingState(false);
        }
        var cancelBtn = document.getElementById('progress-cancel-btn');
        if (cancelBtn) {
            cancelBtn.classList.remove('visible', 'cancelled');
            cancelBtn.querySelector('span').textContent = window.I18N["cancel"];
        }
        // Announce completion to screen readers
        if (window.TTSApp && window.TTSApp.announce) {
            window.TTSApp.announce((window.I18N && window.I18N['sse_generation_complete']) || '生成完成', 'polite');
        }
        // 延迟隐藏进度容器
        setTimeout(function() {
            var progressContainer = document.querySelector('.progress-container');
            if (progressContainer) progressContainer.classList.remove('active');
        }, 2000);
    });

    unifiedEventSource.addEventListener('cancelled', function(e) {
        resetProgressSmoothing();
        try {
            var data = JSON.parse(e.data);
            if (window.appState) window.appState.setGenerating(false);
            // Clear global generating state to re-enable generate buttons
            if (typeof window._setGeneratingState === 'function') {
                window._setGeneratingState(false);
            }
            var cancelBtn = document.getElementById('progress-cancel-btn');
            if (cancelBtn) {
                cancelBtn.classList.remove('visible');
                cancelBtn.classList.add('cancelled');
                cancelBtn.querySelector('span').textContent = window.I18N["stopped"];
            }
            var message = data.message || window.I18N["cancel_generation"] || '生成已取消';
            showToast(message, 'warning', 2000);
            if (window.TTSApp && window.TTSApp.announce) {
                window.TTSApp.announce(message, 'polite');
            }
        } catch(err) {}
    });

    unifiedEventSource.addEventListener('engine_switch', function(e) {
        try {
            var data = JSON.parse(e.data);
            var engineDisplay = document.getElementById('engine-status-text');
            var engineStatusEl = document.getElementById('engine-status');
            if (data.active && engineDisplay) {
                if (data.status === 'in_progress') {
                    engineDisplay.textContent = data.step || window.I18N["loading"];
                    if (window.appState) window.appState.setSwitchingEngine(true);
                } else if (data.status === 'completed') {
                    var engName = '';
                    var engRadioValue = '';
                    var engineNames = {voxcpm2: 'VoxCPM2', indextts2: 'IndexTTS 2.0', dotstts: 'dots.tts'};
                    engName = engineNames[data.engine] || data.engine;
                    engRadioValue = data.engine === 'voxcpm2' ? 'VoxCPM2' : data.engine;
                    engineDisplay.textContent = engName + ' | ' + window.I18N["ready"];
                    if (engineStatusEl) engineStatusEl.classList.remove('error');
                    if (window.appState) window.appState.setSwitchingEngine(false);

                    // Sync model-tab buttons (base.html) with the switched engine
                    var modelTabs = document.querySelectorAll('.model-tab');
                    modelTabs.forEach(function(tab) {
                        var tabModel = tab.getAttribute('data-model');
                        var isActive = (tabModel === data.engine);
                        tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
                        if (isActive) {
                            tab.classList.add('active');
                        } else {
                            tab.classList.remove('active');
                        }
                    });

                    if (window.updateSidebarByEngine) {
                        window.updateSidebarByEngine(data.engine || '');
                    }
                    if (window.autoSwitchTabForEngine) {
                        window.autoSwitchTabForEngine(data.engine);
                    }
                    var pageTitle = document.getElementById('top-page-title');
                    if (pageTitle) {
                        var currentTab = document.querySelector('.sidebar-item.active:not(.sidebar-item-hidden)');
                        if (currentTab) {
                            var tabId = currentTab.getAttribute('data-tab');
                            pageTitle.textContent = (window.getPageTitles && window.getPageTitles()[tabId]) || 'TTS MultiModel';
                        }
                    }
                } else if (data.status === 'failed') {
                    engineDisplay.textContent = (window.I18N["lora_switch"] || 'Switch') + ' ' + (window.I18N["error"] || '') + ': ' + (data.error || window.I18N["unknown_error"]);
                    if (window.appState) window.appState.setSwitchingEngine(false);
                }
            }
        } catch(err) {}
    });

    // PF-1: 模型加载细粒度进度事件
    unifiedEventSource.addEventListener('model_load', function(e) {
        try {
            var data = JSON.parse(e.data);
            var engineDisplay = document.getElementById('engine-status-text');
            var engineStatusEl = document.getElementById('engine-status');

            if (data.active) {
                if (data.status === 'in_progress') {
                    // 显示加载进度文本
                    var stepText = data.step || (window.I18N["loading"] || '加载中...');
                    if (engineDisplay) {
                        engineDisplay.textContent = stepText;
                    }
                    // 更新app状态
                    if (window.appState) {
                        window.appState.model_loading = true;
                        window.appState.model_status = 'loading';
                    }
                    // 同步生成按钮状态
                    if (typeof window._syncGenerateButtonsState === 'function') {
                        window._syncGenerateButtonsState('loading');
                    }
                } else if (data.status === 'completed') {
                    // 加载完成，刷新状态
                    var engineNames = {voxcpm2: 'VoxCPM2', indextts2: 'IndexTTS 2.0'};
                    var engName = engineNames[data.engine] || data.engine;
                    if (engineDisplay) {
                        engineDisplay.textContent = engName + ' | ' + (window.I18N["ready"] || 'Ready');
                    }
                    if (engineStatusEl) engineStatusEl.classList.remove('error');
                    if (window.appState) {
                        window.appState.model_loading = false;
                        window.appState.model_status = 'loaded';
                    }
                    // 同步生成按钮状态为可用
                    if (typeof window._syncGenerateButtonsState === 'function') {
                        window._syncGenerateButtonsState('loaded');
                    }
                    // 触发页面刷新（引擎选择器、状态指示器等）
                    if (window.fetchModelStatus) {
                        window.fetchModelStatus();
                    }
                } else if (data.status === 'failed') {
                    // 加载失败
                    if (engineDisplay) {
                        engineDisplay.textContent = (window.I18N["load_fail"] || 'Load failed') + ': ' + (data.error || '');
                    }
                    if (engineStatusEl) engineStatusEl.classList.add('error');
                    if (window.appState) {
                        window.appState.model_loading = false;
                        window.appState.model_status = 'error';
                    }
                    if (typeof window._syncGenerateButtonsState === 'function') {
                        window._syncGenerateButtonsState('error');
                    }
                }
            }
        } catch(err) {
            console.debug('[SSE] model_load event parse error:', err);
        }
    });
}

function disconnectSSE() {
    if (unifiedEventSource) {
        try { unifiedEventSource.close(); } catch(ex) {}
        unifiedEventSource = null;
    }
    isConnecting = false;
}

// Server-suggested retry interval (ms), updated by SSE retry field
var serverRetryInterval = null;

function scheduleReconnect() {
    if (retryCount >= maxRetries) {
        console.error('[SSE] Max retries reached, giving up');
        // Show visual disconnection indicator
        var statusEl = document.getElementById('engine-status-text');
        if (statusEl) {
            statusEl.textContent = (window.I18N && window.I18N['sse_disconnected']) || '连接已断开';
        }
        return;
    }
    // Use server-suggested interval if available, otherwise exponential backoff
    var delay;
    if (serverRetryInterval !== null) {
        delay = serverRetryInterval;
    } else {
        delay = baseRetryDelay * Math.pow(2, retryCount);
        // Cap delay at 30 seconds
        delay = Math.min(delay, 30000);
    }
    console.log('[SSE] Reconnecting in ' + delay + 'ms (attempt ' + (retryCount + 1) + '/' + maxRetries + ')');
    retryCount++;
    setTimeout(function() {
        createSSEConnection();
    }, delay);
}

// Auto-connect after DOM is ready (delay to allow page to fully load)
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function() {
        setTimeout(createSSEConnection, 800);
    });
} else {
    setTimeout(createSSEConnection, 800);
}

// ===== Toast Stubs (fallback if toast.js not loaded) =====
if (typeof window.showToastSuccess !== 'function') {
    window.showToastSuccess = function(message, duration) {
        if (window.Toast && typeof window.Toast.show === 'function') {
            window.Toast.show(message, 'success', duration);
        } else if (typeof window.showToast === 'function') {
            window.showToast(message, 'success', duration);
        } else {
            console.log('[Toast][Success]', message);
        }
    };
}
if (typeof window.showToastError !== 'function') {
    window.showToastError = function(message, duration) {
        if (window.Toast && typeof window.Toast.show === 'function') {
            window.Toast.show(message, 'error', duration);
        } else if (typeof window.showToast === 'function') {
            window.showToast(message, 'error', duration);
        } else {
            console.error('[Toast][Error]', message);
        }
    };
}
if (typeof window.showToast !== 'function') {
    window.showToast = function(message, type, duration) {
        if (window.Toast && typeof window.Toast.show === 'function') {
            window.Toast.show(message, type || 'info', duration);
        } else {
            var level = type === 'error' ? 'error' : (type === 'warning' ? 'warn' : 'log');
            console[level]('[Toast][' + (type || 'info') + ']', message);
        }
    };
}

// ===== Cancel Generation with Confirmation + Cancelling State =====
var cancellingTimer = null;
var _originalCancelBtnHtml = {};

function _isLongTask(btn) {
    try {
        var progressBar = document.querySelector('[data-duration]');
        if (progressBar) {
            var dur = parseFloat(progressBar.getAttribute('data-duration'));
            if (!isNaN(dur) && dur > 60) return true;
        }
    } catch (e) {}
    try {
        var estSec = window.TTSApp && window.TTSApp.generation && window.TTSApp.generation.estimatedSeconds;
        if (typeof estSec === 'number' && estSec > 60) return true;
    } catch (e) {}
    try {
        var pctEl = btn ? btn.closest('.progress-container') : null;
        if (!pctEl) pctEl = document.querySelector('.progress-container');
        if (pctEl) {
            var pctAttr = pctEl.getAttribute('data-progress-percent');
            var pct = parseFloat(pctAttr);
            if (!isNaN(pct) && pct > 50) return true;
        }
    } catch (e) {}
    try {
        var pctTextEl = document.getElementById('progress-percent');
        if (pctTextEl) {
            var txt = pctTextEl.textContent || '';
            var m = txt.match(/(\d+(?:\.\d+)?)/);
            if (m) {
                var pct2 = parseFloat(m[1]);
                if (!isNaN(pct2) && pct2 > 50) return true;
            }
        }
    } catch (e) {}
    return false;
}

function _setCancellingState(btn) {
    if (!btn) btn = document.getElementById('progress-cancel-btn') || document.querySelector('.progress-cancel-btn');
    if (!btn) return;
    if (!_originalCancelBtnHtml[btn]) {
        _originalCancelBtnHtml[btn] = btn.innerHTML;
    }
    btn.classList.add('cancelling');
    btn.disabled = true;
    var spanEl = btn.querySelector('span');
    if (spanEl) {
        spanEl.textContent = '取消中...';
    } else {
        btn.textContent = '取消中...';
    }
    var svgEl = btn.querySelector('svg');
    if (svgEl) {
        svgEl.style.animation = 'tts-spin 1s linear infinite';
    }
    if (cancellingTimer) clearTimeout(cancellingTimer);
    cancellingTimer = setTimeout(function() {
        _restoreCancelBtn(btn);
    }, 2500);
}

function _restoreCancelBtn(btn) {
    if (!btn) btn = document.getElementById('progress-cancel-btn') || document.querySelector('.progress-cancel-btn');
    if (!btn) return;
    if (cancellingTimer) {
        clearTimeout(cancellingTimer);
        cancellingTimer = null;
    }
    if (btn.classList.contains('cancelling')) {
        btn.classList.remove('cancelling');
        btn.disabled = false;
        if (_originalCancelBtnHtml[btn]) {
            btn.innerHTML = _originalCancelBtnHtml[btn];
        } else {
            var spanEl = btn.querySelector('span');
            if (spanEl) {
                spanEl.textContent = window.I18N ? (window.I18N['cancel'] || '取消') : '取消';
            }
        }
        var svgEl = btn.querySelector('svg');
        if (svgEl) {
            svgEl.style.animation = '';
        }
    }
}

function _performCancelRequest() {
    var csrfToken = window.getCsrfToken ? window.getCsrfToken() : '';
    var headers = {};
    if (csrfToken) headers['X-CSRF-Token'] = csrfToken;
    return fetch('/api/generation/cancel', {
        method: 'POST',
        headers: headers
    }).then(function(r) {
        try { return r.json(); } catch (e) { return {}; }
    }).then(function(data) {
        setTimeout(function() {
            if (typeof window.showToast === 'function') {
                window.showToast('已取消，已生成片段保留于历史记录', 'info', 2500);
            }
        }, 300);
        return data;
    }).catch(function(err) {
        console.error('[Cancel] request failed:', err);
        if (typeof window.showToastError === 'function') {
            window.showToastError('取消请求失败，请重试');
        }
        _restoreCancelBtn();
    });
}

window.cancelGeneration = function(btn) {
    if (!btn) btn = document.getElementById('progress-cancel-btn') || document.querySelector('.progress-cancel-btn');

    var isLong = _isLongTask(btn);
    var shouldProceed = true;

    if (isLong) {
        shouldProceed = window.confirm('当前任务已完成较大进度，取消后将仅保留已生成片段。\n\n确定取消吗？');
    } else {
        // 短任务扩展点：未来可根据配置决定是否对短任务也弹窗确认
        // if (SOME_CONFIG.confirmShortTaskCancel) { shouldProceed = window.confirm('...'); }
    }

    if (!shouldProceed) return false;

    _setCancellingState(btn);
    _performCancelRequest();
    return true;
};

// Wire up cancel button if not already bound
document.addEventListener('click', function(e) {
    var btn = e.target.closest('#progress-cancel-btn, .progress-cancel-btn');
    if (!btn) return;
    if (btn.classList.contains('cancelling')) return;
    e.preventDefault();
    e.stopPropagation();
    window.cancelGeneration(btn);
}, true);

// Restore cancel button on SSE cancelled/complete events
if (unifiedEventSource) {
    unifiedEventSource.addEventListener('cancelled', function() {
        setTimeout(function() { _restoreCancelBtn(); }, 500);
    });
    unifiedEventSource.addEventListener('complete', function() {
        setTimeout(function() { _restoreCancelBtn(); }, 500);
    });
}

// Expose module API
// @deprecated 使用 TTSApp.sse 替代，此 window 挂载点将在未来版本移除
window.SSEManager = {
    connect: createSSEConnection,
    disconnect: disconnectSSE,
    reconnect: function() {
        retryCount = 0;
        createSSEConnection();
    },
    cancel: window.cancelGeneration
};
})();
