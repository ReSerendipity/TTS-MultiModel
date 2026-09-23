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
  > **该快照已过期，2026-09-21 重测见 §6.1（open 76 ｜ high 46 ｜ medium 30 ｜ critical 0）。**
  > §0–§5 保留原样不原地改写：那是当天逐条读过代码的定性记录，台账的价值在于能看出变化。
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
| ~~P0 剩余~~（已做，见 §5） | `py/overly-large-range` 7 + `bad-tag-filter` 中 tests 2 条 → 平台上 dismiss 并写明理由 | 9 | 不动代码，先降噪 |
| P1 | `stack-trace-exposure` 统一「生产模式不回显异常细节」开关（含 `server.host != 127.0.0.1` 时强制） | 29 | 一处中间件，不逐点改 |
| P1 | `path-injection` training/openai_api/audio 三处逐条定性 | 17 | 每条要么 dismiss 理由要么进追踪表 |
| P2 | 前端 `xss-through-dom` + `incomplete-sanitization` | 13 | 逐条看数据源是否用户可控 |
| ~~决策~~（已做） | CSRF 密钥写失败改硬失败：默认 `raise`，只读部署需显式 `TTS_ALLOW_EPHEMERAL_CSRF=1` 走内存态密钥 | 1 | 已核实 `docker-compose.yml:39` 的 `./data` 是可写挂载 |
| 决策 | CodeQL 是否进 `main` 必需检查 | — | 现在加会立刻卡死所有 PR；建议先降到 <30 条再纳入 |

## 5. 处置进展（2026-09-20）

dismiss **前先确认重扫过**：最近一次 CodeQL 分析 `2026-09-19T18:05:48Z`，
commit `552b0c6`（= 当前 main），results=97。逐条 GET 校验 state 与路径未变才 PATCH。

| 告警 | 规则 | 处置 | 理由类别 |
|---|---|---|---|
| #54–#60 | `py/overly-large-range` | dismissed | `false positive`（emoji 码位区间，非可放大循环） |
| #111 #112 | `py/bad-tag-filter` @ tests/ | dismissed | `used in tests`（静态断言测试自家 HTML，非安全边界） |
| #53 | `py/unsafe-deserialization` (critical) | dismissed | `mitigated`（`weights_only=True` + #79 的 realpath 入口守卫） |

处置后：**open 110 → 99，critical 1 → 0**。剩余分布：

| rule | open |
|---|---|
| py/path-injection | 53 |
| py/stack-trace-exposure | 29 |
| js/xss-through-dom | 10 |
| js/incomplete-sanitization | 3 |
| py/bad-tag-filter | 1（`engines/voxcpm2/design.py:82`，§2.4 待定夺） |
| py/reflective-xss | 2 |
| py/url-redirection | 1 |
| py/unsafe-deserialization | 0 |
| py/clear-text-storage-sensitive-data | 1（§2.5，记录不修） |
| py/overly-large-range | 0 |

两条操作口径（踩过）：

- `dismissed_comment` **上限 280 字符**，长理由写不进 API；本表的 §2.x 才是判据的持久出处，
  告警注释里必须带 §号引用。
- `dismissed_reason` 取人读枚举 `"false positive"` / `"used in tests"` / `"mitigated"` /
  `"won't fix"`，不是 `false_positive` 这种下划线形式（422）。
- 代码改动会**推移告警行号**：#53 的汇点从 `:460` 变成 `:468`（就是我加在函数入口的
  8 行守卫），这本身是修复已进主干的旁证。

## 6. 第二批：17 条 `py/path-injection` 复核（2026-09-20，随 PR #83）

逐条读码后分三类；已 dismiss 15 条，留 2 条给修复重扫。

| 组 | 告警 | 条数 | 复核结论 | 处置 |
|---|---|---|---|---|
| `routes/audio.py` | #2 #3 #4 #5 | 4 | 误报。全部文件访问过 `_safe_file_path`：字符白名单→强制拼接 `root_dir`→`resolve()+relative_to`（注释自述防 symlink）；`:441` 的 glob 命中后再复核一次归属 | dismissed `false positive` |
| `routes/training.py` | #42–#50 | 9 | 已缓解。`pretrained_path`/`train_manifest`/`save_path` 三处在 `os.makedirs` 之前**无条件**过 `_validate_path`（`realpath` + `startswith(base + os.sep)`，带分隔符故无同名兄弟目录漏洞） | dismissed `mitigated` |
| `openai_api.py` 输出路径 | #12 #13 | 2 | 误报。sink 读的是应用自生成的输出路径（`final_path` / `_stream_file` 入参），非请求可控 | dismissed `false positive` |
| `openai_api.py` voice | #109 #110 | 2 | **真问题**：`os.path.exists(os.path.join(PERSONA_DIR, f"{body.voice}.wav"))` 把请求体原样拼进路径；命中继续合成、不命中才 400，响应差异即**存在性预言机**（受 `.wav` 后缀约束，可读面有限但仍可探测） | #83 改为 `_persona_wav_exists`（realpath 归属 + `isfile`）。**等合并重扫自然消解；若不消解再以 `mitigated` 收口** |

新旧对照实测（同一夹具）：

```
相对越界 …/secret_target        旧 -> True    新 -> False
绝对路径名（join 会丢弃 dir）    旧 -> True    新 -> False
同名兄弟目录 personas_evil/trap  新 -> False
合法音色 alice                  新 -> True
```

刻意**不用** `_validate_persona_name` 的字符白名单来挡这件事：那会误伤早期登记、名字里带
空格或全角字符的音色；遍历由 containment 挡掉即可。

累计：dismiss **25 条**（第一批 9 + #53；第二批 15），open **110 → 85**，critical **0**。

---

## 6. 2026-09-21 刷新（现状以本节为准，§0–§5 是 09-20 的快照）

本表是从 PR #81 的分支里搬进来的。那条 PR 的**主体方向被实测推翻**（"两个 lock 钉的 4.52.1
低于自家下界"其实是下界本身写错了方向，实测能跑的就是 4.52.1 —— 见
`docs/SECURITY_DEPENDABOT_TRIAGE.md` §1a 与 #103 的回退），所以 PR 关掉了；但这份逐条读过代码的
分诊台账是本仓唯一成体系的 CodeQL 记录，不该跟着一起丢。搬进来时 §0–§5 原文不动，
本节只叠加当天的测量 —— 台账要能看出变化，所以不改写历史。

### 6.1 总量与差额去向（可对上账）

| | 09-20（§0） | 09-21 实测 |
|---|---|---|
| open | 110 | **76** |
| high / medium / critical | 72 / 37 / 1 | **46 / 30 / 0** |
| dismissed | — | 32 |
| fixed | 2 | 4 |

- 差额 34 条的去向全部可解释：§5 已记的 25 条 dismiss + **§5 之后又 dismiss 的 7 条**
  + 重扫自动转 fixed 的 2 条。总数守恒：`110 open + 2 fixed = 76 + 32 + 4 = 112`，
  也就是这个窗口内没有新增告警。
- 那 7 条是 `js/xss-through-dom` #95 #96 #97 #98 #99 #105 #106，09-20 16:07 一批 dismiss，
  理由 `false positive`，平台 comment 里逐条写了插值来源（自家 Jinja 渲染的 i18n 文案 /
  元素自身 `textContent` / 脚本内字面量，均不含外部输入）。**§5 没记这一批**（它冻结在 00:28），
  属于台账滞后，不是处置缺理由。

### 6.2 现状分布（按规则 + 落点）

| rule | 级别 | 09-20 | 现在 | 落点（09-21 实测） |
|---|---|---|---|---|
| py/path-injection | high | 53 | 36 | `persona_manager.py` x18; `persona_metadata.py` x6; `routes/generate/voxcpm2/script.py` x3; `generation.py` x3; `routes/generate/voxcpm2/design.py` x3; `routes/generate/utils.py` x2; `routes/persona.py` x1 |
| py/stack-trace-exposure | medium | 29 | 29 | `routes/model.py` x11; `routes/training.py` x6; `routes/system/settings.py` x5; `routes/persona.py` x4; `routes/generate/utils.py` x2; `routes/generate/voxcpm2/streaming.py` x1 |
| js/xss-through-dom | high | 10 | 3 | 三个 tab 模板各 1：`indextts2_duration.html` / `indextts2_clone.html` / `indextts20_clone.html` |
| js/incomplete-sanitization | high | 3 | 3 | `lora_manager.html` x2; `history.html` x1 |
| py/reflective-xss | high | 2 | 2 | `routes/generate/step_audio_editx/edit.py`; `routes/generate/voicebox/convert.py` |
| py/bad-tag-filter | high | 3 | 1 | `engines/voxcpm2/design.py` x1 |
| py/url-redirection | medium | 1 | 1 | `routes/tabs.py` x1 |
| py/clear-text-storage-sensitive-data | high | 1 | 1 | `app_server.py` x1 —— 处置见 6.3 |
| py/overly-large-range | medium | 7 | **0** | 已全部 dismiss（§5 第一批） |
| py/unsafe-deserialization | **critical** | 1 | **0** | #53 `mitigated`。§2.1 声称的入口守卫**确在 main**，已按代码复核：`persona_manager.py:436-440` 的 realpath 前缀比对、`_PERSONA_NAME_RE.fullmatch`（不再是 `match`）、`tests/test_persona_embedding_load.py` 在库 |

### 6.3 §2.5 那条已落地，但形态与 §4 那行"决策（已做）"不一样

- §4 / #81 的原方案：默认 `raise`，只读部署靠 `TTS_ALLOW_EPHEMERAL_CSRF=1` 退回内存态密钥。
- **实际落地（PR #109）没有这个环境变量开关**：任何取不到密钥的情况一律 `RuntimeError` 拒绝启动，
  `CSRFMiddleware.__init__` 另补一道"空 secret 直接 ValueError"，防别的装配点绕过。
  `.env.example` 也没有引入该变量 —— 读 §4 那行时要按这个口径理解，否则会去找一个不存在的开关。
- 追加的真加固：新密钥用 `os.open(..., 0o600)` 创建（不留"先 0644 建出来、再改权限"的窗口），
  已存在的文件在读后/写前 chmod 收紧并复核；文件系统表达不了 POSIX 权限位时只 warning、
  不拒绝启动（那种情况下拒绝启动不会更安全，只会让 app 起不来）。
  3 条 POSIX 门控测试守着这件事，其中一条反空验证要求同目录 decoy 文件确实带 other 位。
- CodeQL 的"明文存储"半句用 sink 行上的 `codeql[py/clear-text-storage-sensitive-data] ignore`
  带理由抑制，不是把规则整条静音。副作用：写入位置从 `:816` 挪到 `:745`，让既有告警 #1
  在 PR 差异里被算成 "1 new alert"（这也是 #109 的 CodeQL 门禁当时红的原因）。

### 6.4 门禁口径没变（这正是它会漂走的原因）

`main` 的必需检查仍只有 3 项 —— `Lint (ruff)`、`Test (pytest) (3.12, ubuntu-latest)`、
`Typecheck (mypy ratchet)`，且 `strict=false`；**CodeQL 不在其中**。所以这张表不会因为谁没看而阻塞合并，
也正因为这样它的数字只能靠人定期重测。§4 最后那行"CodeQL 是否进 main 必需检查"仍未定，
判据不变：先降到 <30 条再纳入（现在 76）。

## 7. 2026-09-24 刷新（**现状以本节为准**，§6 是 09-21 快照）

### 7.1 总量：76 → **56**

本批 dismiss **16 条**，每条都在 CodeQL 上写了绑定代码事实的理由（注意 `dismissed_comment`
上限 280 字符，长判据只能留在本表）：

| 组 | 条数 | 判定 | 理由要点 |
|---|---|---|---|
| `routes/model.py` 的 `py/stack-trace-exposure` | 11 | `mitigated` | 全部经 `_safe_error_message`；PR **#96** 补齐该函数此前漏脱敏的四条领域异常分支，`tests/test_error_message_redaction.py` 14 断言（修复前 8 failed / 后 14 passed）。CodeQL 不建模自定义净化器，故告警留存 |
| `js/xss-through-dom`（`indextts20_clone.html:140`、`indextts2_clone.html:175`、`indextts2_duration.html:211`） | 3 | `false positive` | 告警行是 `previewAudio.src = URL.createObjectURL(file)`，只能是同源 `blob:` URL，不可能变 `javascript:`；同函数里的文件名走 `textContent`（DOM 文本赋值，非 HTML 解析） |
| `py/url-redirection` #61（`routes/tabs.py:292`） | 1 | `false positive` | `RedirectResponse(url=f"/?tab={tab_name}")` 目标是固定相对路径，用户串落在 query 值内；该文件另有 frozenset 白名单显式拦 `tab_name="../../config.yaml"` |
| `py/bad-tag-filter` #62（`engines/voxcpm2/design.py:82`） | 1 | `false positive` | 规则误用：这里去标签的对象是送进 VoxCPM2 文本编码器的提示串（另有 300 字符硬限），不是 XSS 出口；全仓 Jinja `|safe` 仅 1 处（`partials/progress_bar.html`），渲染面由 autoescape 覆盖 |

同期 PR **#153** 真修 2 条：`py/reflective-xss` #107 #108（`voicebox/convert.py`、
`step_audio_editx/edit.py` 的成功页把表单原文 `edit_type`/`edit_info`、上传文件名派生的
basename、`tau` 等直接插进 HTML 文本与 4 处属性，`basename()` 不去 `<` 与引号 →
可实现反射型 XSS；统一 `html.escape(..., quote=True)`）。
另有 PR **#98** 的 `check_pin_crossconflicts.py` 已进 main（抓上界/通配型锁冲突）。

### 7.2 剩余 40 条的逐条判定（本轮机器扫 + 人读，未在本阶段提交大改）

**`py/path-injection` 36 条**

- **19 条已缓解，可下一批直接收口**：`persona_manager.py` 的 `fn_save_persona`/`delete_persona`
  13 条（`_validate_persona_name` 白名单正则 + `realpath` 前缀比对）、`load_persona_embedding`
  5 条（#79 在函数入口加的 realpath 守卫，越界返回 None，有 `Containment` 测试）、
  `routes/persona.py:108` `_resolve_generated_audio` 1 条（函数体内有 realpath + 前缀判定）。
- **17 条无强校验，需人读或真修**（按簇）：
  `generation.py:preprocess_and_save_temp` **3**（函数体内无任何已知净化器）、
  `persona_metadata.py:load/save_persona_metadata` **6**（同上）、
  `routes/generate/utils.py:resolve_persona_ref` **2**（仅 `os.path.basename`，弱）、
  `voxcpm2/design.py:generate_voxcpm_design` **3** 与 `voxcpm2/script.py:generate_voxcpm_script`
  **3**（仅 basename）。
  共同形态：外部串经 `basename()` 后参与拼路径；`basename` 挡遍历但挡不住同目录内的
  指向与命名混淆。要收口建议统一走 `persona_manager` 那套（白名单 + realpath 前缀），
  而不是每处再写一遍 basename。

**`py/stack-trace-exposure` 18 条**

- **5 条确认为真**：`routes/system/settings.py` 的 725 / 769 / 802 / 819 / 852 五处把
  **`str(exc)` 原文放进响应**。修法是走 `model.py::_safe_error_message` 那套（或泛化文案），
  本阶段未改。
- 13 条待读：`training.py` 5（`start_training` 结构化响应，需确认是否夹 exc 原文）+
  `get_training_log:601` 1（日志接口，返回内容可能本就是设计）、`routes/persona.py` 4、
  `routes/generate/utils.py:829/1096` 2（有 `html.escape`，但 **escape 只防 XSS，不防信息泄露**，
  不能拿它给这族交差）、`voxcpm2/streaming.py:526` 1。

### 7.3 与 issue #97 / #99 的衔接

- **#97（锁集自相矛盾）已按「退」路解决**：main 锁内为 `antlr4 4.9.3`、`mpmath 1.3.0`、
  `tokenizers 0.21.0`、`transformers 4.52.1`，且 `pyproject.toml`/`requirements.txt` 同步把
  声明收窄成 `transformers>=4.52.1,<4.53`、`tokenizers>=0.21.0,<0.22` —— 声明与锁一致了。
  **仍开放的代价**：注释里写明 4.52.x 带 16 条 transformers 公告（按 api.osv.dev 实测），
  但没看到 pip-audit 对这 16 条的命中/豁免说明，属于"已知未结"。
- **#99（前端 XSS）**：其中 3 处真问题（文件名进 `innerHTML` 的 `onclick` 属性，只做了 JS
  字符串转义）在告警表里已消失（`js/incomplete-sanitization` 整族为 0），说明已被修；
  具体修法本轮未复核。
