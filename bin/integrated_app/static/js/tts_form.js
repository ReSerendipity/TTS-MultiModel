/**
 * TTS Form Utilities - Shared form validation, submit state management, and auto-play
 * Extracted from duplicate code across tab templates
 */
window.TTSForm = (function() {
    'use strict';

    /**
     * Initialize form validation for TTS generation forms
     * @param {Object} config
     * @param {string} config.formId - Form element ID
     * @param {string} config.statusId - Status message element ID
     * @param {string} config.submitBtnId - Submit button ID (optional)
     * @param {string} config.textInputName - Name of the text input/textarea (default: 'text')
     * @param {string} config.emptyTextMsg - Message when text is empty
     * @param {Function} config.validate - Optional validate callback (return false to block submit)
     */
    function initValidation(config) {
        var form = document.getElementById(config.formId);
        if (!form) return;

        var emptyMsg = config.emptyTextMsg || (window.I18N && window.I18N['enter_text']) || '请输入文本';
        var textInputName = config.textInputName || 'text';

        form.addEventListener('submit', function(e) {
            if (typeof config.validate === 'function') {
                var result = config.validate(this);
                if (result === false) {
                    e.preventDefault();
                    return false;
                }
            }

            var textarea = this.querySelector('textarea[name="' + textInputName + '"]');
            var input = this.querySelector('input[name="' + textInputName + '"]');
            var textEl = textarea || input;
            if (textEl && !textEl.value.trim()) {
                e.preventDefault();
                var statusEl = document.getElementById(config.statusId);
                if (statusEl) {
                    statusEl.className = 'status-message error';
                    statusEl.textContent = emptyMsg;
                }
                return false;
            }
        });
    }

    /**
     * Manage submit button state (disable during generation, restore after)
     * @param {Object} config
     * @param {string} config.formId - Form element ID
     * @param {string} config.resultId - Result container ID
     * @param {string} config.submitBtnId / config.submitId - Submit button ID (optional)
     * @param {boolean} config.disableSubmit - Whether to disable the button during requests (default: true)
     */
    function initSubmitState(config) {
        var form = document.getElementById(config.formId);
        if (!form) return;

        var submitBtn = config.submitBtnId || config.submitId
            ? document.getElementById(config.submitBtnId || config.submitId)
            : form.querySelector('button[type="submit"], .generate-btn');
        if (!submitBtn) return;

        // Ensure button text is wrapped in span.btn-text for spinner replacement
        var btnTextEl = submitBtn.querySelector('.btn-text');
        if (!btnTextEl) {
            var textContent = submitBtn.textContent.trim();
            submitBtn.innerHTML = '<span class="btn-text">' + textContent + '</span>';
        }

        var originalHtml = submitBtn.innerHTML;

        function setBtnState(state) {
            submitBtn.classList.remove('btn-loading', 'btn-success', 'btn-error');
            if (state) {
                submitBtn.classList.add(state);
            }
        }

        var shouldDisable = config.disableSubmit !== false;

        if (shouldDisable) {
            form.addEventListener('htmx:beforeRequest', function() {
                submitBtn.disabled = true;
                setBtnState('btn-loading');
            });

            document.addEventListener('htmx:afterRequest', function(evt) {
                if (evt.detail && evt.detail.target) {
                    var targetId = (typeof evt.detail.target === 'string') ? evt.detail.target : (evt.detail.target.id || '');
                    // Normalize selector like '#vd-result' to 'vd-result'
                    if (targetId && targetId.charAt(0) === '#') {
                        targetId = targetId.substring(1);
                    }
                    var domTargetId = (evt.detail.target.getAttribute && evt.detail.target.getAttribute('id')) || '';
                    if (targetId === config.resultId || domTargetId === config.resultId) {
                        setBtnState(null);
                        var isSuccessful = evt.detail.successful !== false && !(evt.detail.xhr && evt.detail.xhr.status >= 400);
                        if (isSuccessful) {
                            setBtnState('btn-success');
                            setTimeout(function() {
                                setBtnState(null);
                                submitBtn.disabled = false;
                            }, 1500);
                        } else {
                            setBtnState('btn-error');
                            // Show error feedback in status area
                            var statusEl = document.getElementById(config.statusId);
                            if (statusEl) {
                                var xhr = evt.detail.xhr;
                                var errorMsg = '';
                                if (xhr) {
                                    if (xhr.status === 403) {
                                        errorMsg = (window.I18N && window.I18N['csrf_error']) || '安全验证失败，请刷新页面后重试';
                                    } else {
                                        try {
                                            var resp = JSON.parse(xhr.responseText);
                                            errorMsg = resp.message || resp.detail || ('HTTP ' + xhr.status);
                                        } catch(e) {
                                            // Try to extract error message from HTML error fragment
                                            try {
                                                var tmp = document.createElement('div');
                                                tmp.innerHTML = xhr.responseText;
                                                var htmlErr = tmp.querySelector('.error-message, .status-message.error');
                                                if (htmlErr && htmlErr.textContent.trim()) {
                                                    errorMsg = htmlErr.textContent.trim();
                                                } else {
                                                    errorMsg = 'HTTP ' + xhr.status;
                                                }
                                            } catch(htmlE) {
                                                errorMsg = 'HTTP ' + xhr.status;
                                            }
                                        }
                                    }
                                } else {
                                    errorMsg = (window.I18N && window.I18N['request_failed']) || '请求失败';
                                }
                                statusEl.innerHTML = '<div class="tts-error-block"><div class="error-title">' + ((window.I18N && window.I18N['gen_failed']) || '生成失败') + '</div><div class="error-message">' + errorMsg + '</div></div>';
                            }
                            if (window.Toast) Toast.show(errorMsg || ((window.I18N && window.I18N['gen_failed']) || '生成失败'), 'error');
                            setTimeout(function() {
                                setBtnState(null);
                                submitBtn.disabled = false;
                            }, 2000);
                        }
                    }
                }
            });

            document.addEventListener('htmx:responseError', function(evt) {
                setBtnState(null);
                setBtnState('btn-error');
                var statusEl = document.getElementById(config.statusId);
                var errorMsg = (window.I18N && window.I18N['network_error']) || '网络错误，请检查连接';
                if (evt.detail && evt.detail.xhr) {
                    if (evt.detail.xhr.status === 403) {
                        errorMsg = (window.I18N && window.I18N['csrf_error']) || '安全验证失败，请刷新页面后重试';
                    } else {
                        try {
                            var resp = JSON.parse(evt.detail.xhr.responseText);
                            errorMsg = resp.message || resp.detail || errorMsg;
                        } catch(e) {
                            try {
                                var tmp = document.createElement('div');
                                tmp.innerHTML = evt.detail.xhr.responseText;
                                var htmlErr = tmp.querySelector('.error-message, .status-message.error');
                                if (htmlErr && htmlErr.textContent.trim()) {
                                    errorMsg = htmlErr.textContent.trim();
                                }
                            } catch(htmlE) {}
                        }
                    }
                }
                if (statusEl) {
                    statusEl.innerHTML = '<div class="tts-error-block"><div class="error-title">' + ((window.I18N && window.I18N['gen_failed']) || '生成失败') + '</div><div class="error-message">' + errorMsg + '</div></div>';
                }
                if (window.Toast) Toast.show(errorMsg, 'error');
                setTimeout(function() {
                    setBtnState(null);
                    submitBtn.disabled = false;
                }, 2000);
            });

            document.addEventListener('htmx:sendError', function() {
                setBtnState(null);
                setBtnState('btn-error');
                var statusEl = document.getElementById(config.statusId);
                var errorMsg = (window.I18N && window.I18N['network_error']) || '网络错误，请检查连接';
                if (statusEl) {
                    statusEl.innerHTML = '<div class="tts-error-block"><div class="error-title">' + ((window.I18N && window.I18N['gen_failed']) || '生成失败') + '</div><div class="error-message">' + errorMsg + '</div></div>';
                }
                if (window.Toast) Toast.show(errorMsg, 'error');
                setTimeout(function() {
                    setBtnState(null);
                    submitBtn.disabled = false;
                }, 2000);
            });
        }
    }

    /**
     * Setup auto-play for generated audio
     * @param {Object} config
     * @param {string} config.formId - Form element ID
     * @param {string} config.resultId - Result container ID
     * @param {string} config.audioElementId - Audio element ID (optional)
     */
    function initAutoPlay(config) {
        var form = document.getElementById(config.formId);
        if (!form) return;

        form.addEventListener('htmx:afterSettle', function(e) {
            if (e.detail && e.detail.successful) {
                var resultEl = document.getElementById(config.resultId);
                if (!resultEl) return;
                var audioSrc = resultEl.querySelector('audio');
                if (audioSrc && window.globalAudioPlayer) {
                    var filename = audioSrc.src.split('/').pop().split('?')[0];
                    window.globalAudioPlayer.play(audioSrc.src, filename);
                }
                if (config.audioElementId) {
                    var audioEl = document.getElementById(config.audioElementId);
                    if (audioEl) audioEl.classList.remove('audio-hidden');
                }
            }
        });
    }

    /**
     * Initialize character counter and auto-resize for a textarea.
     * Reads data-* attributes from the textarea when config values are absent.
     * @param {Object} config
     * @param {string} config.textareaId - Textarea element ID
     * @param {string} config.counterId - Counter element ID (optional)
     * @param {number} config.maxChars - Maximum character count (optional)
     * @param {number} config.segmentMaxChars - Per-segment max chars (optional)
     * @param {string} config.counterClass - CSS class for counter (optional)
     */
    function initCharCounter(config) {
        var textarea = document.getElementById(config.textareaId);
        if (!textarea) return;

        var maxChars = config.maxChars || parseInt(textarea.getAttribute('data-max-chars'), 10) || 8192;
        var segmentMaxChars = config.segmentMaxChars || parseInt(textarea.getAttribute('data-segment-max-chars'), 10) || 200;
        var counterId = config.counterId || textarea.getAttribute('data-char-counter') || (config.textareaId.replace('-text', '-char-counter'));
        var counterClass = config.counterClass || textarea.getAttribute('data-counter-class') || 'tts-char-counter';

        if (window.CharCounter) {
            window.CharCounter.init({
                textareaId: config.textareaId,
                counterId: counterId,
                maxChars: maxChars,
                segmentMaxChars: segmentMaxChars,
                counterClass: counterClass
            });
        }

        var autoResize = config.autoResize !== false &&
            (textarea.classList.contains('tts-auto-resize') || textarea.hasAttribute('data-auto-resize'));
        if (autoResize && window.AutoResize) {
            window.AutoResize.init(config.textareaId);
        }
    }

    /**
     * Initialize all TTS form features at once
     * @param {Object} config
     * @param {string} config.formId - Form element ID
     * @param {string} config.resultId - Result container ID
     * @param {string} config.submitBtnId / config.submitId - Submit button ID (optional)
     * @param {string} config.statusId - Status message element ID (optional)
     * @param {Function} config.validate - Optional validate callback (return false to block submit)
     * @param {boolean} config.disableSubmit - Disable submit button during requests (default: true)
     * @param {string} config.textareaId - Textarea element ID (optional, auto-detected from form)
     * @param {string} config.textInputName - Name of the text input/textarea (default: 'text')
     */
    function init(config) {
        if (config.formId) {
            initValidation(config);
            initSubmitState(config);
            initAutoPlay(config);

            if (!config.textareaId) {
                var form = document.getElementById(config.formId);
                if (form) {
                    var textInputName = config.textInputName || 'text';
                    var ta = form.querySelector('textarea[name="' + textInputName + '"]');
                    if (ta && ta.id) {
                        config.textareaId = ta.id;
                    }
                }
            }
        }

        if (config.textareaId) {
            initCharCounter(config);
        }
    }

    return {
        init: init,
        initValidation: initValidation,
        initSubmitState: initSubmitState,
        initAutoPlay: initAutoPlay,
        initCharCounter: initCharCounter
    };
})();

/**
 * TTS Tab Switcher - Shared inner tab switching logic
 */
window.TTSTabSwitcher = (function() {
    'use strict';

    /**
     * Initialize inner tab switching
     * @param {Object} config
     * @param {string} config.formId - Form container ID
     * @param {string} config.prefix - Prefix for tab panel IDs (e.g., 'vc', 'vd', 'it2')
     * @param {string} config.panelClass - CSS class for tab panels (default: 'inner-tab-panel')
     * @param {string} config.btnClass - CSS class for tab buttons (default: 'sub-tab-btn')
     */
    function init(config) {
        var prefix = config.prefix;
        var formEl = document.getElementById(config.formId);
        if (!formEl) return;

        var panelClass = config.panelClass || 'inner-tab-panel';
        var btnClass = config.btnClass || 'sub-tab-btn';

        window['switch' + prefix.charAt(0).toUpperCase() + prefix.slice(1) + 'InnerTab'] = function(tab, btn) {
            formEl.querySelectorAll('.' + panelClass).forEach(function(p) { p.classList.add('panel-hidden'); });
            formEl.querySelectorAll('.' + btnClass).forEach(function(b) { b.classList.remove('active'); });
            var panel = document.getElementById(prefix + '-tab-' + tab);
            if (panel) panel.classList.remove('panel-hidden');
            if (btn) btn.classList.add('active');
        };
    }

    return { init: init };
})();

/**
 * TTS File Upload - Shared file upload feedback logic
 */
window.TTSFileUpload = (function() {
    'use strict';

    /**
     * Initialize file upload feedback
     * @param {Object} config
     * @param {string} config.inputId - File input element ID
     * @param {string} config.nameId - File name display element ID
     * @param {string} config.uploadAreaId - Upload area element ID (optional)
     */
    function init(config) {
        var input = document.getElementById(config.inputId);
        var nameEl = document.getElementById(config.nameId);
        if (!input || !nameEl) return;

        input.addEventListener('change', function() {
            if (this.files && this.files.length > 0) {
                nameEl.textContent = this.files[0].name;
                nameEl.classList.add('file-selected');
            } else {
                nameEl.textContent = (window.I18N && window.I18N['supported_formats']) || '支持 WAV/MP3/FLAC/OGG';
                nameEl.classList.remove('file-selected');
            }
        });

        if (config.uploadAreaId) {
            var uploadArea = document.getElementById(config.uploadAreaId);
            if (uploadArea) {
                uploadArea.addEventListener('click', function(e) {
                    if (e.target.tagName !== 'INPUT') {
                        input.click();
                    }
                });
            }
        }
    }

    return { init: init };
})();

// Add spin animation if not already defined
(function() {
    var existing = document.getElementById('tts-spin-style');
    if (!existing) {
        var style = document.createElement('style');
        style.id = 'tts-spin-style';
        style.textContent = '@keyframes tts-spin { to { transform: rotate(360deg); } }';
        document.head.appendChild(style);
    }
})();

(function UXFixFormDirtyDetection() {
    'use strict';
    const DIRTY_FORMS = new WeakMap();
    const LONG_TEXT_THRESHOLD = 200;

    function getTabContent() {
        return document.getElementById('tab-content');
    }

    function getTargetContainer(el) {
        var form = el.closest('form');
        if (form) return form;
        var tabContentParent = el.closest('[data-tab-content]');
        if (tabContentParent) return tabContentParent;
        var tc = getTabContent();
        return tc || document.body;
    }

    function markDirty(el, isSuper) {
        var container = getTargetContainer(el);
        if (!container) return;
        if (container.tagName === 'FORM') {
            DIRTY_FORMS.set(container, true);
            container.dataset.dirty = 'true';
            if (isSuper) {
                container.dataset.superDirty = 'true';
            } else {
                delete container.dataset.superDirty;
            }
        } else {
            DIRTY_FORMS.set(container, true);
            container.dataset.dirty = 'true';
            if (isSuper) {
                container.dataset.superDirty = 'true';
            } else {
                delete container.dataset.superDirty;
            }
        }
    }

    function isDirty(container) {
        if (!container) return false;
        if (DIRTY_FORMS.get(container)) return true;
        if (container.dataset && container.dataset.dirty === 'true') return true;
        return false;
    }

    function isSuperDirty(container) {
        if (!container || !container.dataset) return false;
        return container.dataset.superDirty === 'true';
    }

    function hasAnyDirty() {
        var tc = getTabContent();
        if (!tc) return false;
        var forms = tc.querySelectorAll('form');
        for (var i = 0; i < forms.length; i++) {
            if (isDirty(forms[i])) return true;
        }
        if (forms.length === 0) {
            var namedInputs = tc.querySelectorAll('[name]');
            for (var j = 0; j < namedInputs.length; j++) {
                var c = getTargetContainer(namedInputs[j]);
                if (isDirty(c)) return true;
            }
        }
        if (isDirty(tc)) return true;
        return false;
    }

    function hasLongDirty() {
        var tc = getTabContent();
        if (!tc) return false;
        var textareas = tc.querySelectorAll('textarea');
        for (var i = 0; i < textareas.length; i++) {
            if (textareas[i].value && textareas[i].value.length > LONG_TEXT_THRESHOLD) {
                var c = getTargetContainer(textareas[i]);
                if (isDirty(c)) return true;
            }
        }
        var forms = tc.querySelectorAll('form');
        for (var j = 0; j < forms.length; j++) {
            if (isSuperDirty(forms[j])) return true;
        }
        if (isSuperDirty(tc)) return true;
        return false;
    }

    function clearDirtyFromContainer(container) {
        if (!container) return;
        DIRTY_FORMS.delete(container);
        if (container.dataset) {
            delete container.dataset.dirty;
            delete container.dataset.superDirty;
        }
    }

    function clearDirtyFromSubmit(btn) {
        var form = btn.closest('form');
        if (form) {
            clearDirtyFromContainer(form);
            return;
        }
        var parentContainer = btn.closest('[data-tab-content]');
        if (parentContainer) {
            clearDirtyFromContainer(parentContainer);
            return;
        }
        var tc = getTabContent();
        if (tc) {
            var forms = tc.querySelectorAll('form');
            for (var i = 0; i < forms.length; i++) {
                clearDirtyFromContainer(forms[i]);
            }
            clearDirtyFromContainer(tc);
        }
    }

    function onInput(e) {
        var target = e.target;
        if (!target) return;
        var tc = getTabContent();
        if (!tc) return;
        if (!tc.contains(target)) return;

        var tagName = target.tagName;
        var isFormElement = (tagName === 'INPUT' || tagName === 'SELECT' || tagName === 'TEXTAREA');
        if (!isFormElement) return;

        var hasName = target.hasAttribute('name');
        var isInsideForm = !!target.closest('form');
        if (!isInsideForm && !hasName) return;

        var isSuper = false;
        if (tagName === 'TEXTAREA' && target.value && target.value.length > LONG_TEXT_THRESHOLD) {
            isSuper = true;
        }
        markDirty(target, isSuper);
    }

    function onHtmxSwap(e) {
        if (!hasAnyDirty()) return;
        var msg = '当前表单有未生成的修改，切换页面将丢失内容。\n\n确定继续切换吗？';
        if (!window.confirm(msg)) {
            if (e.preventDefault) e.preventDefault();
            if (e.stopPropagation) e.stopPropagation();
            return false;
        }
    }

    function onBeforeUnload(e) {
        if (hasAnyDirty() && hasLongDirty()) {
            var msg = '您有未保存的生成参数修改，离开将丢失内容';
            if (e) {
                e.returnValue = msg;
            }
            return msg;
        }
    }

    function onGenerateClick(e) {
        var target = e.target;
        if (!target) return;
        var btn = target.closest('.btn-primary, button[name="generate"]');
        if (!btn) return;
        clearDirtyFromSubmit(btn);
    }

    document.addEventListener('input', onInput, true);
    document.addEventListener('change', onInput, true);
    if (window.htmx) {
        document.addEventListener('htmx:beforeSwap', onHtmxSwap);
    } else {
        document.addEventListener('htmx:beforeSwap', onHtmxSwap);
    }
    window.addEventListener('beforeunload', onBeforeUnload);
    document.addEventListener('click', onGenerateClick, true);
})();
