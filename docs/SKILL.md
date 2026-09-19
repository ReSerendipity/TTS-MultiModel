---
name: "bilibili-repo-harvest"
description: "Harvest open-source repos from Bilibili video descriptions and comments into the project's learning list, with conflict de-dup. Invoke to find similar projects or refresh the repo list."
---

# Bilibili 开源仓库批量采集与去重入库

从哔哩哔哩视频的**简介**与**评论区**批量提取开源仓库地址，经 GitHub 元数据核验后，并入本仓「已学习仓库清单」。核心目标不是"搜得多"，而是**只产出真正未被学过的增量**。

## 何时调用

- 用户要求「去 B 站找和 XX 项目类似的开源项目」
- 要求更新 / 复扫 `docs/project/桌面宠物开源仓库清单.md` 之类的采集清单
- 需要评估某技术主题在中文社区的开源生态分布

## 0. 铁律前置：先建冲突集，再谈发现

**采集前必须先算出"项目已经学过什么"，否则产出的 80% 是重复劳动。**

1. 全仓扫描文档与源码，抽出所有仓库引用：

   ```powershell
   # 扫描范围：*.md *.json *.ts *.tsx *.rs *.toml *.mjs *.py *.yml *.yaml
   # 必须排除：node_modules / target / dist / .git / pnpm-lock.yaml / Cargo.lock
   $rxKnown = [regex]'(?i)(?:www\.)?github\.com/([A-Za-z0-9_.\-]{1,50})/([A-Za-z0-9_.\-]{1,60})'
   ```

2. **双键入库**：每个匹配同时加入 `owner/repo` 与裸 `repo` 名两个键。
   原因：同一项目换组织/换 fork 主时 URL 变、repo 名不变（实测存在 `A/DyberPet` 与 `B/DyberPet` 并存的写法）。
3. **第二通道：名称全文 grep**（仅靠 URL 会漏判，见 §6 坑 B）。

## 1. 通道选型：必须走本机 HTTP

| 能力 | WebFetch（代理出口） | 本机 HTTP（RunCommand） |
|---|---|---|
| `search/type` 站内搜索 | ❌ 412 风控 | ✅ 需 buvid3 |
| `view` 简介 / `v2/reply` 评论 | ✅ | ✅ |
| `search.bilibili.com` 网页 | ❌ 纯前端渲染，只返回页脚 | ❌ 同左 |

**结论**：发现（搜索）环节必须本机 HTTP；`search.bilibili.com` 网页任何通道都不可用，不要浪费尝试。

## 2. 反风控三件套

```powershell
$spi   = (Invoke-WebRequest 'https://api.bilibili.com/x/frontend/finger/spi' -Headers @{'User-Agent'=$agent}).Content | ConvertFrom-Json
$api   = @{
  'User-Agent' = $agent          # 真实 Chrome UA
  'Referer'    = 'https://www.bilibili.com/'   # 缺它必被拦
  'Cookie'     = ('buvid3=' + $spi.data.b_3 + '; b_nut=100')
}
Start-Sleep -Milliseconds 450     # 每次请求后节流
```

- 无 `buvid3` 时，约第 3 次搜索即触发风控；带 `buvid3` + 450~700ms 间隔可稳定连跑 40 次。
- **风控是按出口 IP 信誉判定的**，同一接口换 IP 结果相反。遇到"整批 BLOCKED"先区分是 HTTP 412 还是本地脚本异常（见 §6 坑 A）。

## 3. 关键词矩阵：按轮次由宽到窄

单轮不要超过 ~16 个词，分轮执行并逐轮复盘。**新仓库几乎不出现在头部大词里**——头部词早被历史清单吃干净了。

| 轮次 | 词型 | 示例 | 预期 |
|---|---|---|---|
| R1 | 泛词 | `桌面宠物 开源`、`桌宠 github`、`AI 桌宠` | 覆盖面，去重后 150~200 条 |
| R2 | 细分/新词 | `codex 桌宠`、`openclaw 桌宠`、`桌宠 引擎 开源`、`tauri 桌宠` | **增量主要来源** |
| R3 | 项目特色词 | 用目标项目独有特性拼词：`桌宠 记忆 上下文`、`桌宠 番茄钟`、`AI 编程 状态 桌宠` | 验证差异化定位 |

每轮记录：`videos / relevant / desc_has_repo / new_repos`，用于下一轮调词。

## 4. 提取位置优先级（三级降级）

1. **搜索结果 `description` 字段** —— 一次请求免费拿到摘要级简介，零额外成本，先吃这层。
2. **`x/web-interface/view` → `data.desc`** —— 摘要被截断时补全，同时得到 `aid` / `owner.name` / `pubdate` / `stat`。
3. **`x/v2/reply?type=1&oid=<aid>&sort=1&pn=1&ps=20`** —— 仅对"标题强相关但前两级 0 命中"的视频使用。
   - 必取三处：`data.top_replies`、`data.upper.top`（**UP 主置顶评论是仓库链接最高频位置**）、`data.replies[].content.message` 及其嵌套 `replies[]`。
   - `oid` 用 `view` 返回的 `aid`，不要用搜索结果的字段猜。

**Token 纪律**：`v2/reply` 每条评论都携带 avatar 挂件 / VIP / 勋章 / 分层渲染配置，**严禁把原始 JSON 读进上下文**（实测 8 条评论≈1000 token）。必须在脚本内正则提链后只回传结构化结果。

## 5. 提链正则与假阳性剔除

```powershell
$rxRepo = [regex]'(?i)(?:https?://)?(?:www\.)?((?:github|gitee|gitlab)\.com|gitcode\.com)/([A-Za-z0-9_.\-]{1,60})/([A-Za-z0-9_.\-]{1,70})'
# 归一化：小写、去尾部标点 [.,;)]+、去 .git
# 黑名单段：issues pull tree blob releases actions topics commit
```

必须做的清洗：
- 剥离 HTML 高亮标签：`[regex]'<[^>]+>'`（搜索结果标题含 `<em class="keyword">`）。
- **相关性过滤不可省**：`$title -match '桌宠|桌面宠物|desktop|live2d|shimeji|伴侣|...'`。漏掉它，简介里的顺带外链会整批混入（dotfiles / 终端 / 键盘工具）。
- **识别"顺带外链"**：TTS 模型、字体、素材站等常被 UP 附在简介里，它们**不是同类项目**。宁可单列一节说明，也不要混进同类清单。

## 6. 两个必踩的坑（已在本仓复现）

**坑 A — PowerShell 变量大小写不敏感。**
`$H = @{...}` 做请求头，循环内又写 `$h = 0` 做计数 → hashtable 被覆盖，报
`无法将参数 Headers 绑定：无法将 Int32 转换为 IDictionary`，随后所有请求显示 `BLOCKED`，极易误判成"B 站风控升级了"。
→ 脚本内变量名全局唯一，禁止仅大小写不同的共存；异常分支必须打印原始 Exception 文本。

**坑 B — .NET 文件 API 的相对路径不跟随 `Set-Location`。**
`[System.IO.File]::ReadAllText('docs\project\x.md')` 按**进程 CWD**（通常是用户主目录）解析，而 `gc` 按 PowerShell 当前位置解析 → 同一脚本里一半能用一半报"路径不存在"；更糟的是写操作失败后脚本继续跑完，造成**静默丢写**。
→ 传给 .NET 的路径一律绝对化（`(Resolve-Path $rel).Path`）；**改完必须回读校验**（grep 锚点 / 统计新增行数），不能只看退出码。读写含中文的 md 时用 `[System.IO.File]::ReadAllBytes` 嗅探 BOM 并以同编码写回，避免 `Set-Content` 把 UTF-8 写成 GBK。

**坑 C — 只看 URL 去重会漏判已学项目。**
文档里存在"只写项目名、不附仓库链接"的历史记录，于是该仓库会以"全新项目"身份被误报。
→ 因此 §0 要求双通道去重；命中名称但无 URL 的，标注为 **「半重叠 / 补学」** 而非「新学」，并在正文写清它已学过哪一角、未学过哪一角。

## 7. GitHub 元数据核验（入库前强制）

对每个候选仓库调用 `https://api.github.com/repos/<owner>/<repo>`，取
`stargazers_count / language / pushed_at / fork / archived / description`。

- 404 / 不可达 → 剔除（链接可能来自简介笔误或已删库）。
- `archived=true`、star=0 且 `pushed_at` 久远 → 降级为"仅供参考"。
- 用它反向判断"是否真是同类项目"（description 与 language 是最强证据）。
- 未认证限流 60 次/小时，核验阶段够用；不要在此步并发。

## 8. 产量基准（判断"这次是不是白跑"）

| 指标 | 实测参考值 | 偏离含义 |
|---|---|---|
| 搜索结果中简介含仓库链接比例 | ~15% | 明显偏低 → 关键词太泛或太偏 |
| 高播放视频（>10 万）简介+热评 20 条命中率 | **≈ 0** | 爆款桌宠普遍走"下载即用"、不开源 |
| 新仓库主要来源 | 2 千 ~ 2 万播放的腰部视频 + R2 新词 | 只在头部词里找 = 白跑 |
| 评论区独占链接 | 确实存在，但占比低 | 值得做，按 §4 门槛筛选后做 |

## 9. 入库输出规范

追加进目标清单文档（本仓为 `docs/project/桌面宠物开源仓库清单.md`），沿用其既有表头，逐列给全：

```
| 项目仓库 | 体量/栈 | 一句话介绍 | 来源视频 (BV号) | UP 主 | 发布日 | 提取位置 |
```

- **UP 主与发布日必须经 `view` 接口实测取得**，搜索结果 `author` 字段常为空，缺就标 `—`，禁止推测填写。
- 分层给结论，别只堆列表：**Tier A 建议收录 / Tier B 题材撞车不建议 / Tier C 跨形态参考 / 已剔除误报**，每层写清判定理由。
- 同步维护历史备注：若本轮解开了旧条目"不公开仓库"之类的悬案，回去在旧章节标注**已销案 + 指向新章节**，避免两处事实互相矛盾。
- 文档头部 `整理日期 / 关键词 / 来源范围 / 结果口径` 四要素一并更新，保持单一事实来源。

### 入库前必查：目标文档的 Git 跟踪状态

清单类文档常落在被忽略的目录（本仓 `.gitignore` 有 `docs/project/*`，仅对少数文件用 `!` 开白名单）。写之前先确认：

```bash
git ls-files -- docs/project/<清单文件>.md   # 空 = 未被跟踪
git check-ignore -v docs/project/<清单文件>.md
```

- **被忽略** → 采集结果属**本地研究资产**，不会随仓库发布；这是正常状态，但文档内部互相引用时不得包装成"公开链接"，涉及处按仓库既有惯例标注 `（本地文档，未随仓库发布）`。
- **被跟踪** → 该次写入即发布动作，其中引用的路径必须真实存在且同样已跟踪（AGENTS.md 铁律 #6）。
- 若确需让清单公开，须由用户决定是否新增 `!docs/project/<文件>.md` 白名单，**不得自行扩大发布范围**。

## 10. 合规边界

- 只用公开接口、匿名身份；不绕登录墙，不采集充电专属 / 大会员专属内容。
- 评论数据仅用于定位仓库链接，落库只保留 `UP 主署名 + BV 号`（合理引用出处），不存储普通用户身份信息、不导出评论原文。
- 全程节流 ≥450ms，不对 B 站做并发压测；批量任务失败优先降低频率而非加重试轰炸。
