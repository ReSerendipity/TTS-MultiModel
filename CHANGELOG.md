# Changelog

## [2.2.1](https://github.com/ReSerendipity/TTS_MultiModel/compare/v2.2.0...v2.2.1) (2026-08-22)


### Bug Fixes

* **tests:** 修复 EngineRegistry 导入错误 ([82f6ae5](https://github.com/ReSerendipity/TTS_MultiModel/commit/82f6ae5a37a1623fcbd864e9d8faf98a1ce0f624))


### Documentation

* 顶部补齐 CI 徽章，移除底部重复徽章 ([1205d8a](https://github.com/ReSerendipity/TTS_MultiModel/commit/1205d8a76c9f68d0653a0ddee9a847ffeb8893a2))

## [2.2.0](https://github.com/ReSerendipity/TTS_MultiModel/compare/v2.1.0...v2.2.0) (2026-08-21)


### Features

* add GitHub Pages online demo (pure frontend simulation) ([dad34bc](https://github.com/ReSerendipity/TTS_MultiModel/commit/dad34bcb2109be7e5936f5e58a50e01bf69f849d))
* **compliance:** add script workshop legal warning (5 locales) - prohibit synthesizing protected voices per IndexTTS DISCLAIMER ([4ae3cbd](https://github.com/ReSerendipity/TTS_MultiModel/commit/4ae3cbd1429db235701f37516240a0f8aca0e91c))
* full-feature demo v2 - 15 tabs/command palette/persona/history ([fef46c1](https://github.com/ReSerendipity/TTS_MultiModel/commit/fef46c13ff7826680dde2a5cfc3d767a18b5d1f7))
* **logging:** 完善日志机制 - 统一格式(PID/TID/模块位置/request_id) + 环境变量覆盖 + 双通道输出 ([9097178](https://github.com/ReSerendipity/TTS_MultiModel/commit/9097178276570fc9662eee3553dcb75ae0e4c54b))
* **security:** complete security hardening based on assessment report ([f623304](https://github.com/ReSerendipity/TTS_MultiModel/commit/f62330493b9732bdd17ac5387917533d00ea3969))
* **tests:** 安全测试补盲与 M1 里程碑达成（最终轮次） ([b6b5e48](https://github.com/ReSerendipity/TTS_MultiModel/commit/b6b5e4872e3f547ea8df7d7319cd4072fd106140))
* **tests:** 新增安全测试覆盖与引擎协议合规性测试 (M1 里程碑) ([4a2c010](https://github.com/ReSerendipity/TTS_MultiModel/commit/4a2c010cf46d47fc33a4207e3ba6e25311fc2433))
* **text-processing:** G2P manager, text segmenter, content safety, prompt expander ([9f005b8](https://github.com/ReSerendipity/TTS_MultiModel/commit/9f005b8ec5a8acab7c412a47ad17900fd0963421))
* update integrated app features and clean up deprecated components ([c0728cd](https://github.com/ReSerendipity/TTS_MultiModel/commit/c0728cda58111a67fe6a0902d0e55a624a661e86))
* 添加性能监控脚本与计划文档 ([91b8ddd](https://github.com/ReSerendipity/TTS_MultiModel/commit/91b8ddde5dd9bb547b7bf4d0b72c9726280ed015))
* 累计提交历史任务成果 - IndexTTS2 引擎完善/多语言/文档/学习报告 ([36a94f2](https://github.com/ReSerendipity/TTS_MultiModel/commit/36a94f29cd3040c4489670e07c22f25762109ddf))
* 跨项目借鉴改造 — 安全/配置/断点续跑/模型共享/音频水印 ([11a0e57](https://github.com/ReSerendipity/TTS_MultiModel/commit/11a0e57ea5d887e272c24563ab84f9883945686c))
* 路线图落地 — 数字水印、spec 契约层、断点续跑恢复接线、前端冒烟 ([27e2a7d](https://github.com/ReSerendipity/TTS_MultiModel/commit/27e2a7dc0d236bd3cd4b350e315ae2ad478650d1))


### Bug Fixes

* .gitignore static/ 规则过宽导致前端资源未入库（CI 页面无 CSS/JS）；锚定根目录并补提交 static 39 个文件 ([fc9c484](https://github.com/ReSerendipity/TTS_MultiModel/commit/fc9c484899839cca12a138f60b9e4895ce44699e))
* app_server 补充 __main__ 入口（python -m 启动失败根因，E2E 服务器无法启动） ([611422c](https://github.com/ReSerendipity/TTS_MultiModel/commit/611422c3c8c7817e21a071f95c0f79ba8eb1b3ff))
* **build:** 移除 license classifier（PEP 639 与 license 表达式冲突，新版 setuptools 拒绝构建） ([aca3fc7](https://github.com/ReSerendipity/TTS_MultiModel/commit/aca3fc71a1e8fa5d374d3338a75f6140cd074ccb))
* check_local.py 移除未使用 import 并通过 black ([8834c15](https://github.com/ReSerendipity/TTS_MultiModel/commit/8834c15605db4853ac611a97537c1a58237152a5))
* **ci+tests:** 修复流水线、激活 smoke marker 并更新 AGENTS.md ([89bbcdd](https://github.com/ReSerendipity/TTS_MultiModel/commit/89bbcdd023b37e70dae76afe0af4b5a3352a2716))
* **ci:** pytest 加 180s 超时保护（pytest-timeout），防止测试挂起导致 job 卡 6 小时 ([e47fe7b](https://github.com/ReSerendipity/TTS_MultiModel/commit/e47fe7b05851f64b59550fe04e2a614a6d0f9d42))
* **ci:** release-please 改为仅手动触发（避免 push 时误报失败） ([2676fe2](https://github.com/ReSerendipity/TTS_MultiModel/commit/2676fe286e8e8ec9c855a5839d132a3c82e1212a))
* **e2e:** collapse_toggle 与 theme_toggle 测试前 dismiss onboarding overlay（CI 全新环境无 localStorage 缓存） ([87769a3](https://github.com/ReSerendipity/TTS_MultiModel/commit/87769a3166104bccc29e906563b9a5ee8e253aba))
* **e2e:** test_collapse_toggle 完整版 onboarding dismiss（等 2.2s 覆盖 setTimeout boot + 二扫 DOM） ([590d257](https://github.com/ReSerendipity/TTS_MultiModel/commit/590d2579d28f0ece14247c3ed20e2a4200ff81a2))
* **e2e:** 修正失效的 /tabs/ 路由为 /?tab=，修复 wait_for_function arguments 兼容性（Playwright 新版） ([d58a03f](https://github.com/ReSerendipity/TTS_MultiModel/commit/d58a03f8982da3655c8e32735effa640ac3233d9))
* **e2e:** 停止 JS 定时器 + 禁用 CSS 动画消除动画帧差异；临时重建 Linux baseline ([fa50990](https://github.com/ReSerendipity/TTS_MultiModel/commit/fa509906b6eb55a69033fc185570a9d8750f25dc))
* **e2e:** 冻结 Math.random 消除波形随机绘制导致的截图差异 ([ce84dca](https://github.com/ReSerendipity/TTS_MultiModel/commit/ce84dca77c18b48bd5088a675d7e7f90748657ce))
* **e2e:** 引擎 tab 切换用 JS click 绕过 model-tabs 容器指针拦截（headless 布局差异） ([5903281](https://github.com/ReSerendipity/TTS_MultiModel/commit/5903281f62917af3c3a6147fe365e07ba230d056))
* **e2e:** 截图测试点击折叠分组内 tab 前先展开；E2E job 超时 15→30 分钟 ([c89f2dc](https://github.com/ReSerendipity/TTS_MultiModel/commit/c89f2dc879465aff635052810093d64ad5862c80))
* **e2e:** 拦截远程字体 + 等待 fonts.ready 消除字体加载时序差异 ([c34afc3](https://github.com/ReSerendipity/TTS_MultiModel/commit/c34afc3b6fae8cb3b3c6cb76a48e6624caffec4f))
* **e2e:** 视觉回归测试统一稳定化（onboarding dismiss + 渲染等待）+ 更新全部 baseline ([ad2132f](https://github.com/ReSerendipity/TTS_MultiModel/commit/ad2132f8042c37d9abad80e2b66e997df1c31c95))
* **e2e:** 等待 htmx 异步内容加载完成再截图（消除跨 run 加载时序差异） ([7359fc0](https://github.com/ReSerendipity/TTS_MultiModel/commit/7359fc023ad0232f668ed5f06956c656135a8c92))
* hide watermark from user-visible surfaces (logs to debug, README, demo, agreement wording) ([b1acdc5](https://github.com/ReSerendipity/TTS_MultiModel/commit/b1acdc5ad2d7b6435ddeefc31d963a64d60110e0))
* remove local-only Chinese docs from remote; add gitignore rules ([56e7efc](https://github.com/ReSerendipity/TTS_MultiModel/commit/56e7efc48ba271acd7b444556126d80a76aae581))
* resolve ruff lint & format issues to pass CI (Lint job) ([8cde27a](https://github.com/ReSerendipity/TTS_MultiModel/commit/8cde27ac738b87b555cbb928b4d1c972128b3531))
* ruff import 排序（audio_watermark 拆分 import，修复 CI lint 失败） ([435c19f](https://github.com/ReSerendipity/TTS_MultiModel/commit/435c19fa62b1807f0d5fcab4f1a5d0943697dde8))
* SSE 测试线程改 daemon 防 pytest 挂死（根因：SSE 无限流线程永不退出）；矩阵排除 tests/e2e（Playwright 由独立 workflow 跑） ([0c37acb](https://github.com/ReSerendipity/TTS_MultiModel/commit/0c37acbd9d0338805365892c14d8d82bed51ba11))
* **test:** correct whitespace and length calculation in text segmenter crossfade, fix whitespace and empty text trim in G2P manager/prompt expander, align G2P is_available with design that supported language is always available on download ([15b3f64](https://github.com/ReSerendipity/TTS_MultiModel/commit/15b3f6443f4bb8df8098056f9e598512c34cccd7))
* **test:** pretrained_models 目录测试改为自动创建（目录被 gitignore，CI checkout 不含模型目录） ([2503f0f](https://github.com/ReSerendipity/TTS_MultiModel/commit/2503f0f3141c2314b95e61623343495362c16fab))
* **tests:** 修复测试反模式-PytestReturnNotNone/硬编码/残缺断言/吞没异常/视觉回归无对比, 删除废弃脚本 ([f0bcc63](https://github.com/ReSerendipity/TTS_MultiModel/commit/f0bcc63008721b98c5422c85a89596af5898d1e1))
* **tests:** 消除永真断言与零断言反模式并强化认证行为级测试 ([a982676](https://github.com/ReSerendipity/TTS_MultiModel/commit/a982676f4eb1da7677027b9e8e17d700aa375e8b))
* treat dots_tts as optional in compat check; skip playwright test when dep missing ([629a1bd](https://github.com/ReSerendipity/TTS_MultiModel/commit/629a1bd76996cd6f9ee5ac85bf52c8412fd74bfd))
* zh voice warning in script dubbing play paths ([0f82d8a](https://github.com/ReSerendipity/TTS_MultiModel/commit/0f82d8abcebd7ae9ab508641306c0c386de8da79))
* zh voice warning, full-text display in continue generation to match spoken text ([b7c9f0a](https://github.com/ReSerendipity/TTS_MultiModel/commit/b7c9f0a026b3be8493d33af17806a64c50c6c8a7))
* 修复 task_queue 协程泄漏并消除测试弃用警告 ([4f1f4e0](https://github.com/ReSerendipity/TTS_MultiModel/commit/4f1f4e0bb405d6dbd9f915c1206278d76db79c66))
* 补 psutil 依赖（health.py 顶层导入，CI 测试收集失败） ([d961d90](https://github.com/ReSerendipity/TTS_MultiModel/commit/d961d90230d33fe6809891d03c244743c07adf24))


### Reverts

* pyproject addopts 移除 --timeout（与 pytest-playwright 同名选项冲突致 visual gate 失败）；CI 命令里保留 --timeout=180 ([c2d347e](https://github.com/ReSerendipity/TTS_MultiModel/commit/c2d347e8c0e48bf544257d9ac392d200887347fb))


### Documentation

* add model download & verification examples ([0c4384f](https://github.com/ReSerendipity/TTS_MultiModel/commit/0c4384f533ed743f9a51f4cf03772b1517e14d23))
* add module responsibility boundaries (model_manager/registry/config/service_layer/optimizer); gitignore: unify template ([1b70a86](https://github.com/ReSerendipity/TTS_MultiModel/commit/1b70a86f0ea67faff5d6813b5629450149144ccb))
* **compliance:** add independent third-party declaration vs model owners (ByteDance Seed / Alibaba Tongyi / bilibili) ([499355b](https://github.com/ReSerendipity/TTS_MultiModel/commit/499355b556b21a0b268bca4ccf9f24d4744daf8a))
* **compliance:** rebrand subtitle, unify IndexTTS version naming, add third-party disclaimer to demo footer ([61fa452](https://github.com/ReSerendipity/TTS_MultiModel/commit/61fa452cbcf2e0fe2198cd0e52ea67dbf1f8525f))
* link MODEL_DOWNLOADS.md from README ([93bc422](https://github.com/ReSerendipity/TTS_MultiModel/commit/93bc4223a3c3e06f7d35a1ba256fccdec8cf5ce7))
* record watermark v2 known boundaries (1s-white-noise payload info-theoretic limit, SNR-crest coupling) with measured data and optional breakthrough paths ([fc2585f](https://github.com/ReSerendipity/TTS_MultiModel/commit/fc2585febd45cc7f9ea1609847bbaba700f05d74))
* remove dots.tts references and add VoxCPM source baseline ([c24ec79](https://github.com/ReSerendipity/TTS_MultiModel/commit/c24ec79ba7c1087a707ef49f967aceda1ecfb309))
* restore open-source essentials (LICENSE, NOTICE, USER_AGREEMENT, COC, SECURITY, upstream source declaration) ([dea71ee](https://github.com/ReSerendipity/TTS_MultiModel/commit/dea71ee78c4d83b9c86af860f53ebd24f530cc74))
* restore README, CI, demo, screenshots to remote; gitignore local-only content; restore pyproject readme ref ([2816758](https://github.com/ReSerendipity/TTS_MultiModel/commit/28167584e6e51d2aa0d223cb33deeb1c3930a3cc))
* restore README, CI, demo, screenshots to remote; restore pyproject; gitignore local-only ([4bbcdf8](https://github.com/ReSerendipity/TTS_MultiModel/commit/4bbcdf8555f081be411af7c0fa9c02b7b7a9cb4a))
* self-check pass, bump v1.7 ([ac8e7c1](https://github.com/ReSerendipity/TTS_MultiModel/commit/ac8e7c11da1c4ff0d234a579ffc291b25d4fecf0))
* trigger pages deploy ([f42a806](https://github.com/ReSerendipity/TTS_MultiModel/commit/f42a806e7a9c11ac4c843720ff70e132884d35d0))
* update README to include dots.tts (three-model support) and API/dirs ([c6084f8](https://github.com/ReSerendipity/TTS_MultiModel/commit/c6084f8abf31223ea920de8035d6cd011c5d3f37))
* 模型下载章节补充 HuggingFace/ModelScope 链接，新增社交预览图 ([19cb124](https://github.com/ReSerendipity/TTS_MultiModel/commit/19cb124bf246f355b6adf87e393dde261746e4a8))
* 界面预览只展示浅色截图，深色截图不再跟踪 ([2a7b926](https://github.com/ReSerendipity/TTS_MultiModel/commit/2a7b9268881f971f9d6b92e89db1f96c055f2ff7))
* 补全项目健康度评估报告全部缺失要素（perf目录+AGENTS.md+ARCHITECTURE.md+pre-commit） ([a5348c2](https://github.com/ReSerendipity/TTS_MultiModel/commit/a5348c2f5481f43752cef31c1dc7bafe0efa8247))
