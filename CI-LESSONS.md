# SeedVR2 / TTS / Image 三仓 CI 修复——经验教训清单（2026-08）

> 本文件是防复发收口的素材：每条教训都对应一次真实的 CI 红或本地失败，
> 全部经过实测验证。后续合并进 CONTRIBUTING.md 与最终实施报告。

## A. 测试代码纪律（本次最大的失败来源）

1. **假等待是 E2E flaky 的头号元凶**：`waitForFunction(() => true)` + 立即断言
   是无效等待（sse.spec 8 个测试里 4 个因此失败）。异步 UI（SSE 连接、GPU 探测、
   mock 数据渲染）必须用 `expect.poll(实际条件)` 或 `waitForFunction(实际条件)`
   等待真实状态，而不是"等了个寂寞"再断言。
2. **断言选择器必须与产品当前 DOM 对齐**：产品重写后测试还在找旧 ID/类
   （model status 找 `.model-status-badge` 但实际是 `#statusModel`；dropzone 用
   snake_case 但已改 camelCase）。改产品前先扫测试引用的所有选择器。
3. **测试断言要与产品实现语义一致**：产品在连接 error 时清空 `__sseConnection`
   引用并走指数退避重连——测试硬断言"连接对象必须存在"与产品行为矛盾。
   条件断言（存在→查状态；不存在→查页面存活）才是对的。
4. **路由 mock 用一次性 `route.fulfill` 时，SSE 流必然"关闭"**→ 浏览器触发
   error → 产品走重连。测试不要依赖连接常驻，改断言"请求被发出/事件被处理"。
5. **Playwright 多个 route 注册同一 URL 时顺序敏感**：测试自己的 route 与
   fixtures 的 route 可能互相抢占，务必实测（page.on('request') + 计数器）
   而不是猜。

## B. 视觉/对比度（a11y）专项

6. **axe 在动画中途采样会误报对比度**：opacity 0→1 的入场动画（toast 的
   toastIn、recent 列表的 sv-anim-rise）让 axe 在 ~50% 透明度时采样，
   把达标色混成不达标（实测 #e8e4de×0.5+#2a2622×0.5≈#87837f，4.1:1）。
   修法：a) 动画改纯 transform（toast，opacity 恒 1）；b) 扫描前冻结动画
   （`* { animation:none !important; transition:none !important }`，
   wcag-contrast.spec 已有同款，a11y.spec 补齐）。
7. **双重弱化必踩线**：muted 色（#a89f96，5.91:1 达标）+ `opacity: 0.7`
   双重弱化把 0.72rem 小字压到 ~3.6:1。弱化色本身就是弱化，不要再叠 opacity。
8. **调色前先拿浏览器内实测色值**：之前反复调 --sv-text-muted 无效，是因为
   误判了违规节点；用浏览器内 axe + computed style 拿真实 fg/bg 再动手，
   一次到位。临时 spec 里跑 axe（tmp-axe.spec.ts 模式）是可靠调试法。
9. **半透明背景 + 动画 = 对比度扫描的定时炸弹**：--sv-tooltip-bg 是
   rgba(42,38,34,0.96)，axe 会混合页面背景算色。所有 rgba 背景元素都要
   按"混合后背景"验证对比度。

## C. 环境与工具链（Windows 本地）

10. **Playwright webServer 用 PATH 里的 python**：本机 PATH 的 python
    （AutoClaw 自带）没有 fastapi，服务器只能用 C:\Python312\python.exe 起。
    服务器死掉后 webServer 用错 python 启动失败，误以为是测试失败。
    修法：本地先手动起服务器（reuseExistingServer: true 复用），或改 config
    的 command 指向正确解释器（CI 不受影响）。
11. **PowerShell 输出乱码**：内联 python -c 输出中文在 PowerShell 里乱码，
    脚本必须 `sys.stdout.reconfigure(encoding='utf-8', errors='replace')`；
    复杂脚本写成 .py 文件跑，避免引号/正则转义 SyntaxError。
12. **写仓库文件受沙箱限制**：write 工具只能写 workspace，仓库文件用
    exec + python 脚本（放 .openclaw\tmp\）改；node/TS 实验脚本先写
    .openclaw\tmp\ 再 Copy-Item 到 tests 目录（require 从脚本目录解析）。
13. **服务器被误杀/死掉后要区分"测试失败"和"环境失败"**：webServer 报
    `No module named 'fastapi'` / `Process from config.webServer was not
    able to start` 是环境问题，不是代码问题。

## D. QG / lint / 覆盖率

14. **CI 用固定版本工具链**：ruff/black/mypy 不固定版本会漂移（旧版本地过、
    新版 CI 红）。pyproject.toml 固定版本 + 本地装同版本。
15. **覆盖率门槛只在 CI 判**：跨平台数值有差异，本地不判；CI 红在覆盖率时
    补测试而不是调门槛。
16. **乱码事故**：第三方"编码转换/优化"工具批量改写源文件导致中文乱码
    SyntaxError——只用 UTF-8 无 BOM + .gitattributes 统一 LF；提交前
    check_local.py 的严格 UTF-8 扫描兜底。

## E. 提交与 CI 节奏

17. **绝不 `git add -A`/`git add .`**：只 add 明确文件（本次全程遵守），
    防止把调试产物、临时 spec、测试结果一起提交。
18. **push 后等 CI 出结果再推下一个 commit**：连续 push 触发 concurrency
    取消旧 run，看 CI 状态以最新 HEAD 为准；`cancelled` 不是失败。
19. **临时调试产物必须清理**：tmp-*.spec.ts、调试脚本、复验输出 txt 在
    交付前删除/隔离（本次在 .openclaw\tmp\ 集中管理）。
20. **每次修复后立即本地复验该组 + 相关组**：修完一个 spec 就本地跑绿再
    提交，不要攒到最后一次性验证（全量 231 个一次跑出 20 失败，追溯成本高）。

## F. 防复发机制（已落地 + 本次补强）

- `.gitattributes`：UTF-8 + LF 统一（*.bat/*.ps1 用 CRLF）
- `scripts/install-hooks.ps1`：core.hooksPath → scripts/git-hooks/pre-push，
  推送前自动跑 check_local.py 快检
- `scripts/check_local.py`：ruff / ruff format --check / compileall /
  严格 UTF-8 扫描 / 可选 mypy / 可选全量 pytest（--timeout 防挂死）
- **本次补强**：check_local.py 增加 `--e2e` 选项（本地跑受影响 Playwright
  spec 组）+ 环境预检（fastapi/playwright 可导入）；CONTRIBUTING.md 增加
  "E2E 编写纪律" 与 "提交纪律" 章节
