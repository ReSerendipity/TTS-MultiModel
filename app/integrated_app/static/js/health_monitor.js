/* ===== Health Monitor Module ===== */
(function() {
window._healthPollTimer = null;
var healthTriggerElement = null;
var _miniPrevValues = { gpuUtil: null, vramPct: null, ramPct: null };
var _miniPollTimer = null;
var _miniPollIntervalFast = 4000;
var _miniPollIntervalIdle = 30000;

function _isSystemIdle() {
    var hasGenerating = false;
    try {
        if (window.TTSApp && window.TTSApp.generation) {
            hasGenerating = window.TTSApp.generation.isGenerating === true;
        }
        if (!hasGenerating) {
            var genIndicator = document.querySelector('.generation-indicator, [data-generating="true"], .generating');
            if (genIndicator) hasGenerating = true;
        }
        if (!hasGenerating && window.g_globalState) {
            hasGenerating = window.g_globalState.isGenerating === true;
        }
    } catch(e) {}
    return !hasGenerating;
}

function _setupMiniPolling() {
    if (_miniPollTimer) {
        clearInterval(_miniPollTimer);
        _miniPollTimer = null;
    }
    var isIdle = _isSystemIdle();
    var interval = isIdle ? _miniPollIntervalIdle : _miniPollIntervalFast;
    _miniPollTimer = setInterval(function() {
        var currentIdle = _isSystemIdle();
        var neededInterval = currentIdle ? _miniPollIntervalIdle : _miniPollIntervalFast;
        if (neededInterval !== interval) {
            _setupMiniPolling();
            return;
        }
        fetchMiniMonitorData();
    }, interval);
    _applyMiniMonitorIdleState(isIdle);
}

function _applyMiniMonitorIdleState(isIdle) {
    var valueEls = document.querySelectorAll('.mini-monitor-value');
    valueEls.forEach(function(el) {
        if (isIdle) {
            el.classList.add('idle');
            el.title = '空闲中，点击查看详情';
        } else {
            el.classList.remove('idle');
            if (el.title === '空闲中，点击查看详情') el.title = '';
        }
    });
}

window.openHealthPanel = function() {
    var overlay = document.getElementById('health-panel-overlay');
    if (!overlay) return;
    healthTriggerElement = document.activeElement;
    overlay.classList.add('visible');
    fetchHealthData();
    window.refreshHealthLogs();
    if (window._healthPollTimer) clearInterval(window._healthPollTimer);
    window._healthPollTimer = setInterval(fetchHealthData, 30000);
    var firstFocusable = overlay.querySelector('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
    if (firstFocusable) firstFocusable.focus();
};

// @deprecated 使用 TTSApp.health.close 替代，此 window 挂载点将在未来版本移除
window.closeHealthPanel = function(e) {
    if (e && e.target !== e.currentTarget && e.target.closest('.health-panel')) return;
    var overlay = document.getElementById('health-panel-overlay');
    if (overlay) overlay.classList.remove('visible');
    if (window._healthPollTimer) {
        clearInterval(window._healthPollTimer);
        window._healthPollTimer = null;
    }
    // Return focus to trigger element
    if (healthTriggerElement) {
        healthTriggerElement.focus();
        healthTriggerElement = null;
    }
};

/* ===== 操作日志区 =====
 * 这块 UI（列表容器 + 刷新按钮 + 4 个筛选按钮）早就画好了，但从来没有接上数据源，
 * 于是刷新与筛选全是死按钮（GOTCHAS #133）。数据源是 GET /api/system/logs-compat。
 * 注意 filter_type 走的是操作日志 type 的**精确匹配**，而「配置」按钮对应的 type
 * 叫 config_update —— 这层映射只能放在前端，传 'config' 永远查不到东西。 */
var LOG_TYPE_BY_FILTER = { all: 'all', generation: 'generation', model: 'model', config: 'config_update' };
var BADGE_CLASS_BY_TYPE = { generation: 'generation', model: 'model', config_update: 'config' };
var _healthLogFilter = 'all';

function _escLogText(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function _pad2(n) { return (n < 10 ? '0' : '') + n; }

function _renderHealthLogs(logs) {
    var list = document.getElementById('health-log-list');
    if (!list) return;
    if (!logs.length) {
        list.innerHTML = '<div class="health-empty-logs">'
            + _escLogText((window.I18N && window.I18N['no_logs']) || '暂无日志') + '</div>';
        return;
    }
    list.innerHTML = logs.map(function (e) {
        var d = new Date(e.timestamp || 0);
        var stamp = _pad2(d.getHours()) + ':' + _pad2(d.getMinutes()) + ':' + _pad2(d.getSeconds());
        var badge = BADGE_CLASS_BY_TYPE[e.type] || '';
        var detail = e.details && Object.keys(e.details).length ? ' · ' + _escLogText(JSON.stringify(e.details)) : '';
        return '<div class="health-log-entry"><span class="health-log-time">' + stamp + '</span>'
            + '<span class="health-log-type ' + badge + '">' + _escLogText(e.type) + '</span>'
            + '<span class="health-log-message">' + _escLogText(e.message) + detail + '</span></div>';
    }).join('');
}

window.refreshHealthLogs = function () {
    var list = document.getElementById('health-log-list');
    if (!list) return;
    var url = '/api/system/logs-compat?limit=200&filter_type='
        + encodeURIComponent(LOG_TYPE_BY_FILTER[_healthLogFilter] || 'all');
    fetch(url, { headers: { 'Accept': 'application/json' } })
        .then(function (r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function (d) { _renderHealthLogs(d.logs || []); })
        .catch(function (err) {
            list.innerHTML = '<div class="tts-error-block" role="alert"><div class="error-message">日志读取失败：'
                + _escLogText(err && err.message ? err.message : err) + '</div></div>';
        });
};

window.filterHealthLogs = function (type, btn) {
    _healthLogFilter = type;
    document.querySelectorAll('#health-log-filter .health-log-filter-btn').forEach(function (b) {
        b.classList.toggle('active', b === btn);
    });
    window.refreshHealthLogs();
};

function fetchHealthData() {
    var cacheKey = '/api/system/health';
    if (window.ApiCache) {
        window.ApiCache.get(cacheKey, {}, 10000)
            .then(function(data) {
                updateHealthPanel(data);
                updateMiniMonitor(data);
            })
            .catch(function(err) { console.warn('[Health] Fetch failed:', err); });
    } else {
        fetch('/api/system/health')
            .then(function(r) { return r.json(); })
            .then(function(data) {
                updateHealthPanel(data);
                // 同时更新 Mini Monitor
                updateMiniMonitor(data);
            })
            .catch(function(err) { console.warn('[Health] Fetch failed:', err); });
    }
}

function updateHealthPanel(data) {
    // GPU VRAM
    var gpuUsed = document.getElementById('health-gpu-used');
    var gpuTotal = document.getElementById('health-gpu-total');
    var gpuBar = document.getElementById('health-gpu-bar');
    if (data.gpu && gpuUsed) {
        gpuUsed.textContent = data.gpu.memory_used_mb + ' MB';
        if (gpuTotal) gpuTotal.textContent = data.gpu.memory_total_mb + ' MB';
        if (gpuBar) {
            var pct = data.gpu.memory_percent || 0;
            gpuBar.style.width = pct + '%';
            gpuBar.className = 'health-progress-fill ' + (pct > 90 ? 'health-red' : (pct > 75 ? 'health-yellow' : 'health-green'));
        }
    }
    // Memory (no CPU percent)
    var cpuUsed = document.getElementById('health-cpu-used');
    var cpuTotal = document.getElementById('health-cpu-total');
    var cpuBar = document.getElementById('health-cpu-bar');
    if (data.cpu && cpuUsed) {
        cpuUsed.textContent = data.cpu.memory_used_mb + ' MB';
        if (cpuTotal) cpuTotal.textContent = data.cpu.memory_total_mb + ' MB';
        if (cpuBar && data.cpu.memory_total_mb > 0) {
            var memPct = (data.cpu.memory_used_mb / data.cpu.memory_total_mb) * 100;
            cpuBar.style.width = memPct + '%';
            cpuBar.className = 'health-progress-fill ' + (memPct > 90 ? 'health-red' : (memPct > 75 ? 'health-yellow' : 'health-green'));
        }
    }
    // Model
    var modelEngine = document.getElementById('health-model-engine');
    var modelSize = document.getElementById('health-model-size');
    var modelStatus = document.getElementById('health-model-status');
    if (data.model) {
        if (modelEngine) modelEngine.textContent = _modelDisplayName(data.model.current_engine);
        if (modelSize) modelSize.textContent = _modelDisplayName(data.model.model_size);
        if (modelStatus) {
            modelStatus.textContent = data.model.status === 'ready' ? window.I18N["ready"] : window.I18N["not_loaded"];
            modelStatus.className = 'health-status-badge ' + (data.model.status === 'ready' ? 'ready' : 'not-loaded');
        }
    }
    // Stats
    var statsTotal = document.getElementById('health-stats-total');
    var statsAvg = document.getElementById('health-stats-avg');
    var statsRate = document.getElementById('health-stats-rate');
    if (data.stats) {
        if (statsTotal) statsTotal.textContent = data.stats.total_generations || 0;
        if (statsAvg) statsAvg.textContent = (data.stats.average_time || 0) + 's';
        if (statsRate) statsRate.textContent = (data.stats.success_rate || 100) + '%';
    }
}

function _shouldChangeColor(prevVal, newVal) {
    if (prevVal === null || prevVal === undefined) return true;
    if (prevVal === 0) return Math.abs(newVal) > 5;
    var diffPct = Math.abs(newVal - prevVal) / Math.abs(prevVal);
    return diffPct > 0.10;
}

function _getColorClass(newVal, prevVal) {
    if (!_shouldChangeColor(prevVal, newVal)) return null;
    if (newVal > 80) return 'accent-error';
    if (newVal < 30) return 'accent-success';
    return 'text-secondary';
}

// Number animation smoothing
var _activeAnimations = {};

function _animateNumber(el, startVal, endVal, duration, formatter, onComplete) {
    if (!el) return;
    var elId = el.id || ('anim_' + Math.random().toString(36).substr(2, 9));

    if (_activeAnimations[elId]) {
        cancelAnimationFrame(_activeAnimations[elId]);
    }

    var startTime = null;
    var prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    if (prefersReducedMotion || Math.abs(endVal - startVal) < 0.5) {
        el.textContent = formatter(endVal);
        if (onComplete) onComplete();
        return;
    }

    function step(timestamp) {
        if (!startTime) startTime = timestamp;
        var progress = Math.min((timestamp - startTime) / duration, 1);
        var eased = 1 - Math.pow(1 - progress, 3); // ease-out cubic
        var currentVal = startVal + (endVal - startVal) * eased;
        el.textContent = formatter(currentVal);

        if (progress < 1) {
            _activeAnimations[elId] = requestAnimationFrame(step);
        } else {
            delete _activeAnimations[elId];
            if (onComplete) onComplete();
        }
    }

    _activeAnimations[elId] = requestAnimationFrame(step);
}

function _updateValueWithSmooth(el, newText, valueKey, newVal, formatter) {
    if (!el) return;
    el.style.fontVariantNumeric = 'tabular-nums';
    el.classList.remove('accent-error', 'accent-success', 'text-secondary');
    el.classList.add('updating', 'tabular');

    var prevVal = _miniPrevValues[valueKey];
    var colorClass = _getColorClass(newVal, prevVal);

    if (colorClass) {
        var baseClass = 'mini-monitor-value';
        if (_isSystemIdle()) baseClass += ' idle';
        el.className = baseClass + ' ' + colorClass + ' tabular updating';
    } else {
        if (!el.classList.contains('mini-monitor-value')) el.classList.add('mini-monitor-value');
        if (!el.classList.contains('tabular')) el.classList.add('tabular');
    }

    // Animate numeric values
    if (formatter && prevVal !== null && prevVal !== undefined && !isNaN(prevVal) && !isNaN(newVal)) {
        _animateNumber(el, prevVal, newVal, 400, formatter);
    } else {
        el.textContent = newText;
    }

    _miniPrevValues[valueKey] = newVal;
    setTimeout(function() {
        el.classList.remove('updating');
    }, 400);
}

window.initMiniMonitor = function() {
    fetchMiniMonitorData();
    _setupMiniPolling();
    var checkInterval = setInterval(function() {
        var isIdle = _isSystemIdle();
        _applyMiniMonitorIdleState(isIdle);
    }, 5000);
};

// 模型内部 id → 用户可读名称（避免内部标识直接暴露给用户）
var _MODEL_DISPLAY_NAMES = { voxcpm2: 'VoxCPM2', indextts2: 'IndexTTS 2.5', indextts20: 'IndexTTS 2.0' };
function _modelDisplayName(raw) {
    if (!raw) return '--';
    return _MODEL_DISPLAY_NAMES[raw] || raw;
}

function fetchMiniMonitorData() {
    fetch('/api/system/health')
        .then(function(r) { return r.json(); })
        .then(function(data) { updateMiniMonitor(data); })
        .catch(function(err) { console.warn('[MiniMonitor] Fetch failed:', err); });
}

function updateMiniMonitor(data) {
    // 顶部信息收敛为单个"系统状态"入口：状态点按 GPU/内存占用最大值分级（绿/黄/红），数值在健康面板内展示
    var dot = document.getElementById('mini-status-dot');
    if (!dot) return;
    var gpuPct = (data.gpu && data.gpu.memory_percent) || 0;
    var ramPct = 0;
    if (data.cpu && data.cpu.memory_total_mb > 0) {
        ramPct = (data.cpu.memory_used_mb / data.cpu.memory_total_mb) * 100;
    }
    var maxPct = Math.max(gpuPct, ramPct);
    dot.className = 'mini-status-dot ' + (maxPct > 90 ? 'danger' : (maxPct > 75 ? 'warn' : 'ok'));
    dot.title = 'GPU ' + gpuPct.toFixed(0) + '% / 内存 ' + ramPct.toFixed(0) + '%';
}

// Expose module API
window.HealthMonitor = {
    open: window.openHealthPanel,
    close: window.closeHealthPanel,
    initMini: window.initMiniMonitor
};
})();

/* ===== U5: 队列等待醒目横幅（SSE time_estimate 驱动） =====
 * 后端 sse.py：当 queue_depth>0（有任务排队）时推送 time_estimate.status='generating'，
 * 并带 text/remaining；队列清空时推 'idle'/'complete'。横幅显示在主区域顶部，
 * 黄色醒目，队列清空后自动隐藏。health_monitor.js 先于 sse_manager.js 加载，
 * 故轮询等待 SSEManager 就绪后再订阅。 */
(function QueueWaitBannerController() {
    'use strict';

    var _banner = null;
    var _textEl = null;

    function _ensureRefs() {
        if (_banner) return true;
        _banner = document.getElementById('queue-wait-banner');
        if (!_banner) return false;
        _textEl = document.getElementById('queue-wait-text');
        return true;
    }

    function _show(text) {
        if (!_ensureRefs()) return;
        _banner.hidden = false;
        if (_textEl) _textEl.textContent = text || '当前有任务排队中…';
    }

    function _hide() {
        if (!_ensureRefs()) return;
        _banner.hidden = true;
    }

    function _onTimeEstimate(data) {
        if (!data || typeof data !== 'object') return;
        if (data.status === 'generating') {
            _show(data.text || '当前有任务排队中…');
        } else if (data.status === 'idle' || data.status === 'complete') {
            _hide();
        }
    }

    function _bind() {
        if (window.SSEManager && typeof window.SSEManager.on === 'function') {
            window.SSEManager.on('time_estimate', _onTimeEstimate);
            return true;
        }
        return false;
    }

    function _waitAndBind() {
        if (_bind()) return;
        var tries = 0;
        var timer = setInterval(function() {
            tries++;
            if (_bind() || tries > 50) clearInterval(timer);
        }, 100);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _waitAndBind);
    } else {
        _waitAndBind();
    }
})();
