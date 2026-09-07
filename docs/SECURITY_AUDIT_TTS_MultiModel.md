# 安全审计 — TTS_MultiModel

> 只读审计 · 快照版 · 审计日期：2026-09-02
> 审计对象：FastAPI + 多引擎语音合成平台（VoxCPM2 / IndexTTS2，含训练、LoRA、声音克隆能力）
> 方法：静态扫描 + git 追踪面核查 + 配置校验器审查。未做动态渗透。

## 执行摘要（总体评级：中 / Medium）

无凭据入库、依赖锁定齐全、host 绑定校验器为已有机制（含 0.0.0.0 场景的认证安全网）。克隆/训练能力带来更高的内容滥用风险面，但代码侧已有 content_safety 过滤与声音水印。已确认项见下。

## 已验证项（✓ = 本次核查通过）

### 1. 凭据 / 密钥
- **✓ 无密钥入库**：`git ls-files` 扫描 `.env`、`*.jks`、`*.keystore`、`*.pem`、`*.key`、`secrets/`、`credential` 均无命中；仅存在 `scripts/init_watermark_key.py`（初始化脚本，密钥本体不入库）。
- **✓ 水印密钥无默认值**：`app/integrated_app/watermark.py` 密钥取自环境变量或 `.watermark_key`，无硬编码默认。
- **✓ .gitignore 覆盖**：`.env`、`.watermark_key`、密钥/凭据类路径均已忽略。

### 2. 网络暴露 / 绑定
- **✓ host 校验 + 认证安全网**：`app/integrated_app/config_models.py:136-143` 允许 loopback 或 `0.0.0.0`，但 `0.0.0.0` 仅在 `app_server.py:997` 的认证安全网放行时生效（无认证不可暴露所有 `/api/*`）。
- **✓ CORS origin 收敛**：`app_server.py:785-787` 已移除无效 origin `0.0.0.0`，避免误导运维。

### 3. 依赖供应链
- **✓ 锁文件齐全**：`requirements-lock.txt`（pip-compile 全量 pin）。
- **✓ vendor 分离**：`vendor/` 为独立目录（voxcpm 引擎、文本规范化），与项目代码隔离。

### 4. 内容滥用风险（克隆能力）
- **✓ 过滤与溯源**：`security/content_safety.py` + 音频水印（source_id + 哈希）已内置；`USER_AGREEMENT.md` 明确禁止未授权克隆他人声音。

## 待关注点位（非阻断）

| # | 级别 | 点位 | 建议 |
|---|---|---|---|
| 1 | Medium | 声音克隆/训练（LoRA、VoxCPM2 fine-tune）能力可被用于未授权克隆 | 保持 content_safety 过滤与水印的默认强制；商用场景核对各引擎权重许可（IndexTTS2 为 bilibili Model Use License，商用需书面授权） |
| 2 | Low | 0.0.0.0 场景依赖认证安全网 | 生产部署保持认证开启，勿直连公网；参考 deploy/kubernetes/ 配置 |
| 3 | Info | vLLM 后端（`vllm_backend.py`）可选加速路径 | 仅本机/受控环境启用 |

## 门禁适用性说明

本仓库 `scripts/check_config_refs.py` / `check_spec_refs.py` 已覆盖 config 键消费与规范文件引用一致性；`docs/SECURITY_REMEDIATION_TRACKER.md` 持续跟踪历史修复。

---

*快照审计，非正式安全承诺；建议在每次大版本发布前复核。*