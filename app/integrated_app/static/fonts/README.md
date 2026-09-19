# 标题字体（self-hosted fonts）

## 现在装了什么（2026-09-19 实测）

| 项 | 数量 | 体积 |
| --- | --- | --- |
| `*.woff2` | 776 个（14 个家族 × unicode-range 子集） | 与下行合计 **25.5 MB** |
| `ZhiMangXing-0.ttf` | 1 个（志莽行书上**游只有 TTF**，无 woff2 子集） | 3.9 MB |
| `licenses/*.OFL.txt` | 14 个（每个家族的 OFL 1.1 全文） | ~56 KB |
| `manifest.json` | 776 条：文件名 ↔ 来源 URL ↔ unicode-range | 620 KB |
| `../css/fonts.local.css` | 776 条 `@font-face` | **820 KB**（每次首屏解析一次） |

`base.html` 里的 `<link rel="stylesheet" href="/static/css/fonts.local.css">` **已启用**，
所以字体菜单的 14 项现在都应点亮为可用（判据见下）。

体积是这次实测出来的，不是估的：早先按"每族若干 MB"估成 13 MB 偏低，真实 25.5 MB。
只想要一部分，用 `--only` 重跑即可（脚本不删已有文件，删除要人工确认）。

## 这里原来有什么

| 文件 | 用途 | 来源 |
| --- | --- | --- |
| `InterVariable.woff2` / `InterVariable-Italic.woff2` | 正文与 UI 字体，已在 `static/css/variables.css` 里 `@font-face` 声明 | Inter，SIL OFL 1.1 |
| `README.md` | 本文件 | — |

**14 个「标题字体」曾经不在这里。** 它们靠 `<link href="https://fonts.googleapis.com/css2?family=…">`
外链加载，但 `middleware/security_headers.py` 把 CSP 钉成
`style-src 'self' 'unsafe-inline'` + `font-src 'self' data:`，外链样式表与字体文件都会被浏览器拦掉。
结果是：字体菜单照样列出 14 个家族、点了照样写 `localStorage`，但页面字形一个都不会变——静默失效。

菜单现在会实测（`base.html` 里的 `_fontAvailable()`：把「目标家族 + 已知回退」和「单独那个回退」各渲染一次量宽度，
宽度相同即本机没有该字体），未安装的条目显示 `未安装` 并置灰。所以**只要把文件放进本目录并打开链接，菜单会自己亮起来**，
不需要改前端代码。

> `document.fonts.check()` 和 computed `font-family` 都不能用来判断：本机没装的家族，前者返回 `true`、
> 后者照样回显你写进去的名字。只有渲染宽度这一条信号是诚实的。

## 菜单里的 14 个家族（全部 SIL OFL 1.1）

| 分组 | 菜单名 | CSS `font-family` | 许可 |
| --- | --- | --- | --- |
| 中文现代 | 思源黑体 | `"Noto Sans SC"` | OFL 1.1 |
| 中文现代 | 思源宋体 | `"Noto Serif SC"` | OFL 1.1 |
| 中文现代 | 站酷小薇 | `"ZCOOL XiaoWei"` | OFL 1.1 |
| 中文现代 | 站酷庆科黄油体 | `"ZCOOL QingKe HuangYou"` | OFL 1.1 |
| 中文现代 | 站酷快乐体 | `"ZCOOL KuaiLe"` | OFL 1.1 |
| 中文书法 | 马善政楷书 | `"Ma Shan Zheng"` | OFL 1.1 |
| 中文书法 | 龙藏手书 | `"Long Cang"` | OFL 1.1 |
| 中文书法 | 志莽行书 | `"Zhi Mang Xing"` | OFL 1.1 |
| 中文书法 | 柳建茂草书 | `"Liu Jian Mao Cao"` | OFL 1.1 |
| 西文艺术 | Playfair Display | `"Playfair Display"` | OFL 1.1 |
| 西文艺术 | Cinzel | `"Cinzel"` | OFL 1.1 |
| 西文艺术 | Great Vibes | `"Great Vibes"` | OFL 1.1 |
| 西文艺术 | Pacifico | `"Pacifico"` | OFL 1.1 |
| 西文艺术 | Dancing Script | `"Dancing Script"` | OFL 1.1 |

OFL 允许随发行包再分发，条件是：不得单独售卖字体本身、保留版权声明、改名需申请保留字体名（ Reserved Font Name）。
因此新增字体必须同步在仓库根目录 `THIRD_PARTY_NOTICES.md` 追加一条（家族名 / 作者 / OFL 1.1 / 上游 URL / 文件清单与 SHA-256）。

## 怎么重生成（联网那一步由你决定何时执行）

统一走 `scripts/fetch_title_fonts.py`，四档 I/O 分得很清楚：

```bash
python scripts/fetch_title_fonts.py --list        # ① 0 请求 0 字节：只打印计划
python scripts/fetch_title_fonts.py --resolve     # ② 14 个小 CSS 请求，不传字体字节，写 manifest.json
python scripts/fetch_title_fonts.py --apply       # ③ 传 776 个字体文件 + 生成 fonts.local.css
python scripts/fetch_title_fonts.py --licenses    # ④ 14 个 OFL.txt → licenses/（再分发义务，缺则发布阻断）
```

只要一部分：`--apply --only "Cinzel,Pacifico,Noto Serif SC"`。脚本不删、不覆盖已有字体，
字节数一致的文件跳过，可反复执行；`--licenses` 有任何一个家族拿不到全文就返回非 0。

**镜像坑**：这台机器上 `css2` 返回的字体 URL 域名是 `fonts.gstatic.font.im`（不是
`fonts.gstatic.com`）。脚本的下载白名单已含常见 gstatic 镜像；换环境后若被拒，
显式加 `--allow-host <域名>`，不要改成放开任意 host。

## 验证（不要凭眼睛看，眼睛会接受回退字形）

```bash
# 菜单侧：可用的条目数应等于已安装的家族数
# 浏览器控制台执行：
document.querySelectorAll('#fontPop button[data-avail="1"]').length
document.querySelectorAll('#fontPop button[data-avail="0"]').length
```

页面级：选中某字体后 `document.documentElement.style.getPropertyValue('--tts-font')` 应变化，
且标题元素的渲染宽度**必须**跟着变化（宽度不变 = 字体其实没生效）。

## 打包影响

- `pyproject.toml` 的 `[tool.setuptools.package-data]` 已含 `static/**/*`，新 woff2 会自动进 wheel，无需登记。
- 但它们会直接算进发行包体积；`data/` `outputs/` 之类不受影响。
- 若只想覆盖常用汉字，可不用本脚本，改用 Google 的 `&text=` 子集接口手工产一份小文件：
  `https://fonts.googleapis.com/css2?family=Noto+Serif+SC&text=标题字体示例文字&display=swap`
  （返回的 woff2 只含这些字形，几十 KB，但菜单里其他文字仍会回退。）
