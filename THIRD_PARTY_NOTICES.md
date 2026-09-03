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

---

*疑问或遗漏请通过 Issues 反馈。*