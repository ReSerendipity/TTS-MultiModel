/**
 * CommandPalette - 增强版命令面板 (Ctrl+K)
 * 参考 Linear / Raycast 的命令面板设计
 * 功能：页面导航 / 主题切换 / 引擎切换 / 模型管理 / 语言切换 / 全屏
 */
;(function() {
  'use strict';

  const CommandPalette = {
    overlay: null,
    input: null,
    resultsContainer: null,
    items: [],
    activeIndex: -1,
    isOpen: false,

    // 默认命令注册
    commands: [],

    init: function() {
      this._createDOM();
      this._bindKeyboard();
      this._registerDefaults();
    },

    register: function(cmd) {
      this.commands.push({
        id: cmd.id,
        label: cmd.label,
        description: cmd.description || '',
        category: cmd.category || '',
        icon: cmd.icon || '',
        action: cmd.action || function() {},
        shortcut: cmd.shortcut || ''
      });
    },

    open: function() {
      if (!this.overlay) return;
      this.isOpen = true;
      this.overlay.classList.add('active');
      this.input.value = '';
      this.activeIndex = -1;
      this._renderResults('');
      var self = this;
      setTimeout(function() { self.input.focus(); }, 50);
      document.body.style.overflow = 'hidden';
    },

    close: function() {
      if (!this.overlay) return;
      this.isOpen = false;
      this.overlay.classList.remove('active');
      document.body.style.overflow = '';
    },

    toggle: function() {
      this.isOpen ? this.close() : this.open();
    },

    _createDOM: function() {
      if (document.querySelector('.command-palette-overlay')) {
        this.overlay = document.querySelector('.command-palette-overlay');
        this.input = this.overlay.querySelector('.command-palette-input');
        this.resultsContainer = this.overlay.querySelector('.command-palette-results');
        return;
      }

      var overlay = document.createElement('div');
      overlay.className = 'command-palette-overlay';
      overlay.innerHTML = [
        '<div class="command-palette">',
        '  <div class="command-palette-input-wrap">',
        '    <svg class="command-palette-search-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>',
        '    <input type="text" class="command-palette-input" placeholder="输入命令或搜索页面..." autocomplete="off" spellcheck="false">',
        '    <kbd class="command-palette-kbd-shortcut">ESC</kbd>',
        '  </div>',
        '  <div class="command-palette-results">',
        '    <div class="command-palette-empty">',
        '      <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" opacity="0.3"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>',
        '      <span>开始输入以搜索命令...</span>',
        '    </div>',
        '  </div>',
        '  <div class="command-palette-footer">',
        '    <span class="command-palette-footer-hint"><kbd>↑↓</kbd> 导航 <kbd>↵</kbd> 选择 <kbd>ESC</kbd> 关闭</span>',
        '    <span class="command-palette-footer-count" id="cmd-count"></span>',
        '  </div>',
        '</div>'
      ].join('\n');
      document.body.appendChild(overlay);

      this.overlay = overlay;
      this.input = overlay.querySelector('.command-palette-input');
      this.resultsContainer = overlay.querySelector('.command-palette-results');

      // 点击背景关闭
      overlay.addEventListener('click', function(e) {
        if (e.target === overlay) this.close();
      }.bind(this));

      // 输入过滤
      this.input.addEventListener('input', function() {
        this._renderResults(this.input.value);
      }.bind(this));
    },

    _bindKeyboard: function() {
      document.addEventListener('keydown', function(e) {
        // Ctrl+K 或 Cmd+K 切换
        if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
          e.preventDefault();
          this.toggle();
          return;
        }

        if (!this.isOpen) return;

        switch (e.key) {
          case 'Escape':
            e.preventDefault();
            this.close();
            break;
          case 'ArrowDown':
            e.preventDefault();
            this.activeIndex = Math.min(this.activeIndex + 1, this.items.length - 1);
            this._updateActive();
            break;
          case 'ArrowUp':
            e.preventDefault();
            this.activeIndex = Math.max(this.activeIndex - 1, 0);
            this._updateActive();
            break;
          case 'Enter':
            e.preventDefault();
            if (this.items[this.activeIndex]) {
              this.items[this.activeIndex].action();
              this.close();
            }
            break;
        }
      }.bind(this));
    },

    _registerDefaults: function() {
      var self = this;

      // --- 页面导航类别 ---
      var navPages = [
        { id: 'nav-voice-design', label: '声音设计', tab: 'voice_design', icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>' },
        { id: 'nav-voice-clone', label: '语音克隆', tab: 'voice_clone', icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" x2="12" y1="19" y2="22"/></svg>' },
        { id: 'nav-ultimate-clone', label: '极致克隆', tab: 'ultimate_clone', icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>' },
        { id: 'nav-script', label: '剧本工坊', tab: 'script', icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" x2="8" y1="13" y2="13"/><line x1="16" x2="8" y1="17" y2="17"/></svg>' },
        { id: 'nav-history', label: '历史记录', tab: 'history', icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>' },
        { id: 'nav-persona', label: '音色库', tab: 'persona', icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>' },
        { id: 'nav-lora', label: 'LoRA 管理', tab: 'lora', icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>' },
        { id: 'nav-lora-training', label: 'LoRA 训练', tab: 'lora_training', icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>' },
        { id: 'nav-indextts2-clone', label: 'IndexTTS 克隆', tab: 'indextts2_clone', icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>' },
        { id: 'nav-indextts2-emotion', label: 'IndexTTS 情感', tab: 'indextts2_emotion', icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" x2="9.01" y1="9" y2="9"/><line x1="15" x2="15.01" y1="9" y2="9"/></svg>' },
        { id: 'nav-indextts2-duration', label: 'IndexTTS 时长', tab: 'indextts2_duration', icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>' },
        { id: 'nav-indextts20-clone', label: 'IndexTTS 2.0 克隆', tab: 'indextts20_clone', icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>' },
        { id: 'nav-indextts20-emotion', label: 'IndexTTS 2.0 情感', tab: 'indextts20_emotion', icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" x2="9.01" y1="9" y2="9"/><line x1="15" x2="15.01" y1="9" y2="9"/></svg>' },
        { id: 'nav-settings', label: '设置', tab: 'settings', icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>' }
      ];

      navPages.forEach(function(p) {
        self.register({
          id: p.id,
          label: p.label,
          description: '跳转到 ' + p.label + ' 页面',
          category: '导航',
          icon: p.icon,
          action: function() {
            // 通过侧边栏激活标签页
            if (window.TTSApp && window.TTSApp.sidebar) {
              window.TTSApp.sidebar.activateTab(p.tab);
            }
          }
        });
      });

      // --- 设置操作类别 ---
      self.register({
        id: 'toggle-theme',
        label: '切换主题',
        description: '在浅色和深色模式之间切换',
        category: '设置',
        icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>',
        shortcut: 'T',
        action: function() {
          var html = document.documentElement;
          var isDark = html.classList.contains('dark');
          html.classList.toggle('dark');
          localStorage.setItem('tts-theme', isDark ? 'light' : 'dark');
        }
      });

      self.register({
        id: 'toggle-fullscreen',
        label: '切换全屏',
        description: '进入或退出全屏模式',
        category: '设置',
        icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 3H5a2 2 0 00-2 2v3m18 0V5a2 2 0 00-2-2h-3m0 18h3a2 2 0 002-2v-3M3 16v3a2 2 0 002 2h3"/></svg>',
        shortcut: 'F11',
        action: function() {
          if (!document.fullscreenElement) {
            document.documentElement.requestFullscreen();
          } else {
            document.exitFullscreen();
          }
        }
      });

      // --- 模型操作类别 ---
      self.register({
        id: 'load-voxcpm2',
        label: '加载 VoxCPM2 模型',
        description: '激活 VoxCPM2 引擎',
        category: '模型',
        icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>',
        action: function() {
          var csrfToken = window.getCsrfToken ? window.getCsrfToken() : '';
          var headers = { 'Content-Type': 'application/x-www-form-urlencoded' };
          if (csrfToken) headers['X-CSRF-Token'] = csrfToken;
          fetch('/api/model/load', {
            method: 'POST',
            headers: headers,
            body: 'engine=voxcpm2'
          }).then(function(r) { return r.json(); }).then(function(d) {
            if (window.TTSApp && window.TTSApp.toast) {
              window.TTSApp.toast.show(d.message || 'VoxCPM2 加载中...', 'info');
            }
          }).catch(function() {
            if (window.TTSApp && window.TTSApp.toast) {
              window.TTSApp.toast.show('模型加载请求发送失败', 'error');
            }
          });
        }
      });

      self.register({
        id: 'load-indextts2',
        label: '加载 IndexTTS 模型',
        description: '激活 IndexTTS 2.5 引擎',
        category: '模型',
        icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>',
        action: function() {
          var csrfToken = window.getCsrfToken ? window.getCsrfToken() : '';
          var headers = { 'Content-Type': 'application/x-www-form-urlencoded' };
          if (csrfToken) headers['X-CSRF-Token'] = csrfToken;
          fetch('/api/model/load', {
            method: 'POST',
            headers: headers,
            body: 'engine=indextts2'
          }).then(function(r) { return r.json(); }).then(function(d) {
            if (window.TTSApp && window.TTSApp.toast) {
              window.TTSApp.toast.show(d.message || 'IndexTTS 加载中...', 'info');
            }
          }).catch(function() {
            if (window.TTSApp && window.TTSApp.toast) {
              window.TTSApp.toast.show('模型加载请求发送失败', 'error');
            }
          });
        }
      });

      self.register({
        id: 'unload-model',
        label: '卸载当前模型',
        description: '释放 GPU 显存',
        category: '模型',
        icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>',
        action: function() {
          fetch('/api/model/unload', { method: 'POST' })
            .then(function(r) { return r.json(); }).then(function(d) {
              if (window.TTSApp && window.TTSApp.toast) {
                window.TTSApp.toast.show(d.message || '模型已卸载', 'info');
              }
            }).catch(function() {
              if (window.TTSApp && window.TTSApp.toast) {
                window.TTSApp.toast.show('模型卸载请求失败', 'error');
              }
            });
        }
      });

      self.register({
        id: 'switch-engine-voxcpm2',
        label: '切换引擎 → VoxCPM2',
        description: '切换到 VoxCPM2 引擎',
        category: '模型',
        icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 17l10-10M4 7l10 10"/></svg>',
        action: function() {
          var selector = document.querySelector('[name="engine_selector"]');
          if (selector) {
            selector.value = 'voxcpm2';
            selector.dispatchEvent(new Event('change', { bubbles: true }));
          }
        }
      });

      self.register({
        id: 'switch-engine-indextts2',
        label: '切换引擎 → IndexTTS 2.5',
        description: '切换到 IndexTTS 2.5 引擎',
        category: '模型',
        icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 17l10-10M4 7l10 10"/></svg>',
        action: function() {
          var selector = document.querySelector('[name="engine_selector"]');
          if (selector) {
            selector.value = 'indextts2';
            selector.dispatchEvent(new Event('change', { bubbles: true }));
          }
        }
      });

      self.register({
        id: 'switch-engine-indextts20',
        label: '切换引擎 → IndexTTS 2.0',
        description: '切换到 IndexTTS 2.0 引擎（版本对比用）',
        category: '模型',
        icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 17l10-10M4 7l10 10"/></svg>',
        action: function() {
          var selector = document.querySelector('[name="engine_selector"]');
          if (selector) {
            selector.value = 'indextts20';
            selector.dispatchEvent(new Event('change', { bubbles: true }));
          }
        }
      });

      // --- 语言切换类别 ---
      var languages = [
        { id: 'lang-zh', label: '中文', code: 'zh' },
        { id: 'lang-en', label: 'English', code: 'en' },
        { id: 'lang-ja', label: '日本語', code: 'ja' },
        { id: 'lang-ko', label: '한국어', code: 'ko' }
      ];

      languages.forEach(function(l) {
        self.register({
          id: l.id,
          label: '语言: ' + l.label,
          description: '切换到 ' + l.label + ' 界面',
          category: '语言',
          icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="2" x2="22" y1="12" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>',
          action: function() {
            // 语言切换走 theme_lang.js 的统一入口（写 localStorage+cookie 后带
            // ?lang= 跳转）。历史上这里 fetch 不存在的 /api/set-lang，404 被
            // .then 吞掉后无条件 reload，语言纹丝不动（静默失效）。
            if (typeof window.setLang === 'function') {
              window.setLang(l.code);
            } else {
              location.reload();
            }
          }
        });
      });
    },

    _renderResults: function(query) {
      var q = query.toLowerCase().trim();
      this.items = this.commands.filter(function(cmd) {
        if (!q) return true;
        return cmd.label.toLowerCase().includes(q) ||
               cmd.category.toLowerCase().includes(q) ||
               (cmd.description && cmd.description.toLowerCase().includes(q));
      });

      // 按分类分组
      var groups = {};
      this.items.forEach(function(cmd) {
        if (!groups[cmd.category]) groups[cmd.category] = [];
        groups[cmd.category].push(cmd);
      });

      var html = '';
      var idx = 0;
      var categoryOrder = ['导航', '设置', '模型', '语言'];
      var sortedCategories = Object.keys(groups).sort(function(a, b) {
        var ai = categoryOrder.indexOf(a);
        var bi = categoryOrder.indexOf(b);
        if (ai === -1) ai = 99;
        if (bi === -1) bi = 99;
        return ai - bi;
      });

      for (var ci = 0; ci < sortedCategories.length; ci++) {
        var cat = sortedCategories[ci];
        var items = groups[cat];
        html += '<div class="command-palette-group">';
        html += '<div class="command-palette-group-label">' + cat + '</div>';
        html += '<div class="command-palette-group-items">';
        items.forEach(function(cmd) {
          var cls = idx === this.activeIndex ? ' active' : '';
          html += '<div class="command-palette-item' + cls + '" data-index="' + idx + '">';
          html += '<span class="command-palette-item-icon">' + cmd.icon + '</span>';
          html += '<div class="command-palette-item-body">';
          html += '<span class="command-palette-item-label">' + cmd.label + '</span>';
          if (cmd.description) {
            html += '<span class="command-palette-item-desc">' + cmd.description + '</span>';
          }
          html += '</div>';
          if (cmd.shortcut) {
            html += '<kbd class="command-palette-kbd">' + cmd.shortcut + '</kbd>';
          }
          html += '</div>';
          idx++;
        }.bind(this));
        html += '</div></div>';
      }

      // 更新计数
      var countEl = document.getElementById('cmd-count');
      if (countEl) {
        countEl.textContent = this.items.length + ' 个结果';
        countEl.style.display = this.items.length > 0 ? 'block' : 'none';
      }

      if (this.items.length === 0 && q) {
        html = '<div class="command-palette-empty"><svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" opacity="0.3"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg><span>未找到与 "<strong>' + this._escapeHtml(q) + '</strong>" 相关的命令</span></div>';
      } else if (this.items.length === 0) {
        html = '<div class="command-palette-empty"><svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" opacity="0.3"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg><span>开始输入以搜索命令...</span></div>';
      }

      this.resultsContainer.innerHTML = html;
      this.activeIndex = this.items.length > 0 ? 0 : -1;

      // 绑定鼠标事件
      this.resultsContainer.querySelectorAll('.command-palette-item').forEach(function(el) {
        el.addEventListener('mouseenter', function() {
          this.activeIndex = parseInt(el.getAttribute('data-index'));
          this._updateActive();
        }.bind(this));
        el.addEventListener('click', function() {
          var i = parseInt(el.getAttribute('data-index'));
          if (this.items[i]) {
            this.items[i].action();
            this.close();
          }
        }.bind(this));
      }.bind(this));
    },

    _updateActive: function() {
      var items = this.resultsContainer.querySelectorAll('.command-palette-item');
      items.forEach(function(el, i) {
        el.classList.toggle('active', i === this.activeIndex);
      }.bind(this));

      var activeEl = this.resultsContainer.querySelector('.command-palette-item.active');
      if (activeEl) {
        activeEl.scrollIntoView({ block: 'nearest' });
      }
    },

    _escapeHtml: function(str) {
      var div = document.createElement('div');
      div.textContent = str;
      return div.innerHTML;
    }
  };

  // 初始化
  document.addEventListener('DOMContentLoaded', function() {
    CommandPalette.init();
  });

  // 挂载到全局
  window.CommandPalette = CommandPalette;
})();
