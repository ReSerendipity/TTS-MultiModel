/* ==========================================================================
   P2-8 X-05: New User Onboarding — 5-Step Spotlight Tour
   Zero external deps. Uses localStorage key 'tts_onboarded_v1' to gate.
   Steps: ① Sidebar voice design item  →  ② Text textarea  →  ③ Engine status dot
          →  ④ Generate CTA button       →  ⑤ Bottom audio player
   ========================================================================== */
(function UXOnboarding() {
    'use strict';
    const LS_KEY = 'tts_onboarded_v1';
    /* P2-8 X-05 FIX: 无论是否已引导，都挂载 TTS_replayOnboarding；
       localStorage 闸门仅用于控制"是否自动启动引导展示" */
    const hasOnboarded = !!localStorage.getItem(LS_KEY);

    /* ===== 重放函数始终挂载到 window ===== */
    window.TTS_replayOnboarding = function() {
        localStorage.removeItem(LS_KEY);
        if (document.getElementById('onboarding-overlay')) return;
        boot();
    };
    /* 帮助抽屉中插入 重新播放引导 按钮（在 boot 中延后处理，但挂一个全局 hook 备用） */
    (function injectReplayEntryLater() {
        const doInject = () => {
            const hp = document.querySelector('.help-popup');
            if (!hp || document.getElementById('btn-replay-onboarding')) return;
            const btn = document.createElement('button');
            btn.id = 'btn-replay-onboarding';
            btn.className = 'tts-btn tts-btn-secondary';
            btn.style.cssText = 'width:100%;margin-top:12px;justify-content:center;';
            btn.textContent = '🔁 重新播放新手引导';
            btn.onclick = (e) => { e.stopPropagation(); window.TTS_replayOnboarding(); };
            hp.appendChild(btn);
        };
        setTimeout(doInject, 1500);
        setTimeout(doInject, 5000); /* 两次注入兜底 */
    })();

    /* ===== 首次访问才自动启动引导 ===== */
    if (!hasOnboarded) {
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', () => setTimeout(boot, 2000));
        } else {
            setTimeout(boot, 2000);
        }
    }

    function boot() {
        const target1 = document.querySelector('.sidebar-item[data-tab="voice_design"]') ||
                       document.querySelector('.sidebar-item:first-of-type');
        const target2 = document.querySelector('textarea[name="text"]') ||
                       document.querySelector('textarea[placeholder*="合成"]') ||
                       document.querySelector('textarea');
        const target3 = document.querySelector('.engine-status-dot') ||
                       document.querySelector('.model-tabs-group') ||
                       document.querySelector('.mini-monitor');
        const target4 = Array.from(document.querySelectorAll('button.tts-btn-primary, .btn-primary'))
                           .find(b => /生成|Generate/i.test(b.textContent)) ||
                       document.querySelector('button[name="generate"]') ||
                       document.querySelector('.sidebar-item.active + form button');
        const target5 = document.querySelector('.global-audio-player') ||
                       document.querySelector('.progress-container') ||
                       document.querySelector('.format-selector');

        const steps = [
            { el: target1, title: '① 选择工作台', desc: '侧边栏切换不同的语音合成模式：<b>语音设计</b>是最常用的"文本描述→音色"模式。' },
            { el: target2, title: '② 输入合成文本', desc: '在这里输入你想转语音的文字。支持多语言混合，点击右上角 <b>试试示例文本</b> 可一键填充示例。' },
            { el: target3, title: '③ 查看引擎状态', desc: '这里实时显示当前引擎加载情况。第一次加载需要约 30 秒，请耐心等待绿灯亮起。' },
            { el: target4, title: '④ 点击生成试听', desc: '调好参数后，按这个按钮即可开始生成语音。快捷键 <kbd>Ctrl</kbd>+<kbd>Enter</kbd> 更顺手。' },
            { el: target5, title: '⑤ 播放与导出', desc: '底部播放栏会自动加载生成结果。支持 <b>WAV</b> / <b>MP3</b> 双格式下载，历史记录永久保存。' },
        ].filter(s => s.el);

        if (steps.length < 3) return;
        createOverlay(steps);
    }

    function createOverlay(steps) {
        const mask = document.createElement('div');
        mask.id = 'onboarding-overlay';
        mask.style.cssText = `position:fixed;inset:0;z-index:2147483000;background:rgba(0,0,0,0.55);
            backdrop-filter:blur(3px);-webkit-backdrop-filter:blur(3px);cursor:default;`;

        const spotlight = document.createElement('div');
        spotlight.id = 'onboarding-spotlight';
        spotlight.style.cssText = `position:fixed;border-radius:12px;box-shadow:0 0 0 4000px rgba(0,0,0,0.55);
            transition:all 300ms cubic-bezier(0.22,1,0.36,1);pointer-events:none;border:2px solid var(--accent-primary,#7C5CBF);
            box-sizing:content-box;padding:8px;z-index:2147483001;`;

        const card = document.createElement('div');
        card.id = 'onboarding-card';
        card.style.cssText = `position:fixed;z-index:2147483002;min-width:320px;max-width:420px;
            background:var(--bg-card,#fff);color:var(--text-primary,#111);
            border:1px solid var(--border-subtle);border-radius:16px;padding:20px 22px 16px;
            box-shadow:0 20px 60px rgba(0,0,0,0.4);line-height:1.55;`;

        document.body.appendChild(mask);
        document.body.appendChild(spotlight);
        document.body.appendChild(card);
        renderStep(steps, 0, card, spotlight);

        const close = () => { mask.remove(); spotlight.remove(); card.remove(); localStorage.setItem(LS_KEY, '1'); };
        mask.addEventListener('click', close);
        document.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); }, { once: true });

        window.TTS_replayOnboarding = () => {
            localStorage.removeItem(LS_KEY);
            if (document.getElementById('onboarding-overlay')) return;
            boot();
        };
        setTimeout(() => {
            const hp = document.querySelector('.help-popup .help-footer, .help-popup');
            if (!hp) return;
            const btn = document.createElement('button');
            btn.className = 'tts-btn tts-btn-secondary';
            btn.style.cssText = 'width:100%;margin-top:12px;justify-content:center;';
            btn.textContent = '🔁 重新播放新手引导';
            btn.onclick = (e) => { e.stopPropagation(); window.TTS_replayOnboarding(); };
            hp.appendChild(btn);
        }, 1500);
    }

    function renderStep(steps, idx, card, spotlight) {
        const step = steps[idx];
        const r = step.el.getBoundingClientRect();
        spotlight.style.left = (r.left - 8) + 'px';
        spotlight.style.top = (r.top - 8) + 'px';
        spotlight.style.width = r.width + 'px';
        spotlight.style.height = r.height + 'px';
        const below = r.bottom + 18 + 220 < window.innerHeight;
        card.style.left = Math.max(24, Math.min(window.innerWidth - 444, r.left)) + 'px';
        card.style.top = (below ? (r.bottom + 18) : (Math.max(24, r.top - 240))) + 'px';
        card.innerHTML = `
            <div style="font-size:var(--font-size-xs,12px);color:var(--accent-primary);font-weight:600;letter-spacing:.08em;
                text-transform:uppercase;margin-bottom:6px;">第 ${idx + 1} / ${steps.length} 步</div>
            <h3 style="margin:0 0 8px;font-size:var(--font-size-lg,18px);font-weight:700;">${step.title}</h3>
            <div style="color:var(--text-secondary);font-size:var(--font-size-sm,13px);margin-bottom:18px;">${step.desc}</div>
            <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;">
                <button id="ob-skip" class="tts-btn tts-btn-secondary" style="padding:0 var(--space-3,12px);height:32px;">跳过</button>
                <div style="display:flex;align-items:center;gap:8px;">
                    <button id="ob-prev" class="tts-btn tts-btn-secondary" style="padding:0 var(--space-3,12px);height:32px;"
                        ${idx === 0 ? 'disabled' : ''}>上一步</button>
                    <button id="ob-next" class="tts-btn tts-btn-primary" style="height:32px;">
                        ${idx === steps.length - 1 ? '🎉 完成' : '下一步 →'}
                    </button>
                </div>
            </div>
            <div style="display:flex;gap:6px;justify-content:center;margin-top:12px;">
                ${steps.map((_, i) => `<span style="width:6px;height:6px;border-radius:50%;
                    background:${i === idx ? 'var(--accent-primary)' : 'var(--border-medium)'};"></span>`).join('')}
            </div>`;
        const closeAll = () => document.getElementById('onboarding-overlay')?.click();
        card.querySelector('#ob-skip').onclick = closeAll;
        card.querySelector('#ob-prev').onclick = () => idx > 0 && renderStep(steps, idx - 1, card, spotlight);
        card.querySelector('#ob-next').onclick = () => {
            if (idx === steps.length - 1) return closeAll();
            renderStep(steps, idx + 1, card, spotlight);
        };
        const cr = card.getBoundingClientRect();
        if (cr.right > window.innerWidth - 24) card.style.left = (window.innerWidth - cr.width - 24) + 'px';
        if (cr.bottom > window.innerHeight - 24) card.style.top = (r.top - cr.height - 18) + 'px';
    }
})();
