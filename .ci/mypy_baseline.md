# mypy 基线口径说明

> 创建日期：2026-08-31（MLOps 治理 P1-2）
> CI 读取的基线是 `.ci/mypy_baseline.txt` 中的**纯数字**（CI 用 `tr -d '[:space:]'` 解析），
> 本说明文件**不被 CI 解析**，仅作为口径文档。评估报告曾指出 141 / 220 / 268 三处口径矛盾，
> 此处统一。

## 三种口径

| 口径 | 数值 | 含义 |
|---|---:|---|
| CI 门禁（`typecheck` job） | **141** | 仅装 mypy，不装项目依赖（torch/soundfile 等第三方符号为 `Any`）；命令见 `ci.yml` |
| 本机全依赖 venv | **268** | 装齐 torch/soundfile/datasets 等后，底层 attr-defined/assignment 错误全部浮现 |
| 旧 AGENTS.md 描述 | 220 | 过时估计，已废弃；统一改为引用上面两种真实口径 |

## 重新生成基线（改 mypy 版本或 `typecheck` 依赖集时必跑）

```bash
pip install mypy==<同版本>
mypy app/integrated_app/ --ignore-missing-imports --no-error-summary | grep -c 'error:'
```

将输出整数写入 `.ci/mypy_baseline.txt`（**仅数字，不要加注释**，否则 CI 的
`tr -d '[:space:]'` 解析会得到非数字而报错）。

## 门禁逻辑（见 `ci.yml` typecheck job）

- `COUNT > BASELINE` → 红（类型债务上升，禁止合并）
- `COUNT == BASELINE` → 绿
- `COUNT < BASELINE` → 绿 + warning 提示把 `.ci/mypy_baseline.txt` 下调以固化成果
