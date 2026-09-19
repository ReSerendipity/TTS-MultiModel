# 贡献指南（CONTRIBUTING）

感谢关注 TTS MultiModel！本文件给出从克隆到合并的最短路径。

## 1. 环境搭建

```bash
pip install -r requirements.txt
# 启动（端口 8810）
python app/clean_launch.py
```

## 2. 本地开发循环

| 场景 | 命令 |
|---|---|
| Lint（ruff） | `ruff check app` |
| 格式（ruff-format） | `ruff format app` |
| 单测 | `pytest -q` |
| API 契约快照 | `pytest tests/test_openapi_contract_snapshot.py -q` |

## 3. 分支与提交规范

- 从最新 `main` 切出：`git checkout -b feat/xxx`。
- **Conventional Commits**：`feat(scope): 描述` / `fix(scope): 描述` / `docs:` / `ci:` / `test:` / `chore:`。
- **DCO 签名**：每个提交必须 `git commit -s`。
- 提交层钩子自动跑 ruff / ruff-format / structure-guard / gitleaks；失败请改代码，禁止 `--no-verify`。

## 4. Pull Request

PR 模板会引导填写变更动机、测试结果、自查项。

## 5. 红线

- `tts/` 下的引擎抽象层接口变更必须同步 OpenAPI 契约快照。
- `app/config.yaml` 与 `app/config.py` 的字段必须一一对应。
- 完整性清单（`backend/security/integrity_manifest.json`）变更必须重新生成 `.sig.ed25519`。
