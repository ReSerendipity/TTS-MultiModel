# Third-Party Notices（第三方组件声明）

> 更新日期：2026-09-02。本清单非穷尽：完整依赖以 `requirements.txt` / `requirements-lock.txt`
> 及安装环境的 `pip freeze` 为准；各组件许可以其官方仓库与包内 LICENSE 为准。

## 项目主许可

TTS_MultiModel 项目代码采用 [Apache License 2.0](LICENSE)。

## 模型权重许可（独立于代码，见 USER_AGREEMENT / NOTICE）

| 引擎/模型 | 许可 | 商用说明 |
|---|---|---|
| VoxCPM2 + SenseVoiceSmall + ZipEnhancer | Apache-2.0 | 可直接商用，保留版权声明 |
| IndexTTS2 | bilibili Model Use License | **商用需书面授权**，核对 bilibili 官方条款 |

## 主要 Python 依赖（许可类型为常见归类，以各包 LICENSE 为准）

| 组件 | 常见许可类型 | 说明 |
|---|---|---|
| torch / torchaudio | BSD-3-Clause | 推理训练框架 |
| fastapi | MIT | Web 框架 |
| uvicorn | BSD-3-Clause | ASGI 服务器 |
| pydantic / pydantic-core | MIT | 数据校验 |
| aiohttp | Apache-2.0 | 异步 HTTP 客户端 |
| websockets | BSD-3-Clause | WebSocket |
| numpy | BSD-3-Clause | 数值计算 |
| soundfile / librosa | BSD-3-Clause / ISC | 音频处理 |
| transformers | Apache-2.0 | 模型库 |
| safetensors | Apache-2.0 | 权重加载 |
| sentencepiece | Apache-2.0 | 分词器 |
| audioread | MIT | 音频读取 |
| dataclasses / typing_extensions | Apache-2.0 | 标准扩展 |

## vendored / vendor 目录

### `vendor/voxcpm`（VoxCPM2 引擎代码）

- **上游**: OpenBMB/VoxCPM（vendored 目录为本地保留、未随仓库发布；`vendor/voxcpm/SOURCE.md` 源码出处说明为本地文件）
- **许可**: Apache-2.0（与项目主许可一致）

### `vendor/tn`（文本规范化，中文/英文）

- 常见许可：Apache-2.0 / BSD 类，以包内 LICENSE 与 SOURCE 说明为准

## 字体（`app/integrated_app/static/fonts/`）

随发行包再分发的字体全部为 **SIL Open Font License 1.1**（OFL 允许再分发，条件是不得单独售卖字体本身、
须保留版权声明与授权全文、改名需另行申请保留字体名）。

| 字体 | 许可 | 上游 | 落地方式 |
|---|---|---|---|
| Inter（`InterVariable*.woff2`） | OFL 1.1 | rsms/inter | 直接入库，`static/css/variables.css` 声明 |
| Noto Sans SC / Noto Serif SC | OFL 1.1 | google/fonts | `scripts/fetch_title_fonts.py --apply` |
| ZCOOL XiaoWei / QingKe HuangYou / KuaiLe | OFL 1.1 | google/fonts（站酷） | 同上 |
| Ma Shan Zheng / Long Cang / Zhi Mang Xing / Liu Jian Mao Cao | OFL 1.1 | google/fonts（中文书法类） | 同上 |
| Playfair Display / Cinzel / Great Vibes / Pacifico / Dancing Script | OFL 1.1 | google/fonts | 同上 |

- 每个家族的完整文件清单（文件名 + 来源 URL + unicode-range）由 `--resolve` 写在
  `static/fonts/manifest.json`；OFL 授权全文随各上游仓库的 `OFL.txt` 提供，打包发行时应一并放入
  `static/fonts/licenses/`（缺该目录视为发布阻断项）。
- 之所以自托管：CSP 的 `style-src`/`font-src` 只允许 `'self'`，外链 Google Fonts 会被浏览器静默拦掉。
- 注意 `Zhi Mang Xing`（志莽行书）上游只提供 TTF，无 woff2 子集，单文件约 3.9 MB。

---

*疑问或遗漏请通过 Issues 反馈。*