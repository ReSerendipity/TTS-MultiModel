/**
 * @deprecated Use scripts/capture_screenshots.py instead.
 * This Node.js version is retained for backward compatibility but will be
 * removed in a future release. The Python version provides the same
 * functionality without requiring Node.js.
 *
 * TTS MultiModel - Playwright screenshot capture
 *
 * Captures each VoxCPM2 tab in both light and dark themes, plus the
 * IndexTTS 2.0 secondary tabs. Output structure matches the README:
 *   docs/screenshots/voxcpm2_NN_<feature>_viewport.png   (light, primary)
 *   docs/screenshots/dark/voxcpm2_NN_<feature>_viewport.png (dark, secondary)
 *
 * Usage:
 *   1. Start the UI-only test server: WPy64-312101\python\python.exe bin\start_ui_test.py
 *   2. From this repo: node tests/capture-screenshots.js
 *   OR (preferred): python scripts/capture_screenshots.py
 */

const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const BASE_URL = 'http://127.0.0.1:7869';
const REPO_ROOT = path.join(__dirname, '..');
const OUTPUT_DIR = path.join(REPO_ROOT, 'docs', 'screenshots');
const DARK_DIR = path.join(OUTPUT_DIR, 'dark');

const VIEWPORTS = {
  desktop: { width: 1366, height: 900 },
};

const TABS = [
  { num: '01', tab: 'voice_design', name: 'voice_design' },
  { num: '02', tab: 'voice_clone', name: 'voice_clone' },
  { num: '03', tab: 'ultimate_clone', name: 'ultimate_clone' },
  { num: '04', tab: 'script', name: 'script_workshop' },
  { num: '05', tab: 'prompt_continue', name: 'prompt_continuation' },
  { num: '06', tab: 'lora', name: 'lora' },
  { num: '07', tab: 'lora_training', name: 'lora_training' },
  { num: '08', tab: 'settings', name: 'settings' },
  { num: '09', tab: 'history', name: 'history' },
  { num: '10', tab: 'persona', name: 'persona_library' },
  { num: '11', tab: 'help', name: 'help' },
];

function ensureDir(dir) {
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
}

async function setTheme(page, theme) {
  await page.evaluate((t) => {
    // Skip the new-user onboarding wizard (5-step spotlight)
    localStorage.setItem('tts_onboarded_v1', '1');
    localStorage.setItem('app_theme', t);
    document.documentElement.classList.remove('dark', 'light');
    document.documentElement.classList.add(t);
    document.documentElement.style.colorScheme = t;
  }, theme);
  await page.waitForTimeout(300);
}

async function clickTab(page, tabName) {
  // The TTS UI uses HTMX-driven sidebar buttons; clicking a sidebar item
  // triggers hx-get="/tab/<name>" which loads the tab fragment into #tab-content.
  const button = page.locator(`.sidebar-item[data-tab="${tabName}"]`);
  if ((await button.count()) === 0) {
    throw new Error(`Tab button not found: data-tab="${tabName}"`);
  }
  // 若按钮位于折叠分组（.sidebar-nav-section.section-collapsed）内，先点击
  // 分组标题 .sidebar-nav-label 展开，否则按钮 display:none 无法点击。
  await page.evaluate((t) => {
    const btn = document.querySelector(`.sidebar-item[data-tab="${t}"]`);
    if (!btn) return;
    const sec = btn.closest('.sidebar-nav-section');
    if (sec && sec.classList.contains('section-collapsed')) {
      const label = sec.querySelector('.sidebar-nav-label');
      if (label) label.click();
    }
  }, tabName);
  await page.waitForTimeout(400);  await button.first().click();
  // Wait for HTMX swap to finish
  await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(1200);
}

async function captureTab(page, tab, theme, viewportName) {
  const suffix = theme === 'dark' ? '_dark_viewport' : '_viewport';
  const filename = `voxcpm2_${tab.num}_${tab.name}${suffix}.png`;
  const outDir = theme === 'dark' ? DARK_DIR : OUTPUT_DIR;
  const filePath = path.join(outDir, filename);

  await page.goto(`${BASE_URL}/`, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(800);

  await setTheme(page, theme);

  try {
    await clickTab(page, tab.tab);
  } catch (e) {
    console.error(`  [skip] ${tab.tab}: ${e.message}`);
    return false;
  }

  await page.screenshot({ path: filePath, fullPage: true });
  console.log(`  Captured: ${path.relative(REPO_ROOT, filePath)}`);
  return true;
}

async function captureHomePage(page, theme, viewportName) {
  const suffix = theme === 'dark' ? '_dark_viewport' : '_viewport';
  const filename = `home${suffix}.png`;
  const outDir = theme === 'dark' ? DARK_DIR : OUTPUT_DIR;
  const filePath = path.join(outDir, filename);

  await page.goto(`${BASE_URL}/`, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(2000);
  await setTheme(page, theme);

  await page.screenshot({ path: filePath, fullPage: true });
  console.log(`  Captured: ${path.relative(REPO_ROOT, filePath)}`);
}

(async () => {
  console.log('TTS MultiModel - Screenshot Capture');
  console.log('====================================');
  console.log(`Base URL: ${BASE_URL}`);
  console.log(`Output:   ${OUTPUT_DIR}`);
  console.log(`Dark sub: ${DARK_DIR}`);
  console.log('');

  ensureDir(OUTPUT_DIR);
  ensureDir(DARK_DIR);

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();

  try {
    console.log('Checking if server is running...');
    try {
      const resp = await page.goto(`${BASE_URL}/`, { timeout: 10000, waitUntil: 'domcontentloaded' });
      if (!resp || !resp.ok()) {
        throw new Error(`server responded ${resp ? resp.status() : 'no response'}`);
      }
      console.log('Server is running!');
    } catch (e) {
      console.error('ERROR: Server is not running at', BASE_URL);
      console.error('Start it first: WPy64-312101\\python\\python.exe bin\\start_ui_test.py');
      process.exit(1);
    }

    for (const [vpName, vpSize] of Object.entries(VIEWPORTS)) {
      console.log(`\n=== Viewport: ${vpName} (${vpSize.width}x${vpSize.height}) ===`);
      await page.setViewportSize(vpSize);

      for (const theme of ['light', 'dark']) {
        console.log(`\n--- Theme: ${theme} ---`);
        try {
          await captureHomePage(page, theme, vpName);
        } catch (e) { console.error(`home failed: ${e.message}`); }

        for (const tab of TABS) {
          try {
            await captureTab(page, tab, theme, vpName);
          } catch (e) {
            console.error(`  [err] ${tab.tab}: ${e.message}`);
          }
        }
      }
    }

    console.log('\n====================================');
    console.log('Screenshot capture complete!');
  } catch (error) {
    console.error('Error:', error);
    process.exit(1);
  } finally {
    await browser.close();
  }
})();

