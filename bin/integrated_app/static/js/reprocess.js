/**
 * Unified Audio Reprocess Module
 * Handles reprocessing of generated audio with different parameters.
 */
var Reprocess = (function() {
    'use strict';

    function execute(config) {
        var resultEl = document.getElementById(config.resultId);
        var audioPath = config.audioPath;
        var reprocessUrl = config.url || '/api/generate/post-process';
        var params = config.params || {};

        if (!audioPath) {
            Toast.show('No audio to reprocess', 'warning');
            return;
        }

        var statusEl = document.getElementById(config.statusId);
        if (statusEl) {
            statusEl.innerHTML = '<div style="color:var(--text-muted);font-size:13px;">' + ((window.I18N && window.I18N['processing']) || 'Processing...') + '</div>';
        }

        // Build form-urlencoded body
        var bodyParts = ['audio_path=' + encodeURIComponent(audioPath)];
        if (params.tempo_factor !== undefined) bodyParts.push('tempo_factor=' + encodeURIComponent(params.tempo_factor));
        if (params.voice_enhancement !== undefined) bodyParts.push('voice_enhancement=' + encodeURIComponent(params.voice_enhancement));
        if (params.target_lufs !== undefined) bodyParts.push('target_lufs=' + encodeURIComponent(params.target_lufs));

        var fetchHeaders = {'Content-Type': 'application/x-www-form-urlencoded'};
        var csrfToken = window.getCsrfToken ? window.getCsrfToken() : '';
        if (csrfToken) fetchHeaders['X-CSRF-Token'] = csrfToken;

        fetch(reprocessUrl, {
            method: 'POST',
            headers: fetchHeaders,
            body: bodyParts.join('&')
        })
        .then(function(resp) {
            if (!resp.ok) {
                var errMsg = 'HTTP ' + resp.status;
                if (resp.status === 403) {
                    errMsg = (window.I18N && window.I18N['csrf_error']) || '安全验证失败，请刷新页面后重试';
                }
                if (statusEl) {
                    statusEl.innerHTML = '<div class="tts-error-block"><div class="error-message">' + errMsg + '</div></div>';
                }
                if (window.Toast) Toast.show(errMsg, 'error');
                throw new Error(errMsg);
            }
            return resp.text();
        })
        .then(function(html) {
            if (resultEl) resultEl.innerHTML = html;
            // Try to play the reprocessed audio
            var audioSrc = resultEl ? resultEl.querySelector('audio') : null;
            if (audioSrc && window.globalAudioPlayer) {
                var filename = audioSrc.src.split('/').pop().split('?')[0];
                window.globalAudioPlayer.play(audioSrc.src, filename);
            }
            // Show audio element if hidden
            var audioEl = document.getElementById(config.audioElementId);
            if (audioSrc && audioEl) {
                audioEl.classList.remove('audio-hidden');
            }
        })
        .catch(function(err) {
            if (statusEl) {
                statusEl.innerHTML = '';
                var errBlock = document.createElement('div');
                errBlock.className = 'tts-error-block';
                var errMsg = document.createElement('div');
                errMsg.className = 'error-message';
                errMsg.textContent = err.message;
                errBlock.appendChild(errMsg);
                statusEl.appendChild(errBlock);
            }
        });
    }

    return { execute: execute };
})();
