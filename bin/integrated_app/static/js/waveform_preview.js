/**
 * WaveformPreview - 音频波形预览组件
 * 使用 Canvas API 绘制波形缩略图，利用已有的 CSS 波形令牌
 */
;(function() {
  'use strict';

  const WaveformPreview = {
    defaults: {
      width: 280,
      height: 48,
      barWidth: 2,
      barGap: 1,
      barRadius: 1,
      color: null, // null = 使用 CSS 变量
      bgColor: null,
      progressColor: null,
      progress: 0,
      animated: false,
      hoverPreview: true
    },

    /**
     * 从音频文件绘制波形
     * @param {HTMLCanvasElement|string} canvas - Canvas 元素或选择器
     * @param {string} audioUrl - 音频文件 URL
     * @param {Object} options - 配置选项
     */
    drawFromAudio: async function(canvas, audioUrl, options = {}) {
      const opts = { ...this.defaults, ...options };
      const el = typeof canvas === 'string' ? document.querySelector(canvas) : canvas;
      if (!el) return;

      try {
        const response = await fetch(audioUrl);
        const arrayBuffer = await response.arrayBuffer();
        const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        const audioBuffer = await audioCtx.decodeAudioData(arrayBuffer);
        const channelData = audioBuffer.getChannelData(0);
        this._drawBars(el, channelData, opts);
        audioCtx.close();
      } catch (e) {
        console.warn('[WaveformPreview] Failed to decode audio:', e);
        this._drawPlaceholder(el, opts);
      }
    },

    /**
     * 从 Float32Array 数据绘制波形
     */
    drawFromData: function(canvas, data, options = {}) {
      const opts = { ...this.defaults, ...options };
      const el = typeof canvas === 'string' ? document.querySelector(canvas) : canvas;
      if (!el) return;
      this._drawBars(el, data, opts);
    },

    /**
     * 绘制占位波形（静态随机）
     */
    drawPlaceholder: function(canvas, options = {}) {
      const opts = { ...this.defaults, ...options };
      const el = typeof canvas === 'string' ? document.querySelector(canvas) : canvas;
      if (!el) return;
      const len = Math.floor(opts.width / (opts.barWidth + opts.barGap));
      const data = new Float32Array(len);
      for (let i = 0; i < len; i++) {
        data[i] = 0.1 + Math.random() * 0.5;
      }
      this._drawBars(el, data, opts);
    },

    /**
     * 内部: 绘制柱状波形
     */
    _drawBars: function(canvas, channelData, opts) {
      const ctx = canvas.getContext('2d');
      const dpr = window.devicePixelRatio || 1;
      canvas.width = opts.width * dpr;
      canvas.height = opts.height * dpr;
      canvas.style.width = opts.width + 'px';
      canvas.style.height = opts.height + 'px';
      ctx.scale(dpr, dpr);

      // 获取 CSS 变量颜色
      const style = getComputedStyle(canvas.closest('[data-theme]') || document.documentElement);
      const barColor = opts.color || style.getPropertyValue('--waveform-static-fill').trim() || '#0891B2';
      const bgBarColor = opts.bgColor || style.getPropertyValue('--waveform-bg-bar').trim() || 'rgba(8,145,178,0.15)';
      const progressClr = opts.progressColor || style.getPropertyValue('--waveform-progress-fill').trim() || '#22D3EE';

      // 清除
      ctx.clearRect(0, 0, opts.width, opts.height);

      const totalBars = Math.floor(opts.width / (opts.barWidth + opts.barGap));
      const samplesPerBar = Math.floor(channelData.length / totalBars);
      const centerY = opts.height / 2;

      for (let i = 0; i < totalBars; i++) {
        let sum = 0;
        const start = i * samplesPerBar;
        for (let j = start; j < start + samplesPerBar && j < channelData.length; j++) {
          sum += Math.abs(channelData[j]);
        }
        const avg = sum / samplesPerBar;
        const barHeight = Math.max(2, avg * opts.height * 0.8);
        const x = i * (opts.barWidth + opts.barGap);
        const y = centerY - barHeight / 2;

        // 进度条之前的用高亮色
        const progressX = opts.progress * opts.width;
        if (x < progressX) {
          ctx.fillStyle = progressClr;
        } else {
          ctx.fillStyle = barColor;
        }

        // 圆角矩形
        const r = Math.min(opts.barRadius, opts.barWidth / 2, barHeight / 2);
        ctx.beginPath();
        ctx.moveTo(x + r, y);
        ctx.lineTo(x + opts.barWidth - r, y);
        ctx.quadraticCurveTo(x + opts.barWidth, y, x + opts.barWidth, y + r);
        ctx.lineTo(x + opts.barWidth, y + barHeight - r);
        ctx.quadraticCurveTo(x + opts.barWidth, y + barHeight, x + opts.barWidth - r, y + barHeight);
        ctx.lineTo(x + r, y + barHeight);
        ctx.quadraticCurveTo(x, y + barHeight, x, y + barHeight - r);
        ctx.lineTo(x, y + r);
        ctx.quadraticCurveTo(x, y, x + r, y);
        ctx.closePath();
        ctx.fill();
      }
    },

    /**
     * 内部: 绘制占位符
     */
    _drawPlaceholder: function(canvas, opts) {
      const ctx = canvas.getContext('2d');
      const dpr = window.devicePixelRatio || 1;
      canvas.width = opts.width * dpr;
      canvas.height = opts.height * dpr;
      canvas.style.width = opts.width + 'px';
      canvas.style.height = opts.height + 'px';
      ctx.scale(dpr, dpr);
      ctx.clearRect(0, 0, opts.width, opts.height);

      const style = getComputedStyle(canvas.closest('[data-theme]') || document.documentElement);
      const color = style.getPropertyValue('--text-disabled').trim() || '#D1D5DB';

      const totalBars = Math.floor(opts.width / (opts.barWidth + opts.barGap));
      const centerY = opts.height / 2;
      ctx.fillStyle = color;

      for (let i = 0; i < totalBars; i++) {
        const barHeight = 2 + Math.random() * 4;
        const x = i * (opts.barWidth + opts.barGap);
        const y = centerY - barHeight / 2;
        ctx.fillRect(x, y, opts.barWidth, barHeight);
      }
    }
  };

  // 挂载到全局命名空间
  window.WaveformPreview = WaveformPreview;

  // 自动初始化页面中的波形画布
  document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('[data-waveform-src]').forEach(function(canvas) {
      const src = canvas.getAttribute('data-waveform-src');
      const width = parseInt(canvas.getAttribute('data-waveform-width')) || 280;
      const height = parseInt(canvas.getAttribute('data-waveform-height')) || 48;
      WaveformPreview.drawFromAudio(canvas, src, { width: width, height: height });
    });

    document.querySelectorAll('[data-waveform-placeholder]').forEach(function(canvas) {
      const width = parseInt(canvas.getAttribute('data-waveform-width')) || 280;
      const height = parseInt(canvas.getAttribute('data-waveform-height')) || 48;
      WaveformPreview.drawPlaceholder(canvas, { width: width, height: height });
    });
  });
})();
