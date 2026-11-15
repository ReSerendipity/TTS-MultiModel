/**
 * StreamingPlayback - 边收边播（Web Audio 队列 + 联合进度）
 *
 * 只依赖浏览器原生 AudioContext / Web Audio API，不引入任何第三方库。
 *
 * 背景：
 *   SSE 路由（POST /api/generate/streaming_sse）在 prefer_streaming=true 时
 *   每个推理步就下发一个 ``event: audio``（裸 base64 PCM16、单声道）。
 *   旧前端把这些块全部攒到 ``done`` 才换播放器，用户要等整段合成完才能听到声音。
 *
 * 本模块做两件事：
 *   1. StreamingAudioQueue：PCM16 块到达即转 AudioBuffer，按时间轴无缝排队播放；
 *      同时统计首块/首声延迟、真实断流次数与最大断流间隔，供实测取证。
 *   2. StreamingProgress：按「文本段 + 段内块」联合计算百分比。
 *      段权重 = 1/total_segments；段内进度 = 已收块数 / 预期块数（预期值取前序段
 *      块数的滑动均值，首段用 n/(n+8) 渐近估计），未收到 done 前封顶 99%。
 *      这样最后一段刚开始不会直接跳满 100%，也不会因为块数变多而提前冲顶。
 *
 * API:
 *   StreamingPlayback.supported
 *   StreamingPlayback.begin(opts) -> queue        // 会先停掉上一条队列
 *   StreamingPlayback.decodePCM16Base64(b64) -> Int16Array
 *   StreamingPlayback.createProgress(total) -> progress
 *   StreamingPlayback.getActive()
 */
(function () {
    'use strict';

    function nowMs() {
        return (window.performance && typeof performance.now === 'function')
            ? performance.now()
            : Date.now();
    }

    /**
     * 解码 SSE 的裸 base64 PCM16 块。
     * 后端 yield 的是 ``pcm_data.tobytes()``（int16 小端、交错单声道），
     * 因此字节数必须是偶数；奇数说明协议/数据损坏，直接抛错让调用方记录。
     */
    function decodePCM16Base64(b64) {
        if (!b64) throw new Error('empty audio chunk');
        var binary = atob(b64);
        if (binary.length % 2 !== 0) {
            throw new Error('PCM16 byte length is not even: ' + binary.length);
        }
        var bytes = new Uint8Array(binary.length);
        for (var i = 0; i < binary.length; i++) {
            bytes[i] = binary.charCodeAt(i);
        }
        return new Int16Array(bytes.buffer);
    }

    function formatSeconds(value, digits) {
        var n = Number(value);
        if (!isFinite(n) || n < 0) n = 0;
        return n.toFixed(digits === undefined ? 1 : digits);
    }

    /* ==================================================================
     * StreamingAudioQueue
     * ================================================================== */

    function StreamingAudioQueue(opts) {
        opts = opts || {};
        this.label = opts.label || 'stream';
        this.supported = !!(window.AudioContext || window.webkitAudioContext);
        this.sampleRate = opts.sampleRate || 48000;
        this._ctx = null;
        this._sources = [];
        this._lastSource = null;
        this._nextTime = 0;          // Web Audio 时间轴上下一块的起始时刻
        this._chunks = 0;
        this._samples = 0;
        this._audioSeconds = 0;
        this._underruns = 0;
        this._maxGapMs = 0;
        this._createdAt = nowMs();
        this._firstChunkAt = 0;
        this._firstSoundAt = 0;
        this._firstChunkOffsetMs = 0;
        this._lastChunkAt = 0;
        this._stopped = false;
        this._finished = false;
        this._endedAt = 0;
        this._onEndedCb = null;
        this._closeTimer = null;

        if (this.supported) {
            try {
                var Ctx = window.AudioContext || window.webkitAudioContext;
                this._ctx = new Ctx();
                // 用户点击「流式生成」即为手势上下文；这里再 resume 一次兜底
                // Safari/Chrome 把初始 context 挂在 suspended 的情况。
                if (this._ctx.state === 'suspended' && this._ctx.resume) {
                    this._ctx.resume().catch(function () { /* 忽略：无手势时交由后续点击恢复 */ });
                }
            } catch (e) {
                console.warn('[StreamingPlayback] AudioContext 创建失败，回退整段模式:', e);
                this._ctx = null;
                this.supported = false;
            }
        }
    }

    StreamingAudioQueue.prototype.setSampleRate = function (rate) {
        rate = Number(rate);
        if (isFinite(rate) && rate > 0) this.sampleRate = rate;
    };

    /**
     * 入队一个 PCM16 块并立即排上播放时间轴。
     * @param {Int16Array} pcm
     * @returns {boolean} 是否成功入队
     */
    StreamingAudioQueue.prototype.enqueue = function (pcm) {
        if (!this._ctx || this._stopped || this._finished || !pcm || !pcm.length) {
            return false;
        }

        var ctx = this._ctx;
        var count = pcm.length;
        var duration = count / this.sampleRate;

        try {
            var buffer = ctx.createBuffer(1, count, this.sampleRate);
            var data = buffer.getChannelData(0);
            for (var i = 0; i < count; i++) {
                data[i] = pcm[i] / 32768;
            }

            var source = ctx.createBufferSource();
            source.buffer = buffer;
            source.connect(ctx.destination);

            var ctxNow = ctx.currentTime;
            var startAt;
            var gapMs = 0;
            if (this._nextTime <= 0) {
                // 首块：留 40ms 给调度与音频管线启动，避免 start(0) 被吞。
                startAt = ctxNow + 0.04;
            } else if (this._nextTime < ctxNow + 0.015) {
                // 生成慢于播放：上一块已经播完，下一块才到 -> 真实断流。
                gapMs = (ctxNow - this._nextTime) * 1000;
                if (gapMs > 20 && this._chunks > 0) {
                    this._underruns += 1;
                    if (gapMs > this._maxGapMs) this._maxGapMs = gapMs;
                }
                startAt = ctxNow + 0.03;
            } else {
                startAt = this._nextTime;
            }

            source.start(startAt);
            this._nextTime = startAt + duration;

            var self = this;
            source.onended = function () {
                if (self._stopped || source !== self._lastSource) return;
                self._endedAt = nowMs();
                if (typeof self._onEndedCb === 'function') {
                    try { self._onEndedCb(self.stats()); } catch (e) { /* 回调异常不影响播放 */ }
                }
                self._maybeClose();
            };
            this._lastSource = source;
            this._sources.push(source);

            var now = nowMs();
            this._chunks += 1;
            this._samples += count;
            this._audioSeconds += duration;
            this._lastChunkAt = now;
            if (!this._firstChunkAt) {
                this._firstChunkAt = now;
                this._firstChunkOffsetMs = Math.max(0, (startAt - ctxNow) * 1000);
                this._firstSoundAt = now + this._firstChunkOffsetMs;
            }
            return true;
        } catch (e) {
            console.warn('[StreamingPlayback] 音频块入队失败:', e);
            return false;
        }
    };

    StreamingAudioQueue.prototype.stats = function () {
        var ctx = this._ctx;
        var remaining = 0;
        if (ctx && !this._stopped) {
            remaining = Math.max(0, this._nextTime - ctx.currentTime);
        }
        return {
            label: this.label,
            supported: !!ctx,
            chunks: this._chunks,
            samples: this._samples,
            audioSeconds: this._audioSeconds,
            firstChunkMs: this._firstChunkAt ? (this._firstChunkAt - this._createdAt) : null,
            firstSoundMs: this._firstSoundAt ? (this._firstSoundAt - this._createdAt) : null,
            firstChunkOffsetMs: this._firstChunkOffsetMs,
            underruns: this._underruns,
            maxGapMs: this._maxGapMs,
            remainingSeconds: remaining,
            finished: this._finished,
            ended: this._endedAt > 0,
            stopped: this._stopped
        };
    };

    /** 收到 done 后调用：不再接受新块，播完队列后自动关闭 AudioContext。 */
    StreamingAudioQueue.prototype.finish = function () {
        this._finished = true;
        this._maybeClose();
        return this.stats();
    };

    StreamingAudioQueue.prototype.onEnded = function (fn) {
        this._onEndedCb = fn;
    };

    StreamingAudioQueue.prototype._maybeClose = function () {
        // 只有收到 done（_finished）后才允许关闭上下文。
        // 否则“生成慢于播放”时，最后一块播完后 onended 会误判队列已空，
        // 把仍在等待下一块的 AudioContext 关掉，后续块全部变成
        // “context is closed” 警告（实测 2026-11-15 复现）。
        if (!this._finished || this._stopped) return;
        var self = this;
        if (!this._ctx || this._ctx.state === 'closed') return;
        if (this._closeTimer) clearTimeout(this._closeTimer);
        var remaining = Math.max(0, this._nextTime - this._ctx.currentTime);
        if (remaining <= 0.05) {
            this._closeTimer = setTimeout(function () {
                self._closeTimer = null;
                self._closeContext();
            }, 150);
        } else {
            // 队列还有积压：播完再关，避免 done 之后把正在播的尾部切掉。
            this._closeTimer = setTimeout(function () {
                self._closeTimer = null;
                self._maybeClose();
            }, Math.min(1000, (remaining + 0.15) * 1000));
        }
    };

    StreamingAudioQueue.prototype._closeContext = function () {
        if (!this._ctx || this._ctx.state === 'closed') return;
        try {
            var closing = this._ctx.close();
            if (closing && typeof closing.catch === 'function') closing.catch(function () {});
        } catch (e) { /* 已关闭 */ }
    };

    /** 立即停止播放并关闭上下文（出错 / 用户点回放 / 下一次生成时调用）。 */
    StreamingAudioQueue.prototype.stop = function () {
        if (this._stopped) return;
        this._stopped = true;
        this._finished = true;
        if (this._closeTimer) {
            clearTimeout(this._closeTimer);
            this._closeTimer = null;
        }
        for (var i = 0; i < this._sources.length; i++) {
            var s = this._sources[i];
            try { s.onended = null; } catch (e) { /* ignore */ }
            try { s.stop(0); } catch (e) { /* 尚未 start 的节点 stop 会抛 */ }
            try { s.disconnect(); } catch (e) { /* ignore */ }
        }
        this._sources = [];
        this._lastSource = null;
        this._closeContext();
        if (activeQueue === this) activeQueue = null;
    };

    /* ==================================================================
     * StreamingProgress ——「文本段 + 块」联合百分比
     * ================================================================== */

    /** 粗略估计朗读语速（字/秒），仅用于首段进度估计；首段完成后改用实测段时长。 */
    function estimateCharsPerSecond(text) {
        var s = String(text || '');
        if (!s) return 4.5;
        var cjk = (s.match(/[\u3400-\u9fff\uf900-\ufaff]/g) || []).length;
        var latin = (s.match(/[A-Za-z]/g) || []).length;
        var other = Math.max(0, s.length - cjk - latin);
        // 中文约 4.5 字/秒，英文约 13 字母/秒；标点/空格按半速计。
        var weighted = cjk / 4.5 + latin / 13.0 + other / 8.0;
        return s.length / Math.max(0.25, weighted);
    }

    function StreamingProgress(total, opts) {
        opts = opts || {};
        this.total = Math.max(1, Number(total) || 1);
        this.textLength = opts.text ? String(opts.text).length : 0;
        this.charsPerSec = Number(opts.charsPerSec) > 0
            ? Number(opts.charsPerSec)
            : estimateCharsPerSecond(opts.text || '');
        this.currentSegment = 0;
        this.chunksInSegment = 0;
        this.totalChunks = 0;
        this.currentSegmentSeconds = 0;
        this.completedChunkCounts = [];
        this.completedSeconds = [];
        this.done = false;
    }

    StreamingProgress.prototype.setTotal = function (total) {
        total = Number(total);
        if (isFinite(total) && total > 0) this.total = Math.max(1, Math.round(total));
    };

    StreamingProgress.prototype.getAvgCharsPerSegment = function () {
        if (!this.textLength) return 0;
        return this.textLength / Math.max(1, this.total);
    };

    /** 收到 event: progress（新的一段开始）。上一段的块数/实测时长作为后续段参考。 */
    StreamingProgress.prototype.startSegment = function (segment) {
        var seg = Number(segment);
        if (!isFinite(seg) || seg <= 0) return;
        if (seg > this.currentSegment) {
            if (this.currentSegment > 0) {
                this.completedChunkCounts.push(this.chunksInSegment);
                this.completedSeconds.push(this.currentSegmentSeconds);
                // 只保留最近 8 段，避免长文本数组无限增长。
                if (this.completedChunkCounts.length > 8) this.completedChunkCounts.shift();
                if (this.completedSeconds.length > 8) this.completedSeconds.shift();
            }
            this.currentSegment = seg;
            this.chunksInSegment = 0;
            this.currentSegmentSeconds = 0;
        }
    };

    StreamingProgress.prototype.addChunk = function (samples, sampleRate) {
        this.chunksInSegment += 1;
        this.totalChunks += 1;
        var sr = Number(sampleRate);
        if (!isFinite(sr) || sr <= 0) sr = 48000;
        if (samples && samples > 0) this.currentSegmentSeconds += Number(samples) / sr;
    };

    StreamingProgress.prototype._expectedSegments = function () {
        if (!this.completedChunkCounts.length) return 0;
        var sum = 0;
        for (var i = 0; i < this.completedChunkCounts.length; i++) sum += this.completedChunkCounts[i];
        return sum / this.completedChunkCounts.length;
    };

    /** 当前段预期时长（秒）：优先用已完成段的实测均值，否则按文本字数/语速估算。 */
    StreamingProgress.prototype._expectedSeconds = function () {
        if (this.completedSeconds.length) {
            var sum = 0;
            for (var i = 0; i < this.completedSeconds.length; i++) sum += this.completedSeconds[i];
            var avg = sum / this.completedSeconds.length;
            if (avg > 0.2) return avg;
        }
        var avgChars = this.getAvgCharsPerSegment();
        if (avgChars > 0 && this.charsPerSec > 0) return avgChars / this.charsPerSec;
        return 0;
    };

    StreamingProgress.prototype.percent = function () {
        if (this.done) return 100;
        var total = this.total;
        var completedSegments = Math.max(0, this.currentSegment - 1);
        var expectedSeconds = this._expectedSeconds();
        var intra;
        if (expectedSeconds > 0) {
            // 段内进度按“已收到的音频秒数”计算，块大小不均匀时也不会跳。
            intra = Math.min(0.985, this.currentSegmentSeconds / expectedSeconds);
        } else {
            // 无文本长度时的兜底：n/(n+8) 渐近，但首段会偏快，仅作最后保障。
            intra = this.chunksInSegment / (this.chunksInSegment + 8);
        }
        intra = Math.min(0.985, intra);
        var raw = ((completedSegments + intra) / total) * 100;
        // done 之前封顶 99：最后一段刚开始时是 (N-1)/N（例如 20 段 = 95%），
        // 段内音频进度会把它从 95% 缓慢推到 99%，不会提前跳满、也不会卡死在 95%。
        return Math.min(99, Math.max(0, Math.round(raw * 10) / 10));
    };

    StreamingProgress.prototype.markDone = function () {
        this.done = true;
    };

    StreamingProgress.prototype.snapshot = function () {
        return {
            total: this.total,
            segment: this.currentSegment,
            chunksInSegment: this.chunksInSegment,
            totalChunks: this.totalChunks,
            percent: this.percent(),
            done: this.done
        };
    };

    /* ==================================================================
     * 全局入口
     * ================================================================== */

    var activeQueue = null;

    function begin(opts) {
        if (activeQueue) activeQueue.stop();
        activeQueue = new StreamingAudioQueue(opts);
        return activeQueue;
    }

    function getActive() {
        return activeQueue;
    }

    window.StreamingPlayback = {
        supported: !!(window.AudioContext || window.webkitAudioContext),
        begin: begin,
        getActive: getActive,
        decodePCM16Base64: decodePCM16Base64,
        createProgress: function (total, opts) { return new StreamingProgress(total, opts); },
        formatSeconds: formatSeconds
    };
})();
