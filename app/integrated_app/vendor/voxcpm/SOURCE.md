# VoxCPM Vendor Source Baseline

## Upstream Information

- **Upstream Project**: VoxCPM by OpenBMB (面壁智能)
- **Repository**: https://github.com/OpenBMB/VoxCPM （包在仓库的 `src/voxcpm/` 下，**不是**顶层 `voxcpm/`）
- **License**: Apache-2.0 (Copyright 2025 OpenBMB)
- **Package name**: `voxcpm` (as seen in `__init__.py` and module structure)
- **Anchor commit**: `f772e498a45fbb5fb8e13fbf9b9c48be9fe33e69`（main，2026-09-02）
- **Machine-readable state**: `configs/upstream_sync_state.json`（逐文件 git blob SHA，由探针维护）

## What's Vendored Here

This directory (`app/integrated_app/vendor/voxcpm/`) is a **1:1 copy of upstream `src/voxcpm/`** —
38 files in, 38 files out, no additions and no omissions as of the anchor commit above.

```
vendor/voxcpm/
├── __init__.py
├── cli.py                     # CLI（voxcpm design / clone / batch）
├── core.py                    # VoxCPM 门面类：from_pretrained / generate / generate_streaming
├── zipenhancer.py             # 降噪（ZipEnhancer）
├── model/
│   ├── utils.py               # pick_runtime_dtype（MPS 上强制降级 fp32）
│   ├── voxcpm.py              # VoxCPMModel —— architecture="voxcpm"（VoxCPM-0.5B / 1.5 走这条）
│   └── voxcpm2.py             # VoxCPM2Model —— architecture="voxcpm2"（VoxCPM2，48kHz）
├── modules/                   # audiovae / layers(lora) / locdit / locenc / minicpm4
├── timestamps/                # 时间戳后处理
├── training/                  # 训练数据/加速器/tracker 等工具
└── utils/text_normalize.py
```

## Known Modifications

四处分歧全部经 `diff -u` 实测确认（上游内容取自 anchor commit 的 `raw.githubusercontent.com`）：

| File | Modification | 行为影响 | Date |
|------|--------------|---|------|
| `core.py` | ① `VoxCPM.from_pretrained()` 增加 `revision: Optional[str]` 参数，并传给 `snapshot_download(repo_id=..., revision=...)`，用于固定 HF 版本；② `_generate()` 增加 `streaming_prefix_len: int = 4` 形参并转发给 `tts_model._generate_with_prompt_cache(...)`——该形参上游模型层早已存在，只是门面层从未透出，导致流式首包/断裂权衡无从调节 | 均有（①默认 None 时与上游等价；②默认 4 与上游模型层默认一致） | 2026-09-22 |
| `model/voxcpm.py` | `LlamaTokenizerFast.from_pretrained(path)` 行尾加 `# nosec B615` 注释 | 无 | 2026-09-22 核对 |
| `model/voxcpm2.py` | 同上，`# nosec B615` | 无 | 2026-09-22 核对 |
| `training/data.py` | `load_dataset("json", ...)` 行尾加 `# nosec B615` | 无 | 2026-09-22 核对 |

> 更正记录：本文件此前只登记了 `core.py` 一项，且把它的改动描述成"added local filesystem path
> support / adjusted device handling"——与实际 diff 不符，实际只有 `revision` 一处。另外三处
> `# nosec` 改动从未登记。2026-09-22 由上游同步探针查出并回改。

## Sync Checking

不要手工 curl 单文件比对（旧版本这里给的 URL 路径 `main/voxcpm/core.py` 是错的，上游实际在
`src/voxcpm/`）。统一用仓库内的探针：

```bash
python scripts/check_upstream_sync.py            # 联网比对上游 HEAD，出 docs/reports/ 报告
python scripts/check_upstream_sync.py --offline   # 只看本地漂移
python scripts/check_upstream_sync.py --init      # 吸收上游更新后刷新锚点
python scripts/check_upstream_sync.py --strict     # 有未登记本地改动则非零退出（供门禁用）
```

探针用 git blob SHA-1 比对，只需 1 次 recursive tree 请求，不下载文件内容。

## Upgrade Strategy

1. `python scripts/check_upstream_sync.py` —— 先看 `SYNC_LAG` / `CONFLICT` 清单。
2. 对 `SYNC_LAG` 文件按锚点 commit→HEAD 的差异逐项决定是否吸收；`CONFLICT` 必须人工合并。
3. 吸收后如本地新增了改动，写进上面的 Known Modifications 表 **并** 同步登记到
   `configs/upstream_sync_state.json` 的 `registered_modifications`，否则探针会以 `LOCAL_DRIFT`
   （未登记）报出来。
4. 全部处理完再 `--init` 前移锚点。
5. 回归：`pytest tests/ -q --ignore=tests/e2e` + `python scripts/check_engine_compat.py`。

## License Compliance

All files in this directory retain their original `Copyright 2025 OpenBMB` notice and are licensed
under Apache-2.0 per the upstream license. See upstream repository for full license text.

---

*Last verified against upstream*: 2026-09-22 @ `f772e498a4`（34/38 逐字节相同，4 处差异见上表，均为本地改动）
