/* ===== Global Audio Player Module ===== */
(function() {
var playerEl, audioEl, playBtn, titleEl, progressBar, progressFill, progressThumb;
var volumeIcon, volumeSlider, closeBtn, speedCurrentBtn, speedDropdown, speedOptions;
var timeCurrentEl, timeDurationEl;
var progressInterval = null;
var isHidden = false;
var isInitialized = false;
var waveformCanvas, waveContainer, waveTooltip, clipBtn;
var audioCtx, analyser, source, dataArray, bufferLength;
var animFrameId = null;
var waveformPeaks = null;
var clipMode = false;
var clipStart = null, clipEnd = null;
var clipOverlay = null;
var isDraggingProgress = false;

function getWaveformColors() {
    var style = getComputedStyle(document.documentElement);
    return {
        staticFill: style.getPropertyValue('--waveform-static-fill').trim() || 'rgba(8, 145, 178, 0.5)',
        bgBar: style.getPropertyValue('--waveform-bg-bar').trim() || 'rgba(8, 145, 178, 0.15)',
        gradientStart: style.getPropertyValue('--waveform-gradient-start').trim() || 'rgba(8, 145, 178, 0.9)',
        gradientMid: style.getPropertyValue('--waveform-gradient-mid').trim() || 'rgba(6, 182, 212, 0.8)',
        gradientEnd: style.getPropertyValue('--waveform-gradient-end').trim() || 'rgba(8, 145, 178, 0.9)',
        playhead: style.getPropertyValue('--waveform-playhead').trim() || '#FFFFFF',
        clipFill: style.getPropertyValue('--clip-region-fill').trim() || 'rgba(5, 150, 105, 0.2)',
        clipStroke: style.getPropertyValue('--clip-region-stroke').trim() || '#059669'
    };
}

function initPlayer() {
    if (isInitialized) return;
    playerEl = document.getElementById('global-audio-player');
    audioEl = document.getElementById('global-audio-element');
    playBtn = document.getElementById('gap-play-btn');
    titleEl = document.getElementById('gap-title');
    progressBar = document.getElementById('gap-progress-bar');
    progressFill = document.getElementById('gap-progress-fill');
    progressThumb = document.getElementById('gap-progress-thumb');
    volumeIcon = document.getElementById('gap-volume-icon');
    volumeSlider = document.getElementById('gap-volume-slider');
    closeBtn = document.getElementById('gap-close-btn');
    speedCurrentBtn = document.getElementById('gap-speed-current');
    speedDropdown = document.getElementById('gap-speed-dropdown');
    speedOptions = document.querySelectorAll('.gap-speed-option');
    timeCurrentEl = document.getElementById('gap-time-current');
    timeDurationEl = document.getElementById('gap-time-duration');

    if (!playerEl || !audioEl || !playBtn) {
        console.warn('[GlobalAudio] Elements not ready, retrying...');
        setTimeout(initPlayer, 50);
        return;
    }

    isInitialized = true;
    console.log('[GlobalAudio] Player initialized');

    // Waveform elements
    waveformCanvas = document.getElementById('gap-waveform');
    waveContainer = document.getElementById('gap-wave-container');
    waveTooltip = document.getElementById('gap-wave-tooltip');
    clipBtn = document.getElementById('gap-clip-btn');

    // Event Listeners
    playBtn.addEventListener('click', togglePlay);
    closeBtn.addEventListener('click', function() {
        audioEl.pause();
        audioEl.src = '';
        stopProgressUpdate();
        hidePlayer();
        isHidden = true;
    });

    // Progress bar: click to seek + drag support
    progressBar.addEventListener('mousedown', onProgressMouseDown);
    progressBar.addEventListener('touchstart', onProgressTouchStart, { passive: false });
    document.addEventListener('mousemove', onProgressDrag);
    document.addEventListener('mouseup', onProgressDragEnd);
    document.addEventListener('touchmove', onProgressTouchDrag, { passive: false });
    document.addEventListener('touchend', onProgressDragEnd);

    volumeSlider.addEventListener('input', function() {
        audioEl.volume = parseFloat(volumeSlider.value);
        updateVolumeIcon();
    });

    volumeIcon.addEventListener('click', function() {
        audioEl.muted = !audioEl.muted;
        updateVolumeIcon();
    });

    // Speed dropdown
    speedCurrentBtn.addEventListener('click', function(e) {
        e.stopPropagation();
        speedDropdown.classList.toggle('open');
    });
    speedOptions.forEach(function(opt) {
        opt.addEventListener('click', function() {
            var speed = parseFloat(opt.dataset.speed);
            audioEl.playbackRate = speed;
            speedCurrentBtn.textContent = speed + 'x';
            speedOptions.forEach(function(o) { o.classList.remove('active'); });
            opt.classList.add('active');
            speedDropdown.classList.remove('open');
        });
    });
    // Close dropdown on outside click
    document.addEventListener('click', function(e) {
        if (!e.target.closest('.gap-speed')) {
            speedDropdown.classList.remove('open');
        }
    });

    // Default speed
    audioEl.playbackRate = 1;

    // Waveform hover tooltip
    if (waveformCanvas) {
        waveformCanvas.addEventListener('mousemove', function(e) {
            if (!waveformPeaks || !audioEl.duration) return;
            var rect = waveformCanvas.getBoundingClientRect();
            var x = e.clientX - rect.left;
            var pct = x / rect.width;
            var idx = Math.floor(pct * waveformPeaks.length);
            if (idx < 0) idx = 0;
            if (idx >= waveformPeaks.length) idx = waveformPeaks.length - 1;
            var time = pct * audioEl.duration;
            var amp = waveformPeaks[idx];
            waveTooltip.textContent = formatTime(time) + ' | ' + Math.round(amp * 100) + '%';
            waveTooltip.style.display = 'block';
            waveTooltip.style.left = x + 'px';
            waveTooltip.style.top = '-20px';
        });
        waveformCanvas.addEventListener('mouseleave', function() {
            waveTooltip.style.display = 'none';
        });
    }

    // Clip button
    if (clipBtn) {
        clipBtn.addEventListener('click', toggleClipMode);
    }

    // Download button
    var downloadBtn = document.getElementById('gap-download-btn');
    if (downloadBtn) {
        downloadBtn.addEventListener('click', function() {
            if (!audioEl || !audioEl.src) return;
            var a = document.createElement('a');
            a.href = audioEl.src;
            a.download = (titleEl ? titleEl.textContent : 'audio') + '.wav';
            a.click();
        });
    }

    // Audio events
    audioEl.addEventListener('ended', function() {
        playBtn.innerHTML = '&#9654;';
        playBtn.classList.remove('playing');
        stopProgressUpdate();
        progressFill.style.width = '0%';
        if (clipMode) exitClipMode();
    });

    audioEl.addEventListener('pause', function() {
        playBtn.innerHTML = '&#9654;';
        playBtn.classList.remove('playing');
        stopProgressUpdate();
        if (animFrameId) cancelAnimationFrame(animFrameId);
    });

    audioEl.addEventListener('play', function() {
        playBtn.innerHTML = '&#10074;&#10074;';
        playBtn.classList.add('playing');
        startProgressUpdate();
        drawWaveformLive();
    });

    audioEl.addEventListener('loadedmetadata', function() {
        if (timeDurationEl && audioEl.duration) {
            timeDurationEl.textContent = formatTime(audioEl.duration);
        }
    });
}

// === Progress bar drag handlers ===
function seekToPosition(clientX) {
    if (!audioEl.duration) return;
    var rect = progressBar.getBoundingClientRect();
    var pct = (clientX - rect.left) / rect.width;
    pct = Math.max(0, Math.min(1, pct));
    audioEl.currentTime = pct * audioEl.duration;
    progressFill.style.width = (pct * 100) + '%';
    if (timeCurrentEl) timeCurrentEl.textContent = formatTime(audioEl.currentTime);
}

function onProgressMouseDown(e) {
    if (!audioEl.duration) return;
    isDraggingProgress = true;
    progressBar.classList.add('dragging');
    seekToPosition(e.clientX);
}

function onProgressTouchStart(e) {
    if (!audioEl.duration) return;
    e.preventDefault();
    isDraggingProgress = true;
    progressBar.classList.add('dragging');
    var touch = e.touches[0];
    seekToPosition(touch.clientX);
}

function onProgressDrag(e) {
    if (!isDraggingProgress) return;
    seekToPosition(e.clientX);
}

function onProgressTouchDrag(e) {
    if (!isDraggingProgress) return;
    e.preventDefault();
    var touch = e.touches[0];
    seekToPosition(touch.clientX);
}

function onProgressDragEnd() {
    if (!isDraggingProgress) return;
    isDraggingProgress = false;
    progressBar.classList.remove('dragging');
}

function formatTime(sec) {
    if (isNaN(sec) || sec < 0) sec = 0;
    var m = Math.floor(sec / 60);
    var s = Math.floor(sec % 60);
    return (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s;
}

function updateProgress() {
    if (!audioEl || !audioEl.src || !audioEl.duration) return;
    if (isDraggingProgress) return; // Don't update while dragging
    var pct = (audioEl.currentTime / audioEl.duration) * 100;
    progressFill.style.width = pct + '%';
    if (timeCurrentEl) timeCurrentEl.textContent = formatTime(audioEl.currentTime);
    if (timeDurationEl) timeDurationEl.textContent = formatTime(audioEl.duration);
}

function startProgressUpdate() {
    if (progressInterval) clearInterval(progressInterval);
    progressInterval = setInterval(updateProgress, 100);
}

function stopProgressUpdate() {
    if (progressInterval) { clearInterval(progressInterval); progressInterval = null; }
}

function updateVolumeIcon() {
    if (!volumeIcon || !audioEl) return;
    if (audioEl.muted || audioEl.volume === 0) {
        volumeIcon.textContent = '\uD83D\uDD07';
    } else if (audioEl.volume < 0.5) {
        volumeIcon.textContent = '\uD83D\uDD09';
    } else {
        volumeIcon.textContent = '\uD83D\uDD0A';
    }
}

function showPlayer() {
    if (!playerEl) return;
    playerEl.classList.add('visible');
    var mainContent = document.querySelector('.main-content');
    if (mainContent) mainContent.classList.add('has-audio');
}

function hidePlayer() {
    if (!playerEl) return;
    playerEl.classList.remove('visible');
    var mainContent = document.querySelector('.main-content');
    if (mainContent) mainContent.classList.remove('has-audio');
}

function showError(msg) {
    var existing = document.getElementById('global-audio-error');
    if (existing) existing.remove();
    var toast = document.createElement('div');
    toast.id = 'global-audio-error';
    toast.textContent = msg;
    var errorStyle = getComputedStyle(document.documentElement);
    var errorBg = errorStyle.getPropertyValue('--toast-error-bg').trim() || '#ef4444';
    toast.style.cssText = 'position:fixed;bottom:120px;left:50%;transform:translateX(-50%);background:' + errorBg + ';color:var(--toast-text,#fff);padding:8px 16px;border-radius:8px;font-size:13px;z-index:9999;animation:gapToastIn 0.2s ease-out;max-width:90vw;text-align:center;';
    document.body.appendChild(toast);
    setTimeout(function() {
        if (toast.parentNode) {
            toast.style.opacity = '0';
            toast.style.transition = 'opacity 0.2s';
            setTimeout(function() { toast.remove(); }, 200);
        }
    }, 3000);
}

function playAudio(src, title) {
    if (!audioEl) {
        console.warn('[GlobalAudio] audio element not initialized yet, retrying...');
        setTimeout(function() { playAudio(src, title); }, 100);
        return;
    }
    if (!src) {
        showError('音频源地址无效');
        return;
    }
    audioEl.src = src;
    decodeAudioToWaveform(src);
    titleEl.textContent = title || '播放中...';
    showPlayer();
    audioEl.play().then(function() {
        playBtn.innerHTML = '\u275A\u275A';
        playBtn.classList.add('playing');
        startProgressUpdate();
    }).catch(function(e) {
        console.warn('[GlobalAudio] 自动播放被阻止:', e);
        showError('播放被浏览器阻止，请点击播放按钮手动播放');
        playBtn.innerHTML = '\u25B6';
        playBtn.classList.remove('playing');
    });
}

function togglePlay() {
    if (!audioEl || !audioEl.src) return;
    if (audioEl.paused) {
        audioEl.play();
        playBtn.innerHTML = '\u275A\u275A';
        playBtn.classList.add('playing');
        startProgressUpdate();
    } else {
        audioEl.pause();
        playBtn.innerHTML = '\u25B6';
        playBtn.classList.remove('playing');
        stopProgressUpdate();
    }
}

function decodeAudioToWaveform(src) {
    waveformPeaks = null;
    waveContainer.classList.remove('visible');
    if (!window.AudioContext && !window.webkitAudioContext) return;
    // Reuse existing AudioContext if available and not closed
    if (!audioCtx || audioCtx.state === 'closed') {
        audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    }
    if (audioCtx.state === 'suspended') audioCtx.resume();

    // Disconnect old analyser before loading new audio (source is reused)
    if (analyser) {
        try { analyser.disconnect(); } catch(e) {}
        analyser = null;
    }
    if (source) {
        try { source.disconnect(); } catch(e) {}
    }

    fetch(src)
        .then(function(res) { return res.arrayBuffer(); })
        .then(function(buffer) {
            audioCtx.decodeAudioData(buffer, function(decodedBuffer) {
                var rawData = decodedBuffer.getChannelData(0);
                var steps = Math.min(300, rawData.length);
                var stepSize = Math.floor(rawData.length / steps);
                waveformPeaks = [];
                for (var i = 0; i < steps; i++) {
                    var max = 0;
                    for (var j = 0; j < stepSize; j++) {
                        var val = Math.abs(rawData[i * stepSize + j]);
                        if (val > max) max = val;
                    }
                    waveformPeaks.push(max);
                }
                waveContainer.classList.add('visible');
                drawStaticWaveform();
                initLiveAnalyser();
            }, function(e) {
                console.warn('[Waveform] 解码失败:', e);
            });
        })
        .catch(function(err) {
            console.error('[Waveform] 获取音频数据失败:', err);
        });
}

function drawStaticWaveform() {
    if (!waveformCanvas || !waveformPeaks) return;
    var ctx = waveformCanvas.getContext('2d');
    var w = waveformCanvas.width;
    var h = waveformCanvas.height;
    var colors = getWaveformColors();
    ctx.clearRect(0, 0, w, h);

    var barWidth = w / waveformPeaks.length;
    for (var i = 0; i < waveformPeaks.length; i++) {
        var amp = waveformPeaks[i];
        var barH = Math.max(1, amp * h * 0.9);
        ctx.fillStyle = colors.staticFill;
        ctx.fillRect(i * barWidth, (h - barH) / 2, barWidth - 0.5, barH);
    }
}

function initLiveAnalyser() {
    if (!audioCtx || !audioEl) return;
    // Disconnect old analyser before reconnecting
    if (analyser) {
        try { analyser.disconnect(); } catch(e) {}
        analyser = null;
    }
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 256;
    // createMediaElementSource can only be called once per audio element
    if (!source) {
        try {
            source = audioCtx.createMediaElementSource(audioEl);
        } catch(e) {
            console.warn('[Waveform] 分析器连接失败:', e);
            return;
        }
    }
    try {
        source.connect(analyser);
        analyser.connect(audioCtx.destination);
    } catch(e) {
        console.warn('[Waveform] 分析器连接失败:', e);
    }
    bufferLength = analyser.frequencyBinCount;
    dataArray = new Uint8Array(bufferLength);
}

function drawWaveformLive() {
    if (!analyser || !waveformCanvas || !waveformPeaks) return;
    var ctx = waveformCanvas.getContext('2d');
    var w = waveformCanvas.width;
    var h = waveformCanvas.height;

    function render() {
        animFrameId = requestAnimationFrame(render);
        analyser.getByteFrequencyData(dataArray);
        var colors = getWaveformColors();

        ctx.clearRect(0, 0, w, h);
        var barWidth = w / waveformPeaks.length;

        for (var i = 0; i < waveformPeaks.length; i++) {
            var amp = waveformPeaks[i];
            var barH = Math.max(1, amp * h * 0.9);
            ctx.fillStyle = colors.bgBar;
            ctx.fillRect(i * barWidth, (h - barH) / 2, barWidth - 0.5, barH);
        }

        var liveBars = Math.min(bufferLength, waveformPeaks.length);
        for (var i = 0; i < liveBars; i++) {
            var liveAmp = dataArray[i] / 255;
            var barH = Math.max(1, liveAmp * h * 0.95);
            var gradient = ctx.createLinearGradient(0, (h - barH) / 2, 0, (h + barH) / 2);
            gradient.addColorStop(0, colors.gradientStart);
            gradient.addColorStop(0.5, colors.gradientMid);
            gradient.addColorStop(1, colors.gradientEnd);
            ctx.fillStyle = gradient;
            ctx.fillRect(i * barWidth, (h - barH) / 2, barWidth - 0.5, barH);
        }

        if (audioEl.duration) {
            var playPct = audioEl.currentTime / audioEl.duration;
            var playX = playPct * w;
            ctx.fillStyle = colors.playhead;
            ctx.fillRect(playX - 1, 0, 2, h);
        }

        if (clipMode && clipStart !== null && clipEnd !== null) {
            var x1 = Math.min(clipStart, clipEnd) * w;
            var x2 = Math.max(clipStart, clipEnd) * w;
            ctx.fillStyle = colors.clipFill;
            ctx.fillRect(x1, 0, x2 - x1, h);
            ctx.strokeStyle = colors.clipStroke;
            ctx.lineWidth = 1;
            ctx.strokeRect(x1, 0, x2 - x1, h);
        }
    }
    render();
}

function toggleClipMode() {
    if (clipMode) {
        exitClipMode();
    } else {
        enterClipMode();
    }
}

function enterClipMode() {
    clipMode = true;
    clipStart = null;
    clipEnd = null;
    clipBtn.classList.add('gap-clip-active');

    if (waveformCanvas) {
        var isDragging = false;
        waveformCanvas._clipMouseDown = function(e) {
            isDragging = true;
            var rect = waveformCanvas.getBoundingClientRect();
            var clientX = e.clientX || (e.touches && e.touches[0] ? e.touches[0].clientX : 0);
            var pct = (clientX - rect.left) / rect.width;
            clipStart = pct;
            clipEnd = pct;
        };
        waveformCanvas._clipMouseMove = function(e) {
            if (!isDragging) return;
            var rect = waveformCanvas.getBoundingClientRect();
            var clientX = e.clientX || (e.touches && e.touches[0] ? e.touches[0].clientX : 0);
            var pct = (clientX - rect.left) / rect.width;
            if (pct < 0) pct = 0;
            if (pct > 1) pct = 1;
            clipEnd = pct;
        };
        waveformCanvas._clipMouseUp = function(e) {
            isDragging = false;
            if (clipStart !== null && clipEnd !== null) {
                var t1 = Math.min(clipStart, clipEnd) * audioEl.duration;
                var t2 = Math.max(clipStart, clipEnd) * audioEl.duration;
                if (t2 - t1 < 0.1) {
                    clipStart = null;
                    clipEnd = null;
                    return;
                }
                showClipConfirm(t1, t2);
            }
        };
        waveformCanvas.addEventListener('mousedown', waveformCanvas._clipMouseDown);
        waveformCanvas.addEventListener('mousemove', waveformCanvas._clipMouseMove);
        waveformCanvas.addEventListener('mouseup', waveformCanvas._clipMouseUp);
        waveformCanvas._clipTouchStart = function(e) { e.preventDefault(); waveformCanvas._clipMouseDown(e); };
        waveformCanvas._clipTouchMove = function(e) { e.preventDefault(); waveformCanvas._clipMouseMove(e); };
        waveformCanvas._clipTouchEnd = function(e) { waveformCanvas._clipMouseUp(e); };
        waveformCanvas.addEventListener('touchstart', waveformCanvas._clipTouchStart, { passive: false });
        waveformCanvas.addEventListener('touchmove', waveformCanvas._clipTouchMove, { passive: false });
        waveformCanvas.addEventListener('touchend', waveformCanvas._clipTouchEnd);
    }
}

function exitClipMode() {
    clipMode = false;
    clipStart = null;
    clipEnd = null;
    clipBtn.classList.remove('gap-clip-active');
    if (waveformCanvas) {
        if (waveformCanvas._clipMouseDown) waveformCanvas.removeEventListener('mousedown', waveformCanvas._clipMouseDown);
        if (waveformCanvas._clipMouseMove) waveformCanvas.removeEventListener('mousemove', waveformCanvas._clipMouseMove);
        if (waveformCanvas._clipMouseUp) waveformCanvas.removeEventListener('mouseup', waveformCanvas._clipMouseUp);
        if (waveformCanvas._clipTouchStart) waveformCanvas.removeEventListener('touchstart', waveformCanvas._clipTouchStart);
        if (waveformCanvas._clipTouchMove) waveformCanvas.removeEventListener('touchmove', waveformCanvas._clipTouchMove);
        if (waveformCanvas._clipTouchEnd) waveformCanvas.removeEventListener('touchend', waveformCanvas._clipTouchEnd);
    }
    if (clipOverlay) { clipOverlay.remove(); clipOverlay = null; }
}

function showClipConfirm(t1, t2) {
    var existing = document.getElementById('gap-clip-confirm');
    if (existing) existing.remove();

    var overlay = document.createElement('div');
    overlay.id = 'gap-clip-confirm';
    var i18nClipTitle = (document.getElementById('gap-i18n-clip-title') || {}).textContent || '截取片段';
    var i18nExport = (document.getElementById('gap-i18n-export') || {}).textContent || '导出';
    var i18nCancel = (document.getElementById('gap-i18n-cancel') || {}).textContent || '取消';
    overlay.innerHTML = '<div class="gap-clip-popup"><div class="gap-clip-title">&#9986; ' + i18nClipTitle + '</div><div class="gap-clip-time">' + formatTime(t1) + ' - ' + formatTime(t2) + '</div><div class="gap-clip-actions"><button class="gap-clip-action-btn" onclick="gapExportClip(' + t1.toFixed(2) + ',' + t2.toFixed(2) + ')">&#128190; ' + i18nExport + '</button><button class="gap-clip-action-btn gap-clip-cancel" onclick="document.getElementById(\'gap-clip-confirm\').remove()">&#10005; ' + i18nCancel + '</button></div></div>';
    overlay.style.cssText = 'position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);z-index:10000;';
    document.body.appendChild(overlay);
    clipOverlay = overlay;
}

function gapExportClip(start, end) {
    if (!audioEl || !audioEl.src) return;
    var duration = end - start;
    if (duration <= 0) return;

    var clipPopup = document.getElementById('gap-clip-confirm');
    if (clipPopup) {
        var actionsDiv = clipPopup.querySelector('.gap-clip-actions');
        if (actionsDiv) {
            actionsDiv.innerHTML = '<div style="color:var(--accent-success,#10B981);font-size:13px;">&#8987; ' + ((document.getElementById('gap-i18n-exporting') || {}).textContent || '导出中...') + '</div>';
        }
    }

    var xhr = new XMLHttpRequest();
    xhr.open('GET', audioEl.src);
    xhr.responseType = 'arraybuffer';
    xhr.onload = function() {
        var offCtx = new (window.OfflineAudioContext || window.webkitOfflineAudioContext)(1, Math.ceil(duration * 44100), 44100);
        offCtx.decodeAudioData(xhr.response, function(buffer) {
            var startSample = Math.floor(start * buffer.sampleRate);
            var endSample = Math.floor(end * buffer.sampleRate);
            if (startSample >= buffer.length) startSample = 0;
            if (endSample > buffer.length) endSample = buffer.length;
            var clipLen = endSample - startSample;
            var clipBuffer = offCtx.createBuffer(buffer.numberOfChannels, clipLen, buffer.sampleRate);
            for (var ch = 0; ch < buffer.numberOfChannels; ch++) {
                var srcData = buffer.getChannelData(ch);
                var dstData = clipBuffer.getChannelData(ch);
                for (var i = 0; i < clipLen; i++) {
                    dstData[i] = srcData[startSample + i];
                }
            }
            var src = offCtx.createBufferSource();
            src.buffer = clipBuffer;
            src.connect(offCtx.destination);
            src.start(0);
            offCtx.startRendering().then(function(rendered) {
                var wavBlob = audioBufferToWav(rendered);
                var url = URL.createObjectURL(wavBlob);
                var a = document.createElement('a');
                a.href = url;
                a.download = 'clip_' + formatTime(start).replace(':', '') + '_' + formatTime(end).replace(':', '') + '.wav';
                a.click();
                URL.revokeObjectURL(url);
                if (clipOverlay) { clipOverlay.remove(); clipOverlay = null; }
            });
        }, function(e) {
            console.warn('[Clip] 解码失败:', e);
            if (clipOverlay) { clipOverlay.remove(); clipOverlay = null; }
        });
    };
    xhr.send();
}

function audioBufferToWav(buffer) {
    var numCh = buffer.numberOfChannels;
    var sr = buffer.sampleRate;
    var len = buffer.length;
    var bytesPerSample = 2;
    var blockAlign = numCh * bytesPerSample;
    var dataSize = len * blockAlign;
    var headerSize = 44;
    var totalSize = headerSize + dataSize;
    var ab = new ArrayBuffer(totalSize);
    var view = new DataView(ab);

    function writeStr(offset, str) { for (var i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i)); }
    writeStr(0, 'RIFF');
    view.setUint32(4, totalSize - 8, true);
    writeStr(8, 'WAVE');
    writeStr(12, 'fmt ');
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, numCh, true);
    view.setUint32(24, sr, true);
    view.setUint32(28, sr * blockAlign, true);
    view.setUint16(32, blockAlign, true);
    view.setUint16(34, bytesPerSample * 8, true);
    writeStr(36, 'data');
    view.setUint32(40, dataSize, true);

    var channels = [];
    for (var ch = 0; ch < numCh; ch++) channels.push(buffer.getChannelData(ch));
    var offset = headerSize;
    for (var i = 0; i < len; i++) {
        for (var ch = 0; ch < numCh; ch++) {
            var sample = Math.max(-1, Math.min(1, channels[ch][i]));
            sample = sample < 0 ? sample * 0x8000 : sample * 0x7FFF;
            view.setInt16(offset, sample, true);
            offset += 2;
        }
    }
    return new Blob([ab], { type: 'audio/wav' });
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function() {
        initPlayer();
        // Expose element reference immediately after init
        if (window.globalAudioPlayer) {
            window.globalAudioPlayer.element = document.getElementById('global-audio-element');
        }
    });
} else {
    initPlayer();
    if (window.globalAudioPlayer) {
        window.globalAudioPlayer.element = document.getElementById('global-audio-element');
    }
}

// Expose to global scope - element will be updated after init
var globalPlayerObj = {
    play: playAudio,
    toggle: togglePlay,
    hide: function() { if (closeBtn) closeBtn.click(); },
    show: showPlayer,
    get element() { return document.getElementById('global-audio-element'); },
    set element(val) { /* no-op, getter handles it */ },
    isHidden: function() { return isHidden; },
    resetHidden: function() { isHidden = false; },
    exportClip: gapExportClip
};
// @deprecated 使用 TTSApp.audio 替代，此 window 挂载点将在未来版本移除
window.globalAudioPlayer = globalPlayerObj;
// @deprecated 使用 TTSApp.audio.exportClip 替代，此 window 挂载点将在未来版本移除
window.gapExportClip = gapExportClip;

// Expose module API
// @deprecated 使用 TTSApp.audio 替代，此 window 挂载点将在未来版本移除
window.GlobalAudioPlayer = {
    play: playAudio,
    toggle: togglePlay,
    hide: function() { if (closeBtn) closeBtn.click(); },
    show: showPlayer,
    isHidden: function() { return isHidden; },
    resetHidden: function() { isHidden = false; },
    exportClip: gapExportClip
};
})();
