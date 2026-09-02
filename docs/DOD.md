# 完成定义（Definition of Done）

> **来源**：家族通用 DOD 模板 `.spec_audit/family_DOD.md`（源自 SpiritPal definition-of-done.md 泛化），本仓本地化。
> **适用范围**：TTS_MultiModel 全项目所有功能开发任务。

---

## 0. DoD 等级

| 等级 | 适用场景 | 要求 |
|------|---------|------|
| **Full DoD** | 正式功能开发 / 大型 PR | 全部检查类 |
| **Lite DoD** | Bug 修复 / 小优化 | 代码完成 + 测试覆盖 + 构建验证 |
| **Hotfix DoD** | 紧急线上修复 | 代码完成 + 构建验证（事后补齐其余） |

> 判定「完成」必须先跑对应等级清单；不满足不得标注完成、不得提交 main。

## 1. 代码完成

- [ ] 功能已实现，覆盖 PRD / Spec 定义的所有验收标准（AC）
- [ ] `ruff` / `ruff-format` 通过（0 error）；mypy 命中 `.ci/mypy_baseline` 无新增
- [ ] 无调试残留（`print()` / `breakpoint()` / `pdb`，诊断日志除外需标记 `[diag]`）
- [ ] 未引入跨层违规引用（遵守 AGENTS.md §3 模块边界 + 禁区表）
- [ ] 涉及引擎改动遵守 AGENTS.md SOP / `docs/project/MULTI_ENGINE_DESIGN.md` + `DI_SINGLETONS.md`
- [ ] 新增路由遵守 routes 分层约定（generate / system）

## 2. 测试覆盖

- [ ] 新增/修改函数有对应 pytest 单测（`tests/`）
- [ ] 覆盖正常路径 + 边界条件 + 异常场景
- [ ] 全量 pytest 通过（不新增失败用例）
- [ ] 涉及引擎切换/显存遵守 `test_vram_switch` 等集成约束
- [ ] 前端模板改动跑 smoke / 视觉回归（含 i18n 5 语言）

## 3. 文档同步

- [ ] `AGENTS.md` 已同步（目录结构 / 模块边界 / 配置 / 环境变量）
- [ ] 新增模块在 AGENTS.md §3 / `docs/project/ARCHITECTURE.md` 有对应条目
- [ ] 踩坑已追加到 `docs/project/KNOWN_GOTCHAS.md`（触发/现象/做法/日期）
- [ ] `CHANGELOG.md` 已记录变更（type 对应 Added/Fixed/…）
- [ ] `python scripts/check_spec_refs.py` 退出码 0（无幻影/死链/假门禁）

## 4. 公共服务 / SRE

- [ ] 涉及部署时遵守 `docs/SRE_RUNBOOK.md`（SLO / 探针 / 错误预算）与 `docs/rollback_sop.md`
- [ ] 涉及镜像发布遵守 docker / k8s 钉版（digest）约定，不用 `:latest`

## 5. 构建 & 验证

- [ ] 完整启动通过（`app/clean_launch.py`；`app_server.py` / `openai_api.py` 契约不破坏）
- [ ] 手动验证功能按预期工作（不仅是测试通过）
- [ ] 涉及引擎/权重改动时遵守模型校验（`generate_model_checksums.py` / `verify_model_weights.py`）

## 6. 安全 & 隐私

- [ ] 无硬编码密钥 / API Key / 敏感常量（走 `.env.sway` / Secret 管理）
- [ ] 新增路由遵守安全中间件（auth / csrf / rate_limit / content_safety / integrity_check）
- [ ] 未新增静默吞错（`except: pass` 等）
- [ ] 涉及禁区目录（model/ 权重、integrity、安全模块）走人工确认流程

## 7. 可追溯性

- [ ] 变更有影响评估（破坏性 / 非破坏性）
- [ ] 涉及配置变更时：`config.yaml` 结构同步 config_models + `check_config_refs.py`
- [ ] 涉及版本：`pyproject.toml` / `CHANGELOG.md` / release-please tag 一致
- [ ] 涉及 CI 变更时与 `.github/workflows/*.yml` 实际文件一致（证据绑定）