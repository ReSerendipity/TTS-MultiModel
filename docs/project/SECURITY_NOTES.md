# 11. 安全注意事项

> 本文由 2026-08-27 家族治理 E3 从 AGENTS.md §11 移出，内容逐字保留。

> **⚠️ 2026-08-27 更正**：本节 1/2/3/5 条此前引用了 `common.path_guard.safe_join`、
> `config.yaml → synthesis.max_chars`、`scripts/verify_engine.py`、`configs/model_checksums.yaml`、
> `TTSScheduler 队列满返回 503` —— **这五个标识符/配置项在本仓库全部不存在**。
> 安全类幻影比其他类幻影更危险：它会让开发者误以为防护已由公共模块提供，
> 于是不复用真实实现、也不自建校验，直接裸写 `os.path.join`。以下按实际代码重写。

1. **路径安全（无公共封装，务必复用已有实现）**
   本仓库**没有** `common/path_guard.py`，也**没有** `safe_join()` 函数（全仓搜索零命中）。
   路径防护是**分散内联实现**的，共同手法为 `os.path.realpath()` 解析 + 基目录前缀比对
   （比对基目录时要带尾部 `os.sep`，否则 `/persona` 可被 `/personaxxx` 绕过）。现有实现：

   | 实现 | 位置 | 适用 |
   |---|---|---|
   | `_safe_file_path(root_dir, user_input)` | `app/integrated_app/routes/audio.py:137` | 音频/历史文件读取（含 symlink 攻击防护） |
   | `_validate_path(base_dir, user_path)` | `app/integrated_app/routes/training.py:62` | 训练数据目录 |
   | realpath + `startswith` 前缀比对 | `app/integrated_app/persona_manager.py:168` `:562`、`persona_metadata.py:290` | 音色 wav 与打包元数据 |
   | realpath 前缀比对（禁 symlink 逃出） | `app/integrated_app/routes/generate/voxcpm2/streaming.py:821` | 流式生成写盘 |

   **新增涉及用户输入路径的代码时**：优先复用上述函数；跨模块不便复用时按同一手法实现，
   并**禁止** `os.path.join(base, 用户输入)` 后直接 `open()`。
2. **文本长度与 prompt 注入防护**
   - **不存在** `config.yaml → synthesis.max_chars`。`config.yaml` 顶层键只有
     `version` / `server` / `models` / `ui` / `history` / `logging` / `runtime` / `watermark`。
   - 实际长度上限在 `app/integrated_app/routes/tabs.py` 按引擎给出（8192 / 4096 / 3072 量级），
     分段默认值取 `get_config().generation_defaults.split_max_chars`（读取失败回退 200）。
     **改上限要改这里，不要新增一个文档里的配置键。**
   - 控制 token 白名单在 `app/integrated_app/emotion_control.py:281`：
     `_CHAT_TTS_TAG_PATTERN = re.compile(r"\[(?P<tag>laugh|uv_break|oral_(?P<oral_idx>\d))\]")`
     ——注意是方括号形式 `[laugh]` / `[uv_break]` / `[oral_N]`（此前文档写的 `<laugh>` 是错的），
     且该 token 集源自已下线的 ChatTTS，现为兼容保留。
   - 内容风险审查在 `app/integrated_app/security/content_safety.py`，
     阈值经 Pydantic 配置项 `security.content_safety_threshold` 读取，未配置时回退内置默认值。
3. **模型完整性校验（时机是"下载后"，不是"启动时"）**
   - 校验脚本：`scripts/verify_model_checksums.py`（下载模型后比对 SHA-256，防权重被篡改）
   - 辅助脚本：`scripts/verify_model_weights.py`、`scripts/check_model_paths.py`（防目录移动导致路径漂移）
   - 清单文件：**`app/integrated_app/security/integrity_manifest.json`**（不是 `configs/model_checksums.yaml`，
     后者不存在；`configs/` 目录本身也不存在）
   - 清单生成器：`scripts/generate_integrity_manifest.py`
   - 运行期自检：`app/integrated_app/security/integrity_check.py`、`integrity_selfcheck.py`
   - 权重水印密钥初始化：`scripts/init_watermark_key.py`
   > 此前文档写的"启动时 `scripts/verify_engine.py` 对 3 个引擎权重跑 SHA-256，
   > 不匹配立即终止启动"——脚本名、清单路径、触发时机三者皆错，勿照此排查问题。
4. **网络安全**：生产环境 **绝对不能 `host="0.0.0.0"`**，只监听 `127.0.0.1`
   （`config.yaml → server.host` 已是该值，`server.port` = 7869）。
   外网访问必须套 Nginx（HTTPS + Basic Auth + IP 白名单 + 反向代理限频 `/synthesize`）。
   `config.yaml` 的 `server.ssl.certfile` 目前**未生效**（配置内注释已说明 server 跑 HTTP），
   要上 HTTPS 需在 `app/clean_launch.py` 的 uvicorn 启动处配 `ssl_certfile`/`ssl_keyfile`。
5. **并发与 DoS 防护（实际机制）**
   - **限流**：`app/integrated_app/middleware/rate_limit.py`，超限返回 **`429 Too Many Requests`**。
   - **串行**：per-engine `asyncio.Semaphore`（默认容量 1），见 §3 硬约束 4 与 §7。
   - **`503` 的真实来源不是"队列满"**，而是：
     `EngineNotLoadedError`（引擎未加载，引导用户去 Settings 加载）与
     `InsufficientVRAMError`（CUDA OOM，由 `_run_with_oom_retry` 捕获降级）。
   > 此前文档写的"`TTSScheduler` 队列满（默认 10）返回 503"没有对应实现，
   > 按它去排查 503 会找错方向——遇到 503 请先看是哪个异常类抛的。

---

