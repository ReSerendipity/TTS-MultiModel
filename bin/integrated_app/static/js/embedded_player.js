/**
 * EmbeddedPlayer - 结果卡内嵌音频播放器（波形 + 可拖动进度条）
 *
 * 任何包含 `data-embedded-player` 属性的容器（带 `data-src` 音频 URL）都会
 * 被自动初始化。典型结构（由 routes/generate/utils.py 的 _success_html /
 * _partial_success_html 注入）：
 *
 *   <div class="ep-player" data-embedded-player data-src="/api/audio/xxx.wav">
 *     <button class="ep-play">...</button>
 *     <div class="ep-body">
 *       <canvas class="ep-wave"></canvas>
 *       <div class="ep-bar"><div class="ep-bar-fill"></div></div>
 *       <div class="ep-time"><span class="ep-time-cur">00:00</span><span class="ep-time-dur">00:00</span></div>
 *     </div>
 *   </div>
 *
 * 初始化方式（多路兜底，保证任何换入方式都能生效）：
 *   1. DOMContentLoaded 初始扫描
 *   2. document 级 htmx:afterSettle 监听（htmx 原生事件会冒泡到 document）
 *   3. MutationObserver 观察 body（覆盖 submitWithPersona / Reprocess 等
 *      手动 `innerHTML = html` 赋值场景——innerHTML 插入不会执行内联 script，
 *      也不会冒泡 htmx 事件，只有 MutationObserver 能捕获）
 *
 * 设计说明：
 * - 本组件完全自包含（内部 new Audio()），不依赖全局底部播放器
 *   (window.globalAudioPlayer)。即使全局播放器因缓存等原因未加载，
 *   结果卡内的播放器也始终可用。
 * - 波形：fetch 音频 → Web Audio API 解码 → 取峰值画柱状波峰波谷；
 *   已播放部分用高亮色，播放中绘制白色播放头。
 * - 进度条：点击 / 拖拽 / 触摸均可 seek，拖动时不与播放进度更新打架。
 */
(function () {
  'use strict';

  var ICON_PLAY = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>';
  var ICON_PAUSE = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M7 5h3.2v14H7zM13.8 5H17v14h-3.2z"/></svg>';

  var _colors = null;

  function getColors() {
    if (_colors) return _colors;
    var s = getComputedStyle(document.documentElement);
    _colors = {
      wave: s.getPropertyValue('--waveform-static-fill').trim() || 'rgba(139,92,246,0.45)',
      wavePlayed: s.getPropertyValue('--waveform-progress-fill').trim() || 'rgba(139,92,246,0.95)',
      playhead: 'rgba(255,255,255,0.9)',
      barFill: s.getPropertyValue('--primary').trim() || '#6B5CE7'
    };
    return _colors;
  }

  function formatTime(sec) {
    if (isNaN(sec) || sec < 0 || !isFinite(sec)) sec = 0;
    var m = Math.floor(sec / 60);
    var s = Math.floor(sec % 60);
    return (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s;
  }

  /**
   * 初始化单个内嵌播放器容器（幂等：data-ep-ready 标记后跳过）。
   */
  function initPlayer(root) {
    if (!root || root.nodeType !== 1) return;
    if (root.dataset.epReady === '1') return;

    var playBtn = root.querySelector('.ep-play');
    var wave = root.querySelector('canvas.ep-wave');
    var bar = root.querySelector('.ep-bar');
    var fill = root.querySelector('.ep-bar-fill');
    var curEl = root.querySelector('.ep-time-cur');
    var durEl = root.querySelector('.ep-time-dur');
    var src = root.getAttribute('data-src');

    if (!src || !playBtn || !wave) return;

    var audio = new Audio(src);
    audio.preload = 'metadata';
    var peaks = null;
    var dragging = false;
    var resizeTimer = null;

    function setIcon(playing) {
      playBtn.innerHTML = playing ? ICON_PAUSE : ICON_PLAY;
      playBtn.classList.toggle('ep-playing', playing);
      playBtn.setAttribute('aria-label', playing ? '暂停' : '播放');
    }
    setIcon(false);

    // ---- 波形绘制 ----
    function drawWave() {
      if (!peaks) return;
      var w = wave.clientWidth || 280;
      var h = wave.clientHeight || 44;
      if (w <= 0 || h <= 0) return;
      var dpr = window.devicePixelRatio || 1;
      if (wave.width !== Math.floor(w * dpr) || wave.height !== Math.floor(h * dpr)) {
        wave.width = Math.floor(w * dpr);
        wave.height = Math.floor(h * dpr);
      }
      var ctx = wave.getContext('2d');
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      var colors = getColors();
      var bw = w / peaks.length;
      var played = audio.duration ? audio.currentTime / audio.duration : 0;
      for (var i = 0; i < peaks.length; i++) {
        var amp = peaks[i];
        var bh = Math.max(2, amp * (h - 6));
        var x = i * bw;
        var mid = (h - bh) / 2;
        ctx.fillStyle = (i / peaks.length) <= played ? colors.wavePlayed : colors.wave;
        if (ctx.roundRect) {
          ctx.beginPath();
          ctx.roundRect(x, mid, Math.max(bw - 1, 0.5), bh, Math.min(2, bw / 2));
          ctx.fill();
        } else {
          ctx.fillRect(x, mid, Math.max(bw - 1, 0.5), bh);
        }
      }
      // 播放头
      if (played > 0 && played < 1) {
        var px = played * w;
        ctx.fillStyle = colors.playhead;
        ctx.fillRect(px - 0.5, 0, 1, h);
      }
    }

    // ---- 解码波形 ----
    function decodeWaveform() {
      fetch(src).then(function (res) { return res.arrayBuffer(); }).then(function (buf) {
        var ac = new (window.AudioContext || window.webkitAudioContext)();
        ac.decodeAudioData(buf, function (decoded) {
          var ch = decoded.getChannelData(0);
          var steps = 160;
          var stepSize = Math.max(1, Math.floor(ch.length / steps));
          peaks = new Array(steps);
          for (var i = 0; i < steps; i++) {
            var m = 0;
            for (var j = i * stepSize; j < (i + 1) * stepSize && j < ch.length; j++) {
              var v = Math.abs(ch[j]);
              if (v > m) m = v;
            }
            peaks[i] = m;
          }
          drawWave();
          if (durEl && isFinite(decoded.duration)) durEl.textContent = formatTime(decoded.duration);
        }, function () {
          /* 解码失败（如非标准音频），仅保留进度条能力 */
        });
        try { ac.close(); } catch (e) {}
      }).catch(function () {
        /* fetch 失败（网络/跨域），仅保留进度条能力 */
      });
    }

    // ---- 播放控制 ----
    playBtn.addEventListener('click', function () {
      if (audio.paused) {
        audio.play().then(function () {
          setIcon(true);
        }).catch(function () {
          setIcon(false);
        });
      } else {
        audio.pause();
        setIcon(false);
      }
    });

    audio.addEventListener('play', function () { setIcon(true); });
    audio.addEventListener('pause', function () { setIcon(false); });
    audio.addEventListener('ended', function () { setIcon(false); });
    audio.addEventListener('loadedmetadata', function () {
      if (durEl) durEl.textContent = formatTime(audio.duration);
    });
    audio.addEventListener('timeupdate', function () {
      var pct = audio.duration ? audio.currentTime / audio.duration : 0;
      if (fill) fill.style.width = (pct * 100) + '%';
      if (curEl) curEl.textContent = formatTime(audio.currentTime);
      drawWave();
    });

    // ---- 进度条 seek（点击 + 拖拽 + 触摸） ----
    function seekFromClientX(clientX) {
      var rect = bar.getBoundingClientRect();
      if (!rect.width) return;
      var pct = (clientX - rect.left) / rect.width;
      pct = Math.max(0, Math.min(1, pct));
      if (audio.duration) {
        audio.currentTime = pct * audio.duration;
        if (fill) fill.style.width = (pct * 100) + '%';
        if (curEl) curEl.textContent = formatTime(audio.currentTime);
        drawWave();
      }
    }

    var seekArea = bar || wave;
    if (seekArea) {
      seekArea.addEventListener('mousedown', function (e) {
        dragging = true;
        seekFromClientX(e.clientX);
        e.preventDefault();
      });
      document.addEventListener('mousemove', function (e) {
        if (dragging) seekFromClientX(e.clientX);
      });
      document.addEventListener('mouseup', function () { dragging = false; });

      seekArea.addEventListener('touchstart', function (e) {
        if (e.touches && e.touches[0]) {
          dragging = true;
          seekFromClientX(e.touches[0].clientX);
        }
        e.preventDefault();
      }, { passive: false });
      document.addEventListener('touchmove', function (e) {
        if (dragging && e.touches && e.touches[0]) seekFromClientX(e.touches[0].clientX);
      }, { passive: false });
      document.addEventListener('touchend', function () { dragging = false; });
    }

    // 波形区域点击也可 seek
    if (wave && wave !== seekArea) {
      wave.addEventListener('mousedown', function (e) {
        dragging = true;
        seekFromClientX(e.clientX);
        e.preventDefault();
      });
    }

    // 窗口 resize 防抖重绘
    window.addEventListener('resize', function () {
      if (resizeTimer) clearTimeout(resizeTimer);
      resizeTimer = setTimeout(drawWave, 120);
    });

    root.dataset.epReady = '1';
    root._epAudio = audio;
    decodeWaveform();
  }

  function scan() {
    var nodes = document.querySelectorAll('[data-embedded-player]:not([data-ep-ready])');
    for (var i = 0; i < nodes.length; i++) initPlayer(nodes[i]);
  }

  // 1) 初始扫描
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', scan);
  } else {
    scan();
  }

  // 2) htmx 原生事件（冒泡到 document）
  document.addEventListener('htmx:afterSettle', scan);

  // 3) MutationObserver 兜底（innerHTML 手动赋值 / 动态换入）
  if (window.MutationObserver) {
    var mo = new MutationObserver(function (muts) {
      var need = false;
      for (var i = 0; i < muts.length && !need; i++) {
        var added = muts[i].addedNodes;
        for (var j = 0; j < added.length; j++) {
          var n = added[j];
          if (n.nodeType === 1 && n.matches && (n.matches('[data-embedded-player]') || n.querySelector('[data-embedded-player]'))) {
            need = true;
            break;
          }
        }
      }
      if (need) scan();
    });
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', function () {
        if (document.body) mo.observe(document.body, { childList: true, subtree: true });
      });
    } else if (document.body) {
      mo.observe(document.body, { childList: true, subtree: true });
    }
  }

  /**
   * 生成内嵌播放器 HTML 字符串（与服务端 _EMBEDDED_PLAYER_HTML 结构一致）。
   * 供前端动态换入（如流式生成完成）时使用，避免各页面手写重复结构。
   * @param {string} src - 音频 URL（绝对路径或 blob URL）
   * @returns {string} 内嵌播放器 HTML
   */
  function playerHtml(src) {
    return '<div class="ep-player" data-embedded-player data-src="' + src + '">'
      + '<button type="button" class="ep-play" title="播放/暂停" aria-label="播放/暂停">' + ICON_PLAY + '</button>'
      + '<div class="ep-body"><canvas class="ep-wave" height="44" aria-hidden="true"></canvas>'
      + '<div class="ep-bar"><div class="ep-bar-fill"></div></div>'
      + '<div class="ep-time"><span class="ep-time-cur">00:00</span><span class="ep-time-dur">00:00</span></div>'
      + '</div></div>';
  }

  window.EmbeddedPlayer = { init: initPlayer, scan: scan, html: playerHtml };
})();
