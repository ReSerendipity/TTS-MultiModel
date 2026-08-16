/**
 * TTS MultiModel - Full Website Screenshot Capture
 *
 * 由 Seedvr2 / Image_MultiModel 的 tests/capture-screenshots.js 复制改造：
 *   - BASE_URL 改为 http://127.0.0.1:7869（TTS MultiModel 默认端口）
 *   - 主题持久化键改为 app_theme（class 方式，非 data-theme）
 *   - 页面为单页 SPA + htmx 标签页（sidebar 的 data-tab 切换 /tab/* 到 #tab-content）
 *   - 健康检查端点改为 /api/system/health
 *   - 新增：自动关闭新手引导弹窗（tts_onboarded_v1）
 *
 * Prerequisites:
 *   - TTS MultiModel server running (default http://127.0.0.1:7869), start with start.bat
 *   - Playwright chromium installed: npm install && npx playwright install chromium
 *
 * Usage:
 *   node capture-screenshots.js
 *
 * Optional env overrides:
 *   TTS_BASE_URL  e.g. http://127.0.0.1:7869
 *   TTS_OUT_DIR   e.g. ./screenshots
 *
 * Output: screenshots/<viewport>/<theme>/<NN>-<name>.png
 *
 * NOTE: 只通过 JS click 切换纯 UI 状态（主题、标签页）。触发真实后端工作的
 *       （生成、克隆、训练等）按钮不点击。
 */
const { chromium } = require('@playwright/test');
const path = require('path');
const fs = require('fs');

const BASE_URL = process.env.TTS_BASE_URL || 'http://127.0.0.1:7869';
const OUTPUT_DIR = process.env.TTS_OUT_DIR
  ? path.resolve(process.env.TTS_OUT_DIR)
  : path.join(__dirname, '..', 'screenshots');

const VIEWPORTS = {
  desktop: { width: 1920, height: 1080 },
  tablet: { width: 768, height: 1024, isMobile: true, hasTouch: true },
  mobile: { width: 375, height: 812, isMobile: true, hasTouch: true },
};

const THEMES = ['dark', 'light'];

// 与 tests/e2e/test_screenshot_capture.py 的 TABS 保持一致的核心标签页
const TABS = [
  { num: '01', tab: 'voice_design' },
  { num: '02', tab: 'voice_clone' },
  { num: '03', tab: 'ultimate_clone' },
  { num: '04', tab: 'script' },
  { num: '05', tab: 'prompt_continue' },
  { num: '06', tab: 'lora' },
  { num: '07', tab: 'lora_training' },
  { num: '08', tab: 'settings' },
  { num: '09', tab: 'history' },
  { num: '10', tab: 'persona' },
  { num: '11', tab: 'help' },
];

function ensureDir(dir) {
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
}

async function screenshotPage(page, name, options = {}) {
  const {
    fullPage = true,
    waitFor = null,
    viewportName = 'desktop',
    theme = 'dark',
  } = options;

  const dir = path.join(OUTPUT_DIR, viewportName, theme);
  ensureDir(dir);

  const filePath = path.join(dir, `${name}.png`);

  if (waitFor) {
    await page.waitForTimeout(waitFor);
  }

  await page.screenshot({ path: filePath, fullPage });
  console.log(`  Captured: ${filePath}`);
}

async function setTheme(page, theme) {
  // 主题持久化键 'app_theme' 是 static/js/theme_lang.js 使用的真实键。
  // 通过 class（dark/light）+ colorScheme 应用，导航前设置 localStorage 即可。
  await page.evaluate((t) => {
    localStorage.setItem('app_theme', t);
    const html = document.documentElement;
    html.classList.remove('light', 'dark');
    html.classList.add(t);
    html.style.colorScheme = t;
  }, theme);
  await page.waitForTimeout(300);
}

async function safe(label, viewportName, theme, fn) {
  try {
    await fn();
  } catch (e) {
    console.error(`  [SKIP] ${label} (${viewportName}, ${theme}): ${e.message}`);
  }
}

// 通过 JS click 切换标签页（htmx 处理 /tab/* 加载），避免元素遮挡或移动端侧栏隐藏
async function clickTab(page, tabName) {
  await page.evaluate((t) => {
    const el = document.querySelector(`.sidebar-item[data-tab="${t}"]`);
    if (!el) throw new Error(`sidebar item [data-tab=${t}] not found`);
    el.click();
  }, tabName);
}

async function captureHomePage(page, viewportName, theme) {
  console.log(`Capturing Home Page (${viewportName}, ${theme})...`);
  await page.goto(`${BASE_URL}/`, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(3000);

  await screenshotPage(page, '00-home', { viewportName, theme });
}

async function captureTab(page, tab, viewportName, theme) {
  const label = `${tab.num}-${tab.tab}`;
  console.log(`Capturing Tab ${label} (${viewportName}, ${theme})...`);

  await safe(label, viewportName, theme, async () => {
    await page.goto(`${BASE_URL}/`, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForTimeout(1500);
    await clickTab(page, tab.tab);
    // htmx 加载 + 渲染标签页内容
    await page.waitForTimeout(1800);
    await page.waitForSelector('#tab-content', { timeout: 5000 });
    await screenshotPage(page, `${tab.num}-${tab.tab}`, { viewportName, theme });
  });
}

async function captureAllViewports(page, viewports, themes) {
  for (const [vpName, vpSize] of Object.entries(viewports)) {
    console.log(`\n=== Viewport: ${vpName} (${vpSize.width}x${vpSize.height}) ===`);
    await page.setViewportSize({ width: vpSize.width, height: vpSize.height });

    for (const theme of themes) {
      console.log(`\n--- Theme: ${theme} ---`);
      await setTheme(page, theme);

      await captureHomePage(page, vpName, theme);
      for (const tab of TABS) {
        await captureTab(page, tab, vpName, theme);
      }
    }
  }
}

(async () => {
  console.log('TTS MultiModel - Full Website Screenshot Capture');
  console.log('=================================================');
  console.log(`Base URL: ${BASE_URL}`);
  console.log(`Output Dir: ${OUTPUT_DIR}`);
  console.log('');

  ensureDir(OUTPUT_DIR);

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();

  // 拦截外部字体/CDN 请求（如 fonts.googleapis.com / fonts.gstatic.com）。
  // base.html 头部的 Google Fonts 样式表是 render-blocking 的，在无外网环境下会
  // 挂起并导致 domcontentloaded 永不触发；本地资源（127.0.0.1）照常放行。
  await context.route('**/*', (route) => {
    const url = route.request().url();
    if (url.startsWith('http://fonts.') || url.startsWith('https://fonts.')) {
      return route.abort();
    }
    return route.continue();
  });

  // 自动关闭 TTS 新手引导弹窗（避免遮挡截图）
  await page.addInitScript(() => {
    localStorage.setItem('tts_onboarded_v1', 'true');
  });

  try {
    console.log('Checking if server is running...');
    try {
      await page.goto(`${BASE_URL}/api/system/health`, { timeout: 10000 });
      console.log('Server is running!');
    } catch (e) {
      console.error('ERROR: Server is not running at', BASE_URL);
      console.error('Please start the server first with: start.bat');
      process.exit(1);
    }

    await captureAllViewports(page, VIEWPORTS, THEMES);

    console.log('\n=========================================');
    console.log('Screenshot capture complete!');
    console.log(`All screenshots saved to: ${OUTPUT_DIR}`);

  } catch (error) {
    console.error('Error:', error);
    process.exit(1);
  } finally {
    await browser.close();
  }
})();
