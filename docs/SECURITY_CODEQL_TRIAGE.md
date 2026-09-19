# CodeQL 存量告警分诊表（main 分支）

> 目的：把 GitHub 代码扫描里 **110 条 open 告警**从「CI 装饰物」变成可处置清单。
> 本表只登记**已读代码定性**的结论；未审的一律标 `未审`，不假装看过。
> 配套：`docs/SECURITY_REMEDIATION_TRACKER.md`（配置-实现一致性门禁）、`docs/SECURITY_AUDIT_TTS_MultiModel.md`。

## 0. 重新生成本表的统计口径

```bash
gh api --paginate "repos/ReSerendipity/TTS_MultiModel/code-scanning/alerts?state=open&per_page=100" \
  --jq '.[] | [.number,.rule.id,.rule.security_severity_level,.most_recent_instance.location.path,
               (.most_recent_instance.location.start_line|tostring)] | @tsv'
```

- 时点：2026-09-20。**open 110 ｜ high 72 ｜ medium 37 ｜ critical 1**（另有 fixed 2）。
- 检测窗口：`created_at` 从 2026-09-10 到 2026-09-19 —— 说明**边修边涨**，不是一次性历史包袱。
- 为什么能累积到 110 条还不阻塞：`main` 分支保护只要求 3 个检查
  （`Lint (ruff)`、`Test (pytest) (3.12, ubuntu-latest)`、`Typecheck (mypy ratchet)`），
  **CodeQL 不是必需检查**，且 `required_status_checks.strict=false`。

## 1. 按规则分布

| rule | 级别 | 条数 | 落点（文件 x 条数） |
|---|---|---|---|
| py/path-injection | high | 53 | `app/integrated_app/persona_manager.py` x18; `app/integrated_app/routes/training.py` x9; `app/integrated_app/persona_metadata.py` x6; `app/integrated_app/openai_api.py` x4; `app/integrated_app/routes/audio.py` x4; `app/integrated_app/generation.py` x3; `app/integrated_app/routes/generate/voxcpm2/design.py` x3; `app/integrated_app/routes/generate/voxcpm2/script.py` x3; `app/integrated_app/routes/generate/utils.py` x2; `app/integrated_app/routes/persona.py` x1 |
| py/stack-trace-exposure | medium | 29 | `app/integrated_app/routes/model.py` x11; `app/integrated_app/routes/training.py` x6; `app/integrated_app/routes/system/settings.py` x5; `app/integrated_app/routes/persona.py` x4; `app/integrated_app/routes/generate/utils.py` x2; `app/integrated_app/routes/generate/voxcpm2/streaming.py` x1 |
| js/xss-through-dom | high | 10 | `app/integrated_app/static/js/audio_player.js` x2; `app/integrated_app/static/js/help_drawer.js` x2; `demo/index.html` x2; `app/integrated_app/static/js/tts_form.js` x1; `app/integrated_app/templates/tabs/indextts20_clone.html` x1; `app/integrated_app/templates/tabs/indextts2_clone.html` x1; `app/integrated_app/templates/tabs/indextts2_duration.html` x1 |
| py/overly-large-range | medium | 7 | `app/integrated_app/text_frontend.py` x7 |
| py/bad-tag-filter | high | 3 | `app/integrated_app/engines/voxcpm2/design.py` x1; `tests/test_a11y_static.py` x1; `tests/test_fe_be_consistency.py` x1 |
| js/incomplete-sanitization | high | 3 | `app/integrated_app/templates/tabs/lora_manager.html` x2; `app/integrated_app/templates/tabs/history.html` x1 |
| py/reflective-xss | high | 2 | `app/integrated_app/routes/generate/step_audio_editx/edit.py` x1; `app/integrated_app/routes/generate/voicebox/convert.py` x1 |
| py/clear-text-storage-sensitive-data | high | 1 | `app/integrated_app/app_server.py` x1 |
| py/url-redirection | medium | 1 | `app/integrated_app/routes/tabs.py` x1 |
| py/unsafe-deserialization | **critical** | 1 | `app/integrated_app/persona_manager.py` x1 |

## 2. 已读代码定性的族

### 2.1 `py/unsafe-deserialization` #53 —— critical，已处置（本次）

- 汇点：`app/integrated_app/persona_manager.py:460`
  `raw = torch.load(pt_path, map_location="cpu", weights_only=True)`
- `weights_only=True` 已挡掉任意对象反序列化（RCE 面），CodeQL 未把它建模为净化器，
  所以「不安全反序列化」这半句是**误报**。
- 但告警的成立前提「**攻击者能否决定 `pt_path` 指向哪个文件**」经查**成立**：
  读路径 `load_persona_embedding(name)` 自身不校验，靠调用方各自
  `os.path.basename(persona_name)`（如 `routes/generate/utils.py:1238`）兜底；
  `service_layer.py:1082`、`model_manager_core/load.py:128` 直接传原始 `name`。
- 处置：把 `PERSONA_DIR` 越界判定（`realpath` 前缀比对，与文件里 `_save`/`_delete`
  已有的第二段防线同一写法）收敛进 `load_persona_embedding` 入口，
  置于内存缓存读取之前，避免越界 key 命中缓存。
- 证据（先证明测试会咬，再证明修复有效）：
  - 撤掉守卫：`tests/test_persona_embedding_load.py -k Containment` → **2 failed**，
    失败原因是走到了 `EngineNotLoadedError`，即越界名确实被当成正常音色继续处理；
  - 加回守卫：**10 passed**（7 条原有 + 3 条新增）。

### 2.2 `py/path-injection` 53 条 —— 主体是「净化器未被建模」，但有一处真加固点

已核实存在的两层防御（`persona_manager.py` 头部注释即为其自述）：

1. 白名单正则：`app/integrated_app/config.py:873`
   `_PERSONA_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-\u4e00-\u9fff]{1,50}$")`
   —— 两端锚定，`/ \ . ~ :` 全部排除，遍历字符进不来。
2. `os.path.realpath` + `startswith(realpath(PERSONA_DIR))` 前缀比对
   （`persona_manager.py:179-180`、`595-596`）。

残留问题（已随 2.1 一并处理 / 待处理）：

- 防御分散在调用点（`basename`）而非汇点 —— 已在 2.1 收敛到 `load_persona_embedding`。
- `_validate_persona_name` 用的是 `_PERSONA_NAME_RE.match(...)` 而非 `fullmatch`：
  Python 的 `$` 允许结尾一个换行，`"alice\n"` 能通过校验。影响仅限「造出带换行的同基名文件」，
  不是遍历向量；已一并改为 `fullmatch`。
- `routes/training.py` x9、`openai_api.py` x4、`routes/audio.py` x4 这三处**未逐条审**，
  是下一批（见 §4 P1）。

### 2.3 `py/overly-large-range` 7 条 —— 判定误报

`app/integrated_app/text_frontend.py:222` 起是 `_EMOJI_PATTERN` 的字符类
（`"\U0001f600-\U0001f64f"` 等）。CodeQL 把正则里的超大码位区间当成可放大循环；
实际是表情符号清洗的必要码位覆盖，不随输入长度放大 → **7 条同一处，建议一次性 dismiss
并附本表链接作为理由**。

### 2.4 `py/bad-tag-filter` 3 条 —— 2 条误报，1 条待定夺

- `tests/test_a11y_static.py:38`、`tests/test_fe_be_consistency.py:602`：测试里解析 HTML 做静态断言，
  不是安全边界 → 误报。
- `engines/voxcpm2/design.py:82`：`html.unescape` 后用 `<[^>]+>` 正则去标签，
  属 CodeQL 该规则的经典形态（正则去标签可被嵌套/截断构造绕过）。
  该值是送进 TTS 的前端文本、出站处另有 `html.escape`，故按**低危待定夺**登记，不改。

### 2.5 `py/clear-text-storage-sensitive-data` #1（`app_server.py:816`）—— 真模式 + 一个附带发现

- 明文落盘的是 CSRF HMAC 密钥 `data/.csrf_secret`（`_secrets.token_urlsafe(48)` 生成）。
  单机本地优先形态下这是常规取舍：`data/.csrf_secret` 已在 `.gitignore`，不随包分发。→ 记录不修。
- **附带发现（比告警本身更值得处理）**：同一块 `try` 的 `except OSError` 分支
  只 `logger.warning` 后以 `csrf_secret=""` 继续挂载 `CSRFMiddleware`，
  注释自称「回退到无签名模式」—— CSRF 防护会在密钥写不出来时**静默关闭**。
  这与「资源不足要硬失败，不许警告后照旧继续」的既定纪律冲突。
  未直接改成硬失败的原因：会影响只读挂载 / Docker `data/` 不可写的部署形态，需要单独定夺。

## 3. 未审（不假装看过）

| 族 | 条数 | 缺什么 |
|---|---|---|
| `py/stack-trace-exposure` | 29 | 只读了 `routes/generate/utils.py:1096`（HTMLResponse 里回显 `error_message`，已 `html.escape`）。**设计取舍类**：本地工具把异常细节回显给 UI。要先定「是否允许绑定非回环地址」这条前提，再决定是 29 处逐个改还是加统一「生产模式脱敏」开关 |
| `js/xss-through-dom` | 8/10 | 只确认 `demo/index.html` 2 条（`steps` 为脚本内字面量，无外部输入）。其余 8 条在 `static/js/*.js` 与 3 个 tab 模板，未审 |
| `js/incomplete-sanitization` | 3 | `lora_manager.html` x2、`history.html` x1，未审 |
| `py/reflective-xss` | 2 | `voicebox/convert.py:204`、`step_audio_editx/edit.py:214` 的 `HTMLResponse(content=...)` 插值项是否全部过 `html.escape`，未审 |
| `py/url-redirection` | 1 | `routes/tabs.py:287`（该处只见 `logger.exception`，重定向在 enclosing 块），未审 |
| `py/path-injection` | 17/53 | `routes/training.py` x9、`openai_api.py` x4、`routes/audio.py` x4 未逐条审 |

## 4. 分批处置建议

| 批次 | 内容 | 量 | 判据 |
|---|---|---|---|
| ~~P0~~（已做） | #53 可达路径封死 + 3 条回归测试 + `fullmatch` | 1 + 18 收敛 | 撤守卫必红、加回必绿 |
| P0 剩余 | `py/overly-large-range` 7 + `bad-tag-filter` 中 tests 2 条 → 平台上 dismiss 并写明理由 | 9 | 不动代码，先降噪 |
| P1 | `stack-trace-exposure` 统一「生产模式不回显异常细节」开关（含 `server.host != 127.0.0.1` 时强制） | 29 | 一处中间件，不逐点改 |
| P1 | `path-injection` training/openai_api/audio 三处逐条定性 | 17 | 每条要么 dismiss 理由要么进追踪表 |
| P2 | 前端 `xss-through-dom` + `incomplete-sanitization` | 13 | 逐条看数据源是否用户可控 |
| 决策 | CSRF 密钥写失败是否改硬失败 | 1 | 需先确认部署形态 |
| 决策 | CodeQL 是否进 `main` 必需检查 | — | 现在加会立刻卡死所有 PR；建议先降到 <30 条再纳入 |
