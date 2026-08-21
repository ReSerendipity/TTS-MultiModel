/**
 * Unified Character Counter Module
 * Provides real-time character counting and segment splitting information.
 *
 * Usage (object style):
 *   CharCounter.init({
 *     textareaId: 'my-textarea',
 *     counterId: 'my-counter',
 *     maxChars: 3072,
 *     segmentMaxChars: 200
 *   });
 *
 * Usage (simplified positional):
 *   CharCounter.init('prefix', 8192, 200);
 */
var CharCounter = (function() {
    'use strict';

    function _t(key, placeholders) {
        if (!window.I18N) return key;
        var template = window.I18N[key];
        if (typeof template !== 'string') return key;
        if (!placeholders) return template;
        return template.replace(/\{(\w+)\}/g, function(_match, name) {
            return placeholders.hasOwnProperty(name) ? String(placeholders[name]) : _match;
        });
    }

    function _countSegments(text, maxChars) {
        if (!text || text.trim() === '') return 0;
        var len = text.length;
        return Math.ceil(len / maxChars);
    }

    function _getCounterClass(current, max) {
        if (current === 0) return 'micro-counter-empty';
        if (current <= max * 0.8) return 'micro-counter-ok';
        if (current <= max * 0.95) return 'micro-counter-warn';
        return 'micro-counter-danger';
    }

    function update(config) {
        var textarea = document.getElementById(config.textareaId);
        var counterEl = document.getElementById(config.counterId);

        if (!textarea || !counterEl) return;

        var text = textarea.value || '';
        var current = text.length;
        var max = config.maxChars || 3072;
        var segmentMax = config.segmentMaxChars || 200;
        var segments = _countSegments(text, segmentMax);

        // Build counter text: "current/max (N segments) | N chars per segment"
        var segmentLabel = '';
        if (segments > 0) {
            var plural = segments > 1 && window.CURRENT_LANG === 'en' ? 's' : '';
            segmentLabel = ' (' + _t('char_counter_segments', { count: segments, plural: plural }) + ')';
        }
        var segmentHint = segmentMax
            ? ' | ' + _t('char_counter_per_segment', { max: segmentMax })
            : '';
        counterEl.textContent = current + '/' + max + segmentLabel + segmentHint;
        counterEl.className = config.counterClass + ' ' + _getCounterClass(current, max);

        // Update legacy segment info element if present
        if (config.segmentInfoId) {
            var segEl = document.getElementById(config.segmentInfoId);
            if (segEl) {
                segEl.textContent = segments > 0
                    ? '(' + _t('char_counter_segments', { count: segments }) + ')'
                    : '';
            }
        }
    }

    /**
     * Initialize character counter with either object config or simplified positional args.
     *
     * Object style: init({ textareaId, counterId, maxChars, segmentMaxChars, counterClass })
     * Simplified:   init(prefix, maxChars, segmentMaxChars)
     */
    function init(prefixOrConfig, maxChars, segmentMaxChars) {
        // Detect calling style
        if (typeof prefixOrConfig === 'object') {
            // Object style: init(config)
            var config = prefixOrConfig;
            var textarea = document.getElementById(config.textareaId);
            if (!textarea) return;
            if (textarea.getAttribute('data-char-counter-initialized') === 'true') return;
            textarea.setAttribute('data-char-counter-initialized', 'true');
            update(config);
            textarea.addEventListener('input', function() { update(config); });
            textarea.addEventListener('paste', function() { setTimeout(function() { update(config); }, 0); });
        } else {
            // Simplified style: init(prefix, maxChars, segmentMaxChars)
            var prefix = prefixOrConfig;
            var _config = {
                textareaId: prefix + '-text',
                counterId: prefix + '-char-counter',
                maxChars: maxChars || 8192,
                segmentMaxChars: segmentMaxChars || 200,
                counterClass: prefix + '-char-counter'
            };
            var _textarea = document.getElementById(_config.textareaId);
            if (!_textarea) return;
            if (_textarea.getAttribute('data-char-counter-initialized') === 'true') return;
            _textarea.setAttribute('data-char-counter-initialized', 'true');
            update(_config);
            _textarea.addEventListener('input', function() { update(_config); });
            _textarea.addEventListener('paste', function() { setTimeout(function() { update(_config); }, 0); });
        }
    }

    return {
        init: init,
        update: update
    };
})();
